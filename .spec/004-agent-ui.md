# 004 — Agent Management UI

**Status:** Draft
**Created:** 2026-03-19

---

## Goal

Replace the Django admin dependency for day-to-day agent operations with a dedicated
management UI inside the `agent` app. All significant agent configuration — agents,
tools, memory, and skills — should be manageable without touching `/admin/`.

---

## Background

Spec 003 built the agent system and created read-only views for the dashboard, logs,
memory, tools, and skills. However:

- **Agent CRUD** (create, edit, delete, set default) requires Django admin today
- **Tools page** only lists tools; no enable/disable per agent, no approval-policy override
- **Memory page** is a raw textarea; no paragraph-level visibility or delete
- **Skills page** only lists loaded skills; no install from UI, no enable/disable
- **Tool approval** endpoint exists but no UI card renders in the conversation or agent dashboard

This spec fills all those gaps and adds monitoring.

---

## App Responsibility

Views for sections 1–5 and 7–8 live inside the `agent` app.

Section 6 (Tool Approval inline in chat) **does** require one targeted change to
`chat/views.py` (`MessageStreamView`) — this is the only exception. All other `chat`
app files are unchanged.

---

## Dashboard Scope After This Spec

The existing `/agent/` dashboard retains its current role: active runs, last heartbeat,
default agent status, and quick links. It is **not** simplified — it is the first-stop
quick-action surface. The new Monitoring page (`/agent/monitoring/`) goes deeper on
health, cost, and history and is the second-stop operational view.

---

## Proposed Solution

### 1. Agent Management (`/agent/agents/`)

Full CRUD for `Agent` records.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/agent/agents/` | Agent list |
| `GET` | `/agent/agents/create/` | Create agent form |
| `POST` | `/agent/agents/create/` | Save new agent |
| `GET` | `/agent/agents/<uuid>/` | Edit agent form |
| `POST` | `/agent/agents/<uuid>/` | Save edits |
| `GET` | `/agent/agents/<uuid>/delete/` | Delete confirmation page |
| `POST` | `/agent/agents/<uuid>/delete/` | Execute delete |
| `POST` | `/agent/agents/<uuid>/set-default/` | Mark as default (HTMX) |

#### Agent list

- Table: name, model, is_active badge, is_default badge, run count, created date
- Per-row actions: Edit, Set default (hidden if already default), Delete
- "New agent" button top-right

#### Agent form (create & edit)

Fields:

| Field | Type | Notes |
|---|---|---|
| Name | text | Required, unique |
| Description | textarea | Optional |
| Model | select | `settings.AVAILABLE_MODELS` |
| System prompt | large textarea | Required |
| Is active | checkbox | |
| Is default | checkbox | `save()` override clears others |
| Tools | multi-checkbox | All registered built-in tool names + enabled skill names |

Validation errors are shown inline beneath each field (Django form errors rendered
in the template). Form resubmits to the same URL on error.

`Agent.metadata` is not exposed as a raw field in the form — it is written only by
the tool policy override mechanism (section 2).

#### Delete agent

- Confirmation page shows: agent name, run count (total), count of active runs
- **Block delete** if agent has `PENDING` or `RUNNING` runs — show error, no action
- **Soft delete** if agent has any historical runs: set `is_active=False`, cancel any
  `PENDING` or `WAITING` runs (set to `FAILED`, error="Agent deactivated")
- **Hard delete** only if agent has zero runs ever
- If deleted agent was `is_default`, show warning that no default agent is now set

#### Set-default HTMX

- Returns a `_agent_row.html` partial that re-renders the entire agent list row
  (badge update + button state change). Target: the `<tr>` for the affected agent.
- Also re-renders the previously-default agent row to remove its badge.

---

### 2. Tool Management (`/agent/tools/`)

Extends the existing read-only tools page. The existing
`/agent/tools/<name>/toggle/` endpoint (already implemented in spec 003) is reused.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/agent/tools/` | Tool list (agent-scoped) |
| `POST` | `/agent/tools/<name>/toggle/` | Enable/disable for selected agent *(existing)* |
| `POST` | `/agent/tools/<name>/policy/` | Override approval policy for selected agent |

#### Layout

- **Agent selector** at top (if >1 agent exists): `<select>` with
  `hx-get="/agent/tools/?agent=<uuid>"` `hx-target="#tool-list"` — reloads tool list
  for the selected agent without full page reload. Default agent pre-selected.
