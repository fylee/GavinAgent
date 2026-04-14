# 025 — Skill Sync: Anthropic Spec Compliance

## Goal

Extend the `sync_claude_code` management command so that skills authored to the
Anthropic spec — including extra frontmatter fields (`tools`, `license`) and
bundled resource directories (`scripts/`, `references/`, `assets/`) — are fully
preserved when synced to `~/.claude/skills/`.

## Background

The current `_sync_skills()` implementation in
`agent/management/commands/sync_claude_code.py` has two gaps relative to the
Anthropic skill spec (as defined in `.agents/skills/skill-creator/SKILL.md`):

**1. Frontmatter stripping** — The sync rebuilds the SKILL.md frontmatter from
scratch, keeping only `name` and `description`. All other fields are silently
dropped on sync:

| Field | Anthropic spec | Current sync |
|---|---|---|
| `name` | Required | ✅ preserved |
| `description` | Required | ✅ preserved |
| `compatibility` | Optional | ❌ dropped |
| `tools` | Non-standard (Winbond) | ❌ dropped |
| `license` | Non-standard (Winbond) | ❌ dropped |
| GavinAgent `metadata.*` | GavinAgent extension | ❌ dropped |
| GavinAgent `allowed-tools` | GavinAgent extension | ❌ dropped |

**2. Bundled resources not copied** — The Anthropic spec supports three optional
subdirectories alongside SKILL.md. The sync writes only `SKILL.md` to the
destination and ignores everything else:

| Directory | Purpose | Current sync |
|---|---|---|
| `scripts/` | Executable code for deterministic tasks | ❌ not copied |
| `references/` | Docs loaded into context as needed | ❌ not copied |
| `assets/` | Templates, icons, fonts used in output | ❌ not copied |

Skills that reference bundled resources (e.g. `mcp-builder` using
`scripts/connections.py`, `cim-router` reading `references/catalogs.md`) will
have broken references at runtime because those files never reach
`~/.claude/skills/<name>/`.

This spec targets the skills sourced from `D:/source/agent/skills` (Winbond's
internal skills repo) which already follow the Anthropic directory convention,
as well as any future GavinAgent skills that adopt bundled resources.

## Proposed Solution

### 1. Preserve all frontmatter fields

Replace the current "rebuild from scratch" logic with a merge strategy:

- Parse the source frontmatter with `yaml.safe_load`
- Normalise only `name` (sanitise to filesystem-safe) and keep its value
- Write back all other fields as-is, preserving order where possible
- GavinAgent-specific fields (`metadata`, `allowed-tools`) pass through
  unchanged — they are valid Claude Code extensions

```python
# Before (current)
lines = ["---", f"name: {safe_name}"]
lines.append(f"description: {description}" if description else "description: GavinAgent skill")
lines.append("---")

# After
meta["name"] = safe_name                      # normalise name only
output_frontmatter = yaml.dump(meta, allow_unicode=True, sort_keys=False).rstrip()
lines = ["---", output_frontmatter, "---"]
```

### 2. Copy bundled resource directories

After writing `SKILL.md`, copy any `scripts/`, `references/`, and `assets/`
subdirectories from the source skill folder to the destination. Use
`shutil.copytree` with `dirs_exist_ok=True` so incremental syncs overwrite
cleanly without deleting files that weren't in the source.

```python
BUNDLED_DIRS = ("scripts", "references", "assets")

for subdir_name in BUNDLED_DIRS:
    src_sub = skill_dir / subdir_name
    if src_sub.is_dir():
        dst_sub = dest_dir / subdir_name
        shutil.copytree(src_sub, dst_sub, dirs_exist_ok=True)
```

### 3. Dry-run output

Extend `--dry-run` reporting to list which bundled directories would be copied,
so operators can verify before committing.

### Affected file

`agent/management/commands/sync_claude_code.py` — `_sync_skills()` method only.
No model changes, no migrations, no UI changes required.

### 4. New command: `import_skills`

A new management command `import_skills` copies skills from the Winbond skills
repository (`../skills/.agents/skills/` relative to the GavinAgent project root)
into GavinAgent's local workspace (`agent/workspace/skills/`), making them
available for the existing `sync_claude_code` flow.

