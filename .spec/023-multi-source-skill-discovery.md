# 023 — Multi-Source Skill Discovery and On-Demand Embedding

## Goal

Allow GavinAgent to discover and embed skills from multiple directories (including `.agents/skills/` and `~/.agents/skills/` installed via `npx skills add`), fix the `approval_required` bug introduced by Spec 021's metadata migration, provide a Django management command to embed unembedded skills, and expose an "Embed" action in the Skills UI.

## Background

Running `npx skills add composiohq/skills` installs skills to `.agents/skills/<name>/` (project-level) or `~/.agents/skills/<name>/` (user-level). GavinAgent currently only scans one hardcoded path (`agent/workspace/skills/`), making externally installed skills completely invisible.

Four specific gaps were identified:

### Gap 1 — Wrong scan directory (fatal)

`_skills_dir()` in `embeddings.py`, `loader.py`, and `nodes.py` all return a single hardcoded path. There is no mechanism to scan `.agents/skills/`, `~/.agents/skills/`, or `~/.claude/skills/`.

### Gap 2 — No embed trigger for newly added skills

`embed_all_skills()` only runs at Django startup (background thread in `apps.py`). Skills added after startup are never embedded until the server restarts. There is no management command or UI action to trigger embedding on demand.

### Gap 3 — `approval_required` bug in `loader.py` (Spec 021 regression)

```python
# loader.py:58 — reads from top-level only
approval_required=meta.get("approval_required", False)
```

Spec 021 migrated `approval_required` into `metadata.approval_required`. This line now always returns `False` for any skill that completed the 021 migration, silently breaking the approval gate.

### Gap 4 — Composio `rules/` sub-files not followed

Composio's `SKILL.md` body references `rules/*.md` files via relative Markdown links. GavinAgent's loader reads only the `SKILL.md` body — referenced files are not loaded, so the agent operates with incomplete skill instructions.

## Proposed Solution

### 1. Configurable multi-directory skill scan

Add `AGENT_EXTRA_SKILLS_DIRS` setting and a `all_skill_dirs()` helper in a new `agent/skills/discovery.py` module that aggregates all skill source directories in precedence order:

```python
# Precedence (highest → lowest):
# 1. agent/workspace/skills/            ← GavinAgent native (always first)
# 2. .agents/skills/                    ← project-level (npx skills add)
# 3. ~/.agents/skills/                  ← user-level (npx skills add -g)
# 4. ~/.claude/skills/                  ← Claude Code compat
# 5. AGENT_EXTRA_SKILLS_DIRS entries    ← user-configured extras
```

Name collision rule (consistent with agentskills.io spec): **higher precedence directory wins**. Log a warning when a skill name is shadowed by a lower-precedence directory.

Settings additions in `config/settings/base.py`:

```python
# Extra skill directories scanned in addition to agent/workspace/skills/.
# Format: comma-separated absolute paths.
AGENT_EXTRA_SKILLS_DIRS = config("AGENT_EXTRA_SKILLS_DIRS", default="", cast=Csv())

# Whether to auto-scan standard agentskills.io directories
# (.agents/skills/, ~/.agents/skills/, ~/.claude/skills/)
AGENT_SCAN_STANDARD_SKILL_DIRS = config(
    "AGENT_SCAN_STANDARD_SKILL_DIRS", default=True, cast=bool
)
```

`all_skill_dirs()` is the single source of truth used by `embeddings.py`, `loader.py`, and `nodes.py`, replacing all three copies of `_skills_dir()`. Standard dirs that do not exist on disk are silently skipped.

#### Startup auto-embed scope

The background thread in `apps.py` continues to scan **only `agent/workspace/skills/`** at startup. Extra directories are only embedded when explicitly triggered via the management command or UI. This avoids startup latency from large external skill collections and requires the operator to consciously embed external skills after installing them.

### 2. `rules/` content: decouple routing from execution

Composio's `rules/` directory contains 14+ files totalling 150+ KB — far beyond `EMBED_TEXT_MAX_CHARS = 500`. Appending all rules to the embed text would pollute the routing signal with execution detail.

**Design principle: embedding serves routing; body injection serves execution. Keep them separate.**

**For embedding (routing signal):** use only `SKILL.md` frontmatter fields — `description`, `metadata.triggers`, `metadata.examples` — plus the first 500 chars of the `SKILL.md` body. Do **not** include `rules/` content in the embedded text. This keeps the cosine similarity search focused on skill identity, not implementation detail.

**For body injection (LLM execution):** when a skill is matched and its body is injected into the system prompt, append the full content of `rules/*.md` files (sorted alphabetically) after the `SKILL.md` body. The LLM receives complete instructions at execution time without affecting routing quality.

Only one level deep — do not recurse into `rules/rules/`.

### 3. Fix `approval_required` in `loader.py`

```python
# After fix — reads from metadata with top-level fallback
nested_meta = meta.get("metadata", {}) or {}
approval_required = (
    nested_meta.get("approval_required")
    or meta.get("approval_required")
    or False
)
# Normalise string "true"/"false" (metadata values are strings per spec)
if isinstance(approval_required, str):
    approval_required = approval_required.lower() == "true"
```

### 4. Django management command: `embed_skills`

New command at `agent/management/commands/embed_skills.py`:

```bash
python manage.py embed_skills                  # embed new/changed skills only
python manage.py embed_skills --force          # re-embed all regardless of hash
python manage.py embed_skills --skill weather  # embed one skill only
python manage.py embed_skills --dry-run        # print what would be embedded
```