- **Pending approvals panel** at the very top of the page (above tool list): shows all
  `ToolExecution(status=pending)` across all runs. Disappears when empty. Each card:
  tool name, input args (formatted JSON), run link, timestamp, Approve/Reject buttons.
  This is the canonical place for pending approvals (section 7.4 shows full history).
- **Tool list** (`id="tool-list"`): one row per tool.

#### Per-tool row

| Element | Detail |
|---|---|
| Enable checkbox | Checked = in `agent.tools`; HTMX toggle saves immediately |
| Tool name | `file_read`, `shell`, etc. |
| Default policy badge | From `BaseTool.approval_policy` |
| Policy override dropdown | `— default —` / `Auto` / `Requires approval`; saves to `Agent.metadata["tool_policies"][name]` via `POST /agent/tools/<name>/policy/` |

#### Policy override request format

```
POST /agent/tools/<name>/policy/
Body: agent=<uuid>&policy=auto   # or policy=requires_approval or policy=default
```

`policy=default` removes the key from `Agent.metadata["tool_policies"]`, restoring
the built-in default.

---

### 3. Memory Management (`/agent/memory/`)

Extends the existing raw-textarea memory page.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/agent/memory/` | Memory page *(existing)* |
| `POST` | `/agent/memory/` | Save full textarea *(existing)* |
| `POST` | `/agent/memory/reembed/` | Trigger reembed *(existing)* |
| `GET` | `/agent/memory/search/` | Search memories (HTMX partial) |
| `POST` | `/agent/memory/paragraph/delete/` | Delete one paragraph + reembed |
| `POST` | `/agent/memory/paragraph/edit/` | Edit one paragraph + reembed |

#### View toggle

Alpine.js `x-data="{ view: 'raw' }"` toggles between:
- **Raw** — existing full-file textarea
- **Paragraph** — one card per paragraph (double-newline separated)

#### Paragraph card

- Content shown as text; click to make editable (Alpine `x-show`/`contenteditable`)
- **Save edit**: `POST /agent/memory/paragraph/edit/` with `hash=<sha256>&content=<new>`
  — replaces the paragraph in MEMORY.md by hash, triggers reembed. Returns updated card.
- **Delete**: `POST /agent/memory/paragraph/delete/` with `hash=<sha256>` — removes
  paragraph by hash from MEMORY.md, triggers reembed. Returns empty string (HTMX removes
  the card via `hx-swap="outerHTML"`).
- Paragraph identifier: **SHA-256 hash of paragraph content** (consistent with
  `long_term.py` existing hash approach).
- "Add paragraph" button at bottom: appends a blank editable card; on save, appends to
  the file and reembeds.

#### Embed status bar

Shows above the view toggle:
- Count of `Memory` records in the vector store
- Last reembed timestamp — read from a **file sentinel**:
  `agent/workspace/memory/.reembed_at` (contains ISO timestamp, written by
  `full_reembed()` on completion). Chosen over `Agent.metadata` because memory is
  globally scoped and not tied to any single agent.
- Warning badge "Out of sync" if `MEMORY.md` mtime > sentinel mtime

#### Memory search

- Text input at top: `hx-get="/agent/memory/search/"` `hx-trigger="input changed delay:400ms"`
- Returns `_memory_search_results.html` partial: top-5 paragraphs by cosine similarity,
  highlighted with a yellow left border in the paragraph view.

---

### 4. Skills Management (`/agent/skills/`)

Extends the existing read-only skills list.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/agent/skills/` | Skills list *(existing)* |
| `POST` | `/agent/skills/install/` | Reload from workspace *(existing)* |
| `POST` | `/agent/skills/<name>/toggle/` | Enable/disable |
| `GET` | `/agent/skills/<name>/delete/` | Delete confirmation |
| `POST` | `/agent/skills/<name>/delete/` | Execute delete |

#### Per-skill row

- Name, description, approval policy badge, handler badge (has handler.py / prompt-only)
- **Enable/disable toggle**: `POST /agent/skills/<name>/toggle/` — sets `Skill.enabled`
  in DB; skill loader checks this before adding to LLM tool schema. Returns updated row.
- **Expandable instructions**: Alpine `x-show` toggle revealing the full `instructions`
  content from `SKILL.md`.
- **Delete button**: links to confirmation page.

#### Delete skill