```
python manage.py import_skills
python manage.py import_skills --source ../skills/.agents/skills/
python manage.py import_skills --only mcp-builder cim-router
python manage.py import_skills --dry-run
```

**Default source path** is resolved relative to `settings.BASE_DIR`:
`BASE_DIR / ".." / "skills" / ".agents" / "skills"` — i.e. the sibling
`skills` repo at `D:/source/agent/skills/.agents/skills/`.

**Behaviour:**

- For each skill directory found in the source, copy the entire folder
  (SKILL.md + all subdirectories) into `AGENT_WORKSPACE_DIR/skills/<name>/`
  using `shutil.copytree` with `dirs_exist_ok=True`
- Skip any source directory that has no `SKILL.md`
- `--only <name> [<name> ...]` restricts the import to the named skills
- `--dry-run` lists what would be copied without writing anything
- After a successful import (non-dry-run), automatically invoke
  `_sync_skills()` so the imported skills reach `~/.claude/skills/` in one
  step; this behaviour can be suppressed with `--no-sync`

**Full one-command workflow:**

```bash
# Import from sibling skills repo and push to ~/.claude/skills/ in one step
uv run python manage.py import_skills

# Import only specific skills
uv run python manage.py import_skills --only mcp-builder cim-router

# Preview without writing
uv run python manage.py import_skills --dry-run

# Import only, skip the Claude Code sync step
uv run python manage.py import_skills --no-sync
```

**New file:** `agent/management/commands/import_skills.py`

```python
"""
Import skills from the Winbond skills repository into GavinAgent's workspace,
then optionally sync to ~/.claude/skills/ via sync_claude_code.

Source: <BASE_DIR>/../skills/.agents/skills/   (override with --source)
Dest:   <AGENT_WORKSPACE_DIR>/skills/
"""
from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Import skills from the Winbond skills repo into GavinAgent workspace."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source",
            default=None,
            help="Path to the source skills directory (default: ../skills/.agents/skills/)",
        )
        parser.add_argument(
            "--only",
            nargs="+",
            metavar="NAME",
            help="Import only the named skill(s)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be copied without writing files",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Skip the sync_claude_code step after importing",
        )

    def handle(self, *args, **options) -> None:
        base_dir = Path(settings.BASE_DIR)
        source_dir = Path(options["source"]) if options["source"] else (
            base_dir / ".." / "skills" / ".agents" / "skills"
        ).resolve()
        dest_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
        dry_run: bool = options["dry_run"]
        only: list[str] | None = options["only"]

        if not source_dir.exists():
            self.stderr.write(f"Source not found: {source_dir}")
            return

        imported = 0
        for skill_dir in sorted(source_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            if not (skill_dir / "SKILL.md").exists():
                self.stdout.write(f"  Skip {skill_dir.name} (no SKILL.md)")
                continue
            if only and skill_dir.name not in only:
                continue

            dest = dest_dir / skill_dir.name
            if dry_run:
                subdirs = [d.name for d in skill_dir.iterdir() if d.is_dir()]
                self.stdout.write(
                    f"  Would copy: {skill_dir.name}/"
                    + (f"  [{', '.join(subdirs)}]" if subdirs else "")
                )
            else:
                shutil.copytree(skill_dir, dest, dirs_exist_ok=True)
                self.stdout.write(f"  Imported: {skill_dir.name}")
                imported += 1

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"  Imported {imported} skill(s)"))
            if not options["no_sync"]:
                from agent.management.commands.sync_claude_code import Command as SyncCmd
                sync = SyncCmd()
                sync.stdout = self.stdout
                sync.stderr = self.stderr
                sync.style = self.style
                sync.execute(skills_only=True, mcp_only=False, dry_run=False,
                             claude_dir=".claude")
```

## Out of Scope

- Syncing skills from `D:/source/agent/skills` automatically on a schedule —
  `import_skills` is always operator-triggered
