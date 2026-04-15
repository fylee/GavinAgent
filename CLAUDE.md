# Claude Code Instructions

## Tech Stack

This project uses:
- **Django** — web framework (backend logic, ORM, admin)
- **HTMX** — frontend interactivity without full-page reloads
- **PostgreSQL** — primary database
- **uv** — Python package manager and virtual environment tool

Always use these technologies. Do not introduce alternatives (e.g., Flask, React, SQLite, pip/poetry) unless explicitly requested.

## Project Conventions

### Package Management
- Use `uv add <package>` to add dependencies — never `pip install`
- Use `uv run <command>` to execute scripts in the managed environment
- Lock file (`uv.lock`) must always be committed alongside `pyproject.toml` changes

### Django
- Follow Django's app-per-feature structure
- Use class-based views (CBVs) for standard CRUD; function-based views (FBVs) for simple or HTMX-specific endpoints
- All database access goes through the ORM — no raw SQL unless performance-critical and justified in the relevant spec
- Keep business logic out of views — use model methods or service modules
- Use Django's built-in auth system; do not roll custom auth

### HTMX
- Prefer partial template responses over full-page reloads for interactive features
- Use `HX-*` response headers for server-driven UI control (redirects, triggers, swaps)
- Keep JavaScript minimal — HTMX attributes in templates, not inline scripts
- Alpine.js is acceptable for local state that HTMX cannot handle

### PostgreSQL
- All schema changes via Django migrations — never modify the DB directly
- Name migrations descriptively when auto-generated names are ambiguous
- Use `select_related` / `prefetch_related` to avoid N+1 queries

### Templates
- Store templates in `<app>/templates/<app>/`
- Use template inheritance: a `base.html` at project level, app-level blocks
- HTMX partial templates use the `_partial.html` naming convention (e.g., `_task_list.html`)

## Specs and Documentation

### Specs (`.spec/`)
- All significant features or architectural changes require a spec **before** implementation begins
- Spec files are named sequentially: `001-<slug>.md`, `002-<slug>.md`, etc.
- A spec must include: **Goal**, **Background**, **Proposed Solution**, **Out of Scope**, and **Open Questions**
- Do not implement a major feature without a corresponding spec
- Minor bug fixes and trivial changes do not require a spec

### Test Reports (`.testreport/`)
- After running tests for a spec, save the test report to `.testreport/<NNN>-<slug>.md`
- The filename leading number **must match** the corresponding spec number exactly (e.g., spec `027-llm-call-resilience.md` → report `027-llm-call-resilience.md`)
- Sub-specs use the same decimal notation: spec `021.1-skill-authoring-compliance.md` → report `021.1-skill-authoring-compliance.md`
- Each report must include: run date/time, command used, total pass/fail counts, and a per-test result table
- Reports are updated in-place on each subsequent test run (replace, do not append)
- Do **not** store test reports inside `.spec/` files — keep spec (design) and report (execution) separate

### Docs (`.doc/`)
- Place all project documentation here (architecture decisions, runbooks, API references, onboarding guides)
- File names should be descriptive kebab-case: `database-schema.md`, `deployment.md`, etc.
- Keep docs up to date when implementing spec changes

## Skills

- All skills live in `agent/workspace/skills/<name>/SKILL.md` — this is the **source of truth**
- After editing a skill, run `uv run python manage.py sync_claude_code --skills-only` to push changes to `~/.claude/skills/`
- **Never edit `~/.claude/skills/` directly** — changes will be overwritten on the next sync

## Workflow

1. For any non-trivial change, check `.spec/` for an existing spec
2. If none exists, draft the spec first and confirm with the user before proceeding
3. Implement against the spec; note any deviations in the spec file
4. Update `.doc/` if the change affects documented behavior

## Code Style

- Follow PEP 8; use `ruff` for linting and formatting
- Type hints on all function signatures
- No commented-out code — delete it or capture reasoning in a spec/doc
- Keep views thin, models rich, templates dumb