- Confirmation page: skill name, description, warning that files are NOT deleted from disk
- On confirm: removes from in-memory `SkillRegistry`, deletes `Skill` DB record
- Files remain on disk — user cleans up `agent/workspace/skills/<name>/` manually

#### Install / reload

- The existing "Reload skills" button re-scans `workspace/skills/` and syncs to DB.
- No UI for install-from-path (dropped — security risk of arbitrary path input
  outweighs the benefit; user copies the directory manually then reloads).

#### Multi-worker note

Skill registry is in-memory per worker process. Reloading via the UI updates only the
worker handling that request. A full restart or Celery task broadcast is required for
all workers to see the change. This limitation is noted in the UI with a warning banner.

---

### 5. Runs (`/agent/runs/`)

Restyle the existing run list and run detail pages from the old Bootstrap `base.html`
to `base_agent.html` (dark theme, consistent nav).

#### Run list (`/agent/runs/`)

- Filter bar: `?status=<value>&source=<value>` URL params; HTMX `hx-push-url="true"`
  so the filtered URL is bookmarkable. Filter bar uses `hx-get="/agent/runs/"` on
  `<select>` change.
- Table columns: agent name | trigger source | status badge | input preview | created date | link
- Pagination: 25 per page via Django `Paginator`; `?page=N` URL param; HTMX for
  page navigation (`hx-push-url="true"`).
- "New run" form: collapsed by default, Alpine `x-show` toggle. Agent select + input
  textarea + submit. Shows link to `/agent/agents/create/` when no agents exist.

#### Run detail (`/agent/runs/<uuid>/`)

Two-column layout:

**Left column — metadata + output**
- Agent name (link to edit), trigger source, status badge, timestamps
- Cancel button if `pending` or `running`
- Output section: `AgentRun.output` rendered as markdown (using `marked.js`) once
  `status=completed`; error message if `status=failed`

**Right column — tool execution trace**
- Ordered list of all `ToolExecution` records for this run (`created_at ASC`)
- Each row: tool name | status badge | duration | expandable input JSON |
  expandable output JSON | approval timestamp (if applicable)
- Approve / Reject buttons inline for `ToolExecution(status=pending)` on a `waiting`
  run — calls existing `/agent/approve/<id>/` endpoint
- If multiple pending tool executions exist, show all (oldest first)

**Polling**
- If run is `pending`, `running`, or `waiting`: the right column polls
  `GET /agent/runs/<uuid>/status/` every 2s via HTMX (`hx-swap="innerHTML"`)
- The `_run_status.html` partial is extended to include the tool execution trace;
  renamed to `_run_detail.html` to reflect its expanded scope. The old partial name
  is kept as an alias for backwards compatibility.

---

### 6. Tool Approval — Inline in Chat

When the agent is waiting for tool approval, an approval card appears in the
conversation message stream.

#### Change to `chat/views.py`

`MessageStreamView.get()` is extended: after checking for an assistant `Message`, it
also checks for a `ToolExecution(status=pending)` on any `AgentRun` linked to the
conversation with `status=waiting`. If found, it returns `_tool_approval_card.html`
instead of the typing indicator.

If multiple pending tool executions exist for the run, the card shows the **oldest
one** (the others remain pending and will be shown after this one is resolved).

#### Partial template: `chat/_tool_approval_card.html`

- Yellow left border, wrench icon — visually distinct from chat bubbles
- Shows `tool_execution.tool_name` and `tool_execution.input` as formatted JSON
- Two buttons: **Approve** (green) and **Reject** (red)
  - Both POST to `/agent/approve/<id>/` with `action=approve` or `action=reject`
  - HTMX target: `hx-swap="outerHTML"` on the card itself
- After decision: card replaced with a one-line summary bubble
  (e.g. "Shell command approved · 14:32") using the existing `_message.html` style
- After card is resolved, the typing indicator resumes automatically on the next
  polling cycle (the `MessageStreamView` logic falls through to the indicator)

---

### 7. Monitoring (`/agent/monitoring/`)