- Validating or linting frontmatter fields beyond `name` and `description`
- Removing stale bundled resource files from the destination when they are
  deleted from the source (a future cleanup pass)
- Changes to how GavinAgent's embedding or routing reads skills

## Acceptance Criteria

**`sync_claude_code` improvements**
- [x] After sync, `~/.claude/skills/<name>/SKILL.md` frontmatter contains all
      fields from the source, not just `name` and `description`
- [x] After sync, `~/.claude/skills/<name>/scripts/` exists and matches source
      when the source skill has a `scripts/` directory
- [x] After sync, `~/.claude/skills/<name>/references/` exists and matches
      source when the source skill has a `references/` directory
- [x] After sync, `~/.claude/skills/<name>/assets/` exists and matches source
      when the source skill has an `assets/` directory
- [x] Skills without bundled directories sync identically to today (no
      regression)
- [x] `--dry-run` lists each bundled directory that would be copied
- [x] GavinAgent-specific frontmatter fields (`metadata.triggers`,
      `allowed-tools`) are preserved, not dropped

**`import_skills` new command**
- [x] `uv run python manage.py import_skills` copies all valid skills from
      `../skills/.agents/skills/` into `agent/workspace/skills/`
- [x] Source directories without a `SKILL.md` are skipped with a log message
- [x] `--only mcp-builder cim-router` imports only the named skills
- [x] `--dry-run` prints what would be copied without touching the filesystem
- [x] `--no-sync` skips the automatic `sync_claude_code` step
- [x] By default, `sync_claude_code --skills-only` is invoked automatically
      after import so the skills reach `~/.claude/skills/` in one command
- [x] `--source <path>` overrides the default sibling repo location
- [x] Bundled subdirectories (`scripts/`, `references/`, `assets/`) are
      included in the copy, not just `SKILL.md`

## Open Questions

1. **Stale file cleanup**: If a file is removed from `scripts/` in the source,
   the destination copy persists until manually deleted. Should the sync do a
   full `rmtree` + `copytree` instead of `dirs_exist_ok=True`? Risk: a failed
   sync mid-run could leave the destination in a broken state.

2. **`tools` field semantics**: The `tools` field lists specific MCP tool names
   (e.g. `fab-mcp/get_hold_lot_list`). Claude Code does not natively enforce
   this field. Should GavinAgent strip it to avoid confusing the Claude Code
   runtime, or pass it through as inert metadata?

3. **Conflict handling in `import_skills`**: If a skill name exists in both the
   Winbond skills repo and `agent/workspace/skills/` (e.g. a locally customised
   version), the import will overwrite it silently. Should `--force` be required
   to overwrite existing skills, or should the default be overwrite-always?

## Implementation Notes

Both commands are fully implemented and all acceptance criteria are satisfied.

**`sync_claude_code._sync_skills()`** (`agent/management/commands/sync_claude_code.py`):
- Merge strategy: `yaml.safe_load` the source frontmatter, normalise `name` only
  (`re.sub` special chars → dashes), default `description` if absent, then
  `yaml.dump` the full dict back — all other fields preserved
- Bundled dirs: iterates `["scripts", "references", "assets"]`, copies present
  ones via `shutil.copytree(src_sub, dst_sub, dirs_exist_ok=True)`
- Dry-run: appends `[scripts, references]` bracket note to each line
- BOM handling: reads SKILL.md with `encoding="utf-8-sig"`

**`import_skills`** (`agent/management/commands/import_skills.py`):
- New command; copies entire skill dirs from source → `AGENT_WORKSPACE_DIR/skills/`
  using `shutil.copytree(..., dirs_exist_ok=True)`
- Skips dirs without `SKILL.md`
- Supports `--only`, `--dry-run`, `--no-sync`, `--source`
- After import (non-dry-run, without `--no-sync`), automatically calls
  `sync_claude_code --skills-only` so skills reach `~/.claude/skills/` in one step

**Tests**: `tests/agent/test_sync_claude_code.py` — 30 test cases covering
frontmatter preservation, bundled dir copy, dry-run output, BOM handling,
`--only` filter, `--no-sync`, incremental overwrite, and error handling.
