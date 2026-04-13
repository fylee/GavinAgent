# GitHub Copilot Instructions

## Tech Stack

This project uses Django, HTMX, PostgreSQL, and uv. Suggest code consistent with these technologies only.

- **Django** — backend framework; use ORM, CBVs/FBVs, built-in auth
- **HTMX** — frontend interactivity via HTML attributes, not JavaScript frameworks
- **PostgreSQL** — primary database via Django ORM
- **uv** — package manager (`uv add`, `uv run`); never suggest `pip install`

## Package Management

```bash
# Add a dependency
uv add django psycopg[binary] django-htmx

# Run management commands
uv run python manage.py migrate
uv run python manage.py runserver
```

## Django Conventions

- **Views**: CBVs for standard CRUD, FBVs for HTMX partials
- **Models**: Business logic belongs here, not in views
- **Templates**: `<app>/templates/<app>/`, inherit from `base.html`
- **Migrations**: Always use `makemigrations` and `migrate` — no manual DB changes
- **Queries**: Use `select_related`/`prefetch_related` to prevent N+1s

## HTMX Patterns

Prefer HTMX attributes for interactivity:

```html
<!-- Partial list refresh -->
<button hx-get="/tasks/" hx-target="#task-list" hx-swap="innerHTML">
  Refresh
</button>

<!-- Inline form submission -->
<form hx-post="/tasks/create/" hx-target="#task-list" hx-swap="outerHTML">
  {% csrf_token %}
  ...
</form>
```

Partial template responses should be named `_<name>.html` (e.g., `_task_list.html`).

Return partials from views when the request is HTMX:

```python
from django.shortcuts import render

def task_list(request):
    tasks = Task.objects.all()
    template = "_task_list.html" if request.htmx else "task_list.html"
    return render(request, template, {"tasks": tasks})
```

## PostgreSQL / ORM

```python
# Good — single query with join
tasks = Task.objects.select_related("owner").filter(status="open")

# Avoid — raw SQL unless justified in a spec
from django.db import connection
cursor = connection.cursor()  # only with documented justification
```

## Skills

- All skills live in `agent/workspace/skills/<name>/SKILL.md` — this is the **source of truth**
- After editing a skill, run `uv run python manage.py sync_claude_code --skills-only` to push changes to `~/.claude/skills/`
- **Never edit `~/.claude/skills/` directly** — changes will be overwritten on the next sync

## Specs and Documentation

- Major features must have a spec in `.spec/` before implementation
- Spec files follow sequential naming: `001-feature-name.md`, `002-feature-name.md`
- Project documentation lives in `.doc/`
- Do not suggest or generate code for a major feature if no spec exists — prompt to write one first

## Spec Template (`.spec/NNN-feature-name.md`)

```markdown
# NNN — Feature Name

## Goal
What problem does this solve?

## Background
Context and motivation.

## Proposed Solution
How will it be implemented?

## Out of Scope
What is explicitly excluded?

## Open Questions
Unresolved decisions.
```

## Code Style

- PEP 8; use `ruff` for linting/formatting
- Type hints on all function signatures
- No commented-out code
- Thin views, rich models, dumb templates
- No inline JavaScript — use HTMX attributes or Alpine.js for local state only