A dedicated operational overview page.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/agent/monitoring/` | Full monitoring page |
| `GET` | `/agent/monitoring/health/` | Health check partial (HTMX, cached 10s) |

#### Layout

Two-column, auto-refreshes every 30s (`hx-trigger="every 30s"` on the outer container):
- **Right column (1/3):** System health + Cost summary
- **Left column (2/3):** Recent activity feed + Approval history

---

#### 7.1 System Health

Status grid at top of right column. Results cached in Redis at key
`agent:monitoring:health` for 10s. Returned as `_health_status.html` partial.

| Service | Green | Yellow | Red |
|---|---|---|---|
| Default agent | is_default + is_active agent exists | — | None configured |
| Celery worker | ping response < 2s | — | No response (timeout=2s) |
| Redis | `ping()` succeeds | — | Unreachable |
| Database | `ensure_connection()` succeeds | — | Exception raised |
| Last heartbeat | Within `2×AGENT_HEARTBEAT_INTERVAL_MINUTES` of now | Between `2×` and `4×` interval | Overdue beyond `4×` or last status=error |

Global status banner: **All systems operational** / **Degraded** / **Critical**
(worst-case across all services).

Celery ping uses `app.control.inspect(timeout=2).ping()` — enforced via the `timeout`
kwarg, not `asyncio`.

---

#### 7.2 Cost

**`LLMUsage` model** — see New Models section.

`core/llm.py`'s `get_completion()` gains optional kwargs `source="chat"`,
`run=None`, `conversation=None`. These default to `None`/`"unknown"` so existing
callers are not broken. After a successful completion, a `LLMUsage` record is written
synchronously. `litellm.completion_cost()` exceptions are caught; cost defaults to 0.
Timezone for aggregation: UTC (Django `USE_TZ=True`).

Callers that should pass context:
- `agent/graph/nodes.py` `call_llm()` → `source="agent"`, `run=<AgentRun>`
- `chat/services.py` `reply()` → `source="chat"`, `conversation=<Conversation>`

**Cost cards (right column, below health)**

- **Today** — total tokens + estimated USD (UTC midnight boundary)
- **This month** — rolling 30-day totals
- **By model** table — model | calls | tokens | estimated USD
- **Agent vs Chat** split — two numbers side by side

---

#### 7.3 Recent Activity

Chronological feed, left column, last 50 events. Implemented as a Python-level merge
of N querysets (not SQL UNION) sorted by `created_at DESC`, capped at 50 records.
Each fetch: at most 50 rows per source. Acceptable at this scale.

| Event type | Source model | Icon | Detail |
|---|---|---|---|
| Run started | `AgentRun` (any status, ordered by created_at) | ▶ | Agent name, trigger, input preview |
| Run completed | `AgentRun(status=completed)` | ✓ | Duration (finished_at − started_at), output preview |
| Run failed | `AgentRun(status=failed)` | ✗ | Error message |
| Tool executed | `ToolExecution` | 🔧 | Tool name, status, duration |
| Heartbeat | `HeartbeatLog` | ♥ | Status, actions count |
| Memory reembedded | `ReembedLog` (new — see New Models) | 🧠 | Paragraph count |
| Chat message | `chat.Message(role=user)` | 💬 | Content preview |

Filter toggle (Alpine): **All** / **Agent only** (hides chat messages).

Relative timestamps use Django's `{{ value|timesince }}` template filter.
Clicking a run event navigates to `/agent/runs/<uuid>/`.

---

#### 7.4 Approval History

Full history of all `ToolExecution(requires_approval=True)`, left column below
activity feed. Distinct from the Tools page pending panel (which shows only pending).

Columns: tool name | decision badge | agent | run link | input preview | decided at

Filter tabs (HTMX, `?approval_status=pending`): **All** | **Approved** | **Rejected** | **Pending**

Pending rows: yellow background, inline Approve/Reject buttons (calls
`/agent/approve/<id>/`, HTMX `hx-swap="outerHTML"` on the row).

Pagination: 25 per page, `?approval_page=N`.

---

### 8. Workspace File Editor (`/agent/workspace/`)

Provides a UI to edit the workspace persona files that drive agent behaviour.
These are distinct from `Agent.system_prompt` (DB field) — AGENTS.md and SOUL.md
are loaded fresh on every agent loop run and override/supplement the DB prompt.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/agent/workspace/` | Workspace file list |
| `GET/POST` | `/agent/workspace/<filename>/` | Edit a workspace file |

#### File list

Shows the three editable files: `AGENTS.md`, `SOUL.md`, `HEARTBEAT.md`.
Each row: filename, last modified time, size, Edit button.
`MEMORY.md` is excluded — managed via `/agent/memory/`.

#### File editor

