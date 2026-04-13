# 021 — Agent Skills Specification Compliance

## Goal

Bring all GavinAgent `SKILL.md` files into compliance with the [agentskills.io specification](https://agentskills.io/specification), so skills are portable, interoperable, and valid against the reference validator.

## Background

GavinAgent skills were designed before the agentskills.io specification was published. The current frontmatter schema uses several non-spec fields that are either renamed, relocated, or absent in the official spec:

| Current field      | Spec status        | Resolution                                    |
|--------------------|--------------------|-----------------------------------------------|
| `name`             | Required — valid   | Keep as-is (all names are valid)              |
| `description`      | Required — valid   | Keep; improve thin descriptions               |
| `version`          | Not in spec        | Move to `metadata.version`                    |
| `tools`            | Not in spec        | Rename to `allowed-tools` (spec field)        |
| `approval_required`| Not in spec        | Move to `metadata.approval_required`          |
| `triggers`         | Not in spec        | Move to `metadata.triggers`                   |
| `trigger_patterns` | Not in spec        | Move to `metadata.trigger_patterns`           |
| `examples`         | Not in spec        | Move to `metadata.examples` or body section   |

The spec defines only these frontmatter keys: `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`.  Any other top-level key is non-conformant and may be rejected or ignored by third-party agents or the `skills-ref` validator.

GavinAgent's routing system (embedding-based skill selection) reads `triggers`, `trigger_patterns`, and `examples` at skill-load time. These GavinAgent-specific fields must be preserved — but relocated inside `metadata` where the spec allows arbitrary key-value pairs.

## Proposed Solution

### Schema changes per skill

**Before (non-conformant):**
```yaml
---
name: weather
description: Get current weather…
approval_required: false
tools: [run_skill]
examples:
  - "what's the weather in Taipei?"
---
```

**After (spec-conformant):**
```yaml
---
name: weather
description: Get current weather and today's forecast for one or more cities. Returns temperature, conditions, wind, humidity, and UV index. Use this skill instead of web_read for all weather queries.
allowed-tools: Bash
compatibility: Requires internet access to Open-Meteo API
metadata:
  approval_required: "false"
  examples: "what's the weather in Taipei? | current temperature in Tokyo | weather forecast for New York"
---
```

### Frontmatter mapping rules

1. **`tools: [run_skill]`** → `allowed-tools: Bash` (or the actual tools needed, space-separated string per spec)
2. **`version: N`** → `metadata.version: "N"` (metadata values must be strings)
3. **`approval_required: false`** → `metadata.approval_required: "false"`
4. **`triggers: [...]`** → `metadata.triggers: "word1 | word2 | word3"` (pipe-separated string, since metadata values must be strings)
5. **`trigger_patterns: [...]`** → `metadata.trigger_patterns: "pattern1 | pattern2"` (pipe-separated string)
6. **`examples: [...]`** → either `metadata.examples: "ex1 | ex2"` (if short enough) or move to a `## Examples` section in the body

### Description quality improvements

Several descriptions are too thin for routing (spec recommends keywords that help agents identify relevant tasks):

| Skill              | Current description                                          | Action needed |
|--------------------|--------------------------------------------------------------|---------------|
| `data-analysis`    | "Processing tabular data, statistics, and comparisons"       | Expand to include task triggers (analyse, compute, rank, etc.) |
| `charts`           | "Generating charts and data visualisations"                  | Expand to include chart types and when to prefer charts over tables |
| `web-research`     | "Searching the web, fetching URLs, and finding current information" | Minor — already describes when to use |
| `workflow-management` | Good — already has "when the user wants something done at a specific time…" | No change needed |
| `weather`          | Good trigger phrase at end                                   | Minor wording cleanup |
| `stock-chart`      | Good — describes both what and when                          | Minor cleanup after migrating `tools` |
| `edwm-wip-movement`| Adequately specific                                          | No change needed |

### Skill-by-skill changes

#### `weather`
- Remove: `approval_required`, `tools`
- Add: `allowed-tools: Bash`
- Add: `compatibility: Requires internet access to Open-Meteo API`
- Move: `examples` → `metadata.examples` (pipe-separated)

#### `stock-chart`
- Remove: `approval_required`, `tools`, `triggers`, `examples`
- Add: `allowed-tools: Bash`
- Add: `compatibility: Requires internet access to Yahoo Finance`
- Move: `triggers` → `metadata.triggers`
- Move: `examples` → `metadata.examples`

#### `edwm-wip-movement`
- Remove: `triggers`, `examples`, `version`
- Move: `triggers` → `metadata.triggers`
- Move: `examples` → `metadata.examples`
- Move: `version` → `metadata.version`
- Add: `compatibility: Requires EDWM MCP server (SSE transport)`

#### `data-analysis`
- Remove: `triggers`, `version`
- Improve: `description` — expand to include keywords
- Move: `triggers` → `metadata.triggers`
- Move: `version` → `metadata.version`

#### `charts`
- Remove: `triggers`, `tools`, `version`
- Improve: `description` — expand to include chart types and when to use
- Move: `triggers` → `metadata.triggers`
- Move: `version` → `metadata.version`
- Add: `allowed-tools: Bash` (if `chart` tool needs pre-approval)

#### `web-research`
- Remove: `triggers`, `version`
- Move: `triggers` → `metadata.triggers`
- Move: `version` → `metadata.version`

#### `workflow-management`
- Remove: `examples`, `triggers`, `trigger_patterns`, `version`
- Move: all to `metadata`

### GavinAgent routing adapter

The embedding-based skill router (`agent/skills/embeddings.py`) currently reads `triggers` and `trigger_patterns` as top-level frontmatter keys.  After this migration it must read them from `metadata`:

```python
# Before
triggers = frontmatter.get("triggers", [])

# After
metadata = frontmatter.get("metadata", {})
triggers_raw = metadata.get("triggers", "")
triggers = [t.strip() for t in triggers_raw.split("|")] if triggers_raw else []
```

Similarly for `trigger_patterns` and `examples`.

### Validation

After migration, run the `skills-ref` validator on each skill:
```bash
skills-ref validate agent/workspace/skills/<name>
```

All skills must pass without errors before the spec is considered implemented.

## Routing Architecture Decision

This section establishes the routing model for GavinAgent going forward. The decision here is a prerequisite for **Spec 022** (implementation) and **Spec 021-1** (skill authoring guidance update).

### Two candidate models

**Model A — Embedding-only (current)**
- `embeddings.py` embeds `name + description + triggers + examples` into pgvector
- Query is embedded at runtime; cosine similarity selects skills above threshold 0.55
- LLM receives pre-selected skill content; does not participate in routing decision
- `triggers` are essential signal; `description` quality is secondary

**Model B — Model-driven catalog (agentskills.io standard)**
- All skill `name + description` pairs are injected into the system prompt as a catalog (~50–100 tokens/skill)
- LLM reads the catalog and decides which skill to activate
- No embedding search; no threshold
- `description` quality is the sole routing signal; `triggers` do not exist

### Decision: Hybrid (A + B)

Neither pure model is ideal for GavinAgent at this stage:

- Pure A: Non-compliant spirit; LLM has no visibility into available skills; `description` quality doesn't matter
- Pure B: Loses the precision of embedding routing; large skill sets inflate every system prompt; requires all descriptions to be excellent before cutover

**Recommended hybrid**:

1. **Keep embedding routing (Model A)** as the primary selector — proven, efficient, handles large skill sets
2. **Add a skill catalog to the system prompt (Model B)** — a compact list of `name: description` pairs so the LLM knows what skills exist and can self-activate via slash command or explicit mention
3. **Migrate `triggers` to `metadata`** (per 021 frontmatter rules) — `embeddings.py` reads from `metadata.triggers`
4. **Improve `description` quality** — descriptions must serve both routing systems simultaneously

This hybrid makes GavinAgent spec-compliant in format, gives the LLM skill visibility, and preserves embedding routing precision. Full migration to pure Model B is deferred to a future spec once description quality has been validated.

**Spec 022** implements the hybrid: catalog injection into the system prompt + `embeddings.py` metadata adapter. Spec 021 only decides the architecture; it does not implement it.

## Downstream Dependencies

| Spec | Depends on 021 for |
|---|---|
| **021-1** | Frontmatter schema (so AGENTS.md authoring guide uses correct field locations) and routing decision (so description quality guidance is correct) |
| **022** | Routing architecture decision (hybrid model above) |

Development order: **021 → 021-1 → 022**

## Out of Scope

- Changing skill body content or instructions
- Adding `license` field (no license to apply; proprietary internal skills)
- Publishing skills to the agentskills.io registry
- Modifying `handler.py` files for `weather` or `stock-chart`
- Changing the sync mechanism (`sync_claude_code` management command)
- Full migration to pure Model B routing (deferred)

## Acceptance Criteria

- [ ] All `SKILL.md` files have only spec-defined top-level frontmatter keys: `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`
- [ ] `name` in each frontmatter matches its parent directory name
- [ ] All descriptions are ≥ 50 characters and describe both what the skill does and when to use it
- [ ] `version`, `triggers`, `trigger_patterns`, `examples`, `approval_required` are inside `metadata` (not top-level)
- [ ] GavinAgent skill router reads `triggers`, `trigger_patterns`, `examples` from `metadata` without regression
- [ ] `skills-ref validate` passes on all skills (or the validator confirms compliance)
- [ ] Skill routing behaviour is unchanged — same skills are selected for the same user queries as before migration

## Open Questions

1. **`metadata` value types**: The spec says metadata is "a map from string keys to string values". GavinAgent's `triggers` are currently lists. Pipe-separated strings are proposed — should we use JSON-encoded strings instead for easier parsing? (e.g. `triggers: '["wip","movement"]'`)

2. **`allowed-tools`**: The `weather` and `stock-chart` skills use a `run_skill` internal tool that is not a standard Claude tool. Should `allowed-tools` reflect the real underlying tools (`Bash`) or be omitted since the experimental flag has variable support?

3. **Sync timing**: Should the router adapter change (`embeddings.py`) and the SKILL.md migrations be committed atomically (one PR), or should the adapter change ship first to ensure backwards compatibility during migration?

## Implementation Notes

<!-- Filled in during or after implementation. -->