Behaviour:
- Scans all directories from `all_skill_dirs()`, respecting trust status (see § 5)
- Embeds skills whose content hash has changed or that have no `SkillEmbedding` record
- Prints per-skill result and a summary: `embedded N, skipped M unchanged, skipped K untrusted`
- `--force` bypasses hash check and re-embeds everything
- `--skill <name>` scopes to a single named skill
- `--dry-run` prints decisions without writing to DB

### 5. Trust boundary for external skill directories

Skills from `.agents/skills/` originate from the project repository, which may be a freshly cloned, untrusted external repo. Per the agentskills.io spec's trust recommendation, GavinAgent should not silently embed external skill content without operator confirmation.

#### Trust model

Two tiers:

| Source | Trust status |
|---|---|
| `agent/workspace/skills/` | Always trusted — operator owns this directory |
| `AGENT_EXTRA_SKILLS_DIRS` entries | Always trusted — operator explicitly configured them |
| Standard dirs (`.agents/`, `~/.agents/`, `~/.claude/`) | **Requires confirmation** — first-time only |

#### `TrustedSkillSource` model

New Django model in `agent/models.py`:

```python
class TrustedSkillSource(TimeStampedModel):
    path = models.CharField(max_length=500, unique=True)
    approved_by = models.CharField(max_length=100, blank=True)  # username or "system"
    note = models.TextField(blank=True)
```

A directory is trusted if it has a `TrustedSkillSource` record with its resolved absolute path.

#### Trust flow

1. `all_skill_dirs()` marks each directory as `trusted` or `pending`
2. `embed_all_skills()` and `embed_skills` management command skip `pending` directories with a warning: `"Skipping untrusted skill source: .agents/skills/ — approve it in the Skills UI first"`
3. Skills from `pending` directories are **discovered and shown** in the Skills UI (so the operator can see what would be installed), but their embedding status badge shows `untrusted` instead of `embedded` / `not embedded`
4. The Skills UI shows an **"Approve Source"** button next to any skill from an untrusted directory. Clicking it creates a `TrustedSkillSource` record and immediately triggers embedding for that directory's skills

#### Discovery without trust

Skills from untrusted directories are loaded into the registry (so they appear in the UI) but are **excluded from**:
- `build_skill_catalog()` — not shown to the LLM in the system prompt
- `find_relevant_skills()` — not returned by embedding search
- `_build_skills_section()` — body never injected into context

This ensures untrusted skills are visible to the operator for review but have zero effect on agent behaviour until explicitly approved.

### 6. Skill UI: "Embed" action

The Skills list page (`/agent/skills/`) needs these additions:

**Embedding status badge** — each skill row shows a badge:
- `embedded` — has a `SkillEmbedding` record
- `not embedded` — no record, will not be found by similarity search
- `untrusted` — source directory not yet approved

**Per-skill "Embed" button** — posts to `POST /agent/skills/<name>/embed/`:
- Triggers embedding for that skill only
- Returns HTMX partial updating the row badge
- Disabled (greyed out) if source is `untrusted`

**Per-skill "Approve Source" button** — shown only for `untrusted` skills, posts to `POST /agent/skills/<name>/approve-source/`:
- Creates `TrustedSkillSource` for the skill's parent directory
- Triggers embedding for all skills from that directory
- Returns HTMX partial updating all affected rows

**Global "Re-embed All" button** — in the page header, posts to `POST /agent/skills/embed-all/`:
- Runs full `embed_all_skills()` across all trusted directories
- Returns toast notification: `"Re-embedded N skill(s)"`

`SkillsView` context must include:
- Per-skill embedding status (join against `SkillEmbedding`)
- Per-skill trust status (join against `TrustedSkillSource` via skill's source directory)

## Out of Scope

- Implementing `activate_skill` tool for model-driven routing (Spec 022 / future)
- Publishing or pushing skills to the agentskills.io registry
- Following reference chains deeper than one level (`rules/rules/` not supported)
- Per-skill content sandboxing (trust is per-directory, not per-file)
- UI for browsing or searching the agentskills.io registry

## Acceptance Criteria

- [ ] `all_skill_dirs()` returns native dir + standard dirs + `AGENT_EXTRA_SKILLS_DIRS`, in precedence order; non-existent dirs are silently skipped
- [ ] Skills installed via `npx skills add composiohq/skills` appear in the Skills UI after discovery
- [ ] Skills from `.agents/skills/` are discovered but shown as `untrusted` until approved
- [ ] Approving a source creates a `TrustedSkillSource` record and triggers embedding immediately
- [ ] `rules/*.md` content is appended to skill body at **injection time** (not embed time), preserving routing signal quality
- [ ] `loader.py` reads `approval_required` from `metadata.approval_required` with top-level fallback; `"true"`/`"false"` strings normalised to bool
- [ ] `python manage.py embed_skills` embeds only changed/new trusted skills; `--force` re-embeds all; `--dry-run` prints without writing
- [ ] Skills UI shows `embedded` / `not embedded` / `untrusted` badge per skill
- [ ] Per-skill "Embed" button triggers embedding and updates badge via HTMX
- [ ] "Approve Source" button trusts a directory and triggers embedding for all its skills
- [ ] "Re-embed All" button runs full re-embed across trusted dirs and shows count toast
- [ ] Startup auto-embed only covers `agent/workspace/skills/` (no latency regression)
- [ ] Name collision between directories resolved by precedence with a logged warning

## Implementation Notes

<!-- Filled in during or after implementation. -->