- Large `<textarea>` with the file content, monospace font
- Save button: writes file to disk immediately
- "Restore example" button: overwrites with the `.example` file content (with
  confirmation prompt via Alpine)
- No live preview — raw markdown editing only

---

## Navigation

Updated `base_agent.html` nav (full ordered set):

```
Dashboard | Agents | Runs | Monitoring | Logs | Memory | Tools | Skills | Workspace
```

`hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'` added to `<body>` in
`base_agent.html` — currently missing, required for all HTMX POST/DELETE requests.

---

## New Models

### `LLMUsage`

```python
class LLMUsage(TimeStampedModel):
    id                  = UUIDField(primary_key=True)
    model               = CharField(max_length=100)
    prompt_tokens       = IntegerField(default=0)
    completion_tokens   = IntegerField(default=0)
    total_tokens        = IntegerField(default=0)
    estimated_cost_usd  = DecimalField(max_digits=10, decimal_places=6, default=0)
    source              = CharField(max_length=20)  # "agent" | "chat" | "unknown"
    run                 = FK(AgentRun, null=True, blank=True, on_delete=SET_NULL)
    conversation        = FK("chat.Conversation", null=True, blank=True, on_delete=SET_NULL)

    class Meta:
        indexes = [
            Index(fields=["created_at"]),
            Index(fields=["source", "created_at"]),
        ]
```

### `ReembedLog`

Records each time `full_reembed()` or `reembed()` completes. Used as the data source
for the "Memory reembedded" activity event and the embed status bar sentinel.

```python
class ReembedLog(TimeStampedModel):
    id               = UUIDField(primary_key=True)
    paragraph_count  = IntegerField()   # paragraphs processed
    records_added    = IntegerField(default=0)
    records_deleted  = IntegerField(default=0)
    triggered_by     = CharField(max_length=20)  # "auto" | "manual" | "file_write"
```

`long_term.reembed()` and `long_term.full_reembed()` create a `ReembedLog` record on
completion. Replaces the file sentinel approach — a DB record is cleaner and queryable.
The embed status bar reads `ReembedLog.objects.order_by("-created_at").first()`.

Both models live in `agent/models.py` and require a single new migration.

---

## New Endpoints Summary

| Method | Path | Description |
|---|---|---|
| `GET` | `/agent/agents/` | Agent list |
| `GET/POST` | `/agent/agents/create/` | Create agent |
| `GET/POST` | `/agent/agents/<uuid>/` | Edit agent |
| `GET/POST` | `/agent/agents/<uuid>/delete/` | Delete confirmation + execute |
| `POST` | `/agent/agents/<uuid>/set-default/` | Set as default (HTMX) |
| `GET` | `/agent/tools/` | Tool list with agent selector |
| `POST` | `/agent/tools/<name>/toggle/` | Enable/disable *(existing)* |
| `POST` | `/agent/tools/<name>/policy/` | Override approval policy |
| `GET` | `/agent/memory/` | Memory page *(existing)* |
| `POST` | `/agent/memory/` | Save full file *(existing)* |
| `POST` | `/agent/memory/reembed/` | Trigger reembed *(existing)* |
| `GET` | `/agent/memory/search/` | Search memories (HTMX) |
| `POST` | `/agent/memory/paragraph/delete/` | Delete paragraph by hash |
| `POST` | `/agent/memory/paragraph/edit/` | Edit paragraph by hash |
| `GET` | `/agent/skills/` | Skills list *(existing)* |
| `POST` | `/agent/skills/install/` | Reload from workspace *(existing)* |
| `POST` | `/agent/skills/<name>/toggle/` | Enable/disable skill |
| `GET/POST` | `/agent/skills/<name>/delete/` | Delete confirmation + execute |
| `GET` | `/agent/runs/` | Run list (restyled + filter) |
| `GET` | `/agent/runs/<uuid>/` | Run detail with tool trace (restyled) |
| `GET` | `/agent/monitoring/` | Monitoring overview |
| `GET` | `/agent/monitoring/health/` | Health check partial (HTMX) |
| `GET` | `/agent/workspace/` | Workspace file list |
| `GET/POST` | `/agent/workspace/<filename>/` | Edit workspace file |

---

## UI Style

Dark background (`#343541`), panel backgrounds (`#202123`), Tailwind CSS CDN, HTMX for
partial updates, Alpine.js for local state (view toggles, expand/collapse, confirmations).

No new JS libraries. `marked.js` (already loaded in chat) is also loaded in
`base_agent.html` for markdown rendering on run detail output.

---

## Out of Scope

- Agent-to-agent communication or multi-agent orchestration
- Skill marketplace / remote install (dropped — security risk)
- Role-based access control (all views open — same as current)
- Alerting / push notifications on health degradation
- Heartbeat schedule management via UI (interval is a settings/env var)
- Multi-worker skill registry sync (noted as a limitation in the skills UI)

---

## Open Questions

| # | Question | Decision |
|---|---|---|
| 1 | Hard-delete vs soft-delete for agents with runs? | Soft-delete (`is_active=False`) + cancel active runs if historical runs exist; hard-delete only if zero runs |
| 2 | Per-agent approval policy override storage? | `Agent.metadata["tool_policies"]` — avoids a new migration; easy to promote to a model later |
| 3 | Inline approval card — replace typing indicator or separate bubble? | Replace typing indicator (same polling slot); typing indicator resumes after decision |
| 4 | `LLMUsage` written synchronously or via Celery? | Synchronous inside `get_completion()` — avoids loss if queue is busy |
| 5 | Activity feed include chat messages? | Yes, with a filter toggle to hide them |
| 6 | Memory embed status sentinel — file or DB? | DB (`ReembedLog` model) — cleaner and queryable; no file sentinel |

---

## Testing

### Key test cases

#### Agent CRUD (`agent/tests/test_agent_views.py`)

| Test | What it verifies |
|---|---|
| `test_agent_create` | POST valid form → Agent created, redirects to list |
| `test_agent_create_duplicate_name` | POST duplicate name → form error, no create |
| `test_agent_edit_set_default` | POST with `is_default=True` → other agents cleared |
| `test_agent_soft_delete_with_runs` | Delete agent with runs → `is_active=False`, active runs cancelled |
| `test_agent_hard_delete_no_runs` | Delete agent with no runs → DB record removed |
| `test_agent_delete_blocked_active_run` | Delete agent with RUNNING run → 400, not deleted |
| `test_set_default_htmx` | POST set-default → returns `_agent_row.html` partial |

#### Tool management

| Test | What it verifies |
|---|---|
| `test_tool_policy_override` | POST policy → `Agent.metadata["tool_policies"]` updated |
| `test_tool_policy_reset` | POST policy=default → key removed from metadata |
| `test_pending_approvals_panel` | Pending ToolExecution → card appears on tools page |

#### Memory management

| Test | What it verifies |
|---|---|
| `test_paragraph_delete_by_hash` | POST with hash → paragraph removed from file, reembed triggered |
| `test_paragraph_edit_by_hash` | POST with hash + new content → paragraph replaced, reembed triggered |
| `test_memory_search_returns_partial` | GET search → `_memory_search_results.html` returned |
| `test_embed_status_shows_reembed_log` | `ReembedLog` record → status bar shows correct timestamp |
| `test_out_of_sync_warning` | MEMORY.md mtime > ReembedLog created_at → warning badge shown |

#### Skills

| Test | What it verifies |
|---|---|
| `test_skill_toggle_disable` | POST toggle → `Skill.enabled=False`, excluded from LLM tools |
| `test_skill_delete` | POST delete → removed from registry + DB, files untouched |

#### Monitoring

| Test | What it verifies |
|---|---|
| `test_health_check_all_ok` | All services healthy → banner shows "All systems operational" |
| `test_health_check_no_default_agent` | No default agent → Agent status red |
| `test_health_check_cached` | Second request within 10s → no new DB/Redis queries |
| `test_cost_today_aggregation` | `LLMUsage` records today → correct token + cost totals |
| `test_llm_usage_recorded_on_completion` | `get_completion()` call → `LLMUsage` record created |
| `test_llm_usage_not_recorded_on_error` | `get_completion()` raises → no `LLMUsage` record |
| `test_approval_history_filter` | `?approval_status=pending` → only pending rows returned |

#### Inline approval card in chat

| Test | What it verifies |
|---|---|
| `test_stream_returns_approval_card` | `AgentRun(status=waiting)` + pending TE → `_tool_approval_card.html` returned by stream endpoint |
| `test_stream_returns_typing_after_approval` | After approval, next poll → typing indicator (no more pending TE) |
| `test_multiple_pending_shows_oldest` | Two pending TEs → oldest shown first |

---

## Implementation Notes

_To be filled in during implementation._
