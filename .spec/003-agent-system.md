# 003 — Agent System (OpenClaw-inspired)

**Status:** Draft
**Created:** 2026-03-19

---

## Goal

Extend the existing `agent` app into a full autonomous AI coding agent, inspired by the
OpenClaw pattern. The agent can execute tools, maintain persistent memory, run proactively
on a heartbeat schedule, and load modular skills. The `chat` app is not modified; it
communicates with the agent exclusively through a Django signal.

---

## Background

Spec 001 defined the `Agent`, `AgentRun`, and `Memory` models plus a minimal LangGraph
stub. Spec 002 built the chat UI. This spec builds out the agent intelligence layer:
a proper tool system, memory system, skill system, heartbeat, and agent dashboard — all
inside the `agent` app.

---

## Decisions vs. Source Spec

| Topic | Source spec | This project |
|---|---|---|
| Streaming | Django Channels + SSE | HTMX polling (spec 002 decision, no Channels) |
| CLI | Typer + Rich | Click (already implemented in `interfaces/cli.py`) |
| RAG | LlamaIndex | pgvector directly via `core/memory.py` (already in place) |
| AgentSession model | New model | Reuse existing `AgentRun` (same role, already linked to `Conversation`) |
| AgentMessage model | New model | Reuse existing `chat.Message` (already stores all messages) |
| Tracing | LangSmith | Optional — add `LANGSMITH_API_KEY` to `.env`; off when key absent |

---

## App Responsibility Split

```
chat/                              agent/
───────────────────────            ───────────────────────────────
Conversation UI (HTMX)             Agent loop (LangGraph)
Message history                    Tool execution + approval
Model / prompt settings            Memory (short + long term)
Polling for replies                Skill management
                                   Heartbeat scheduler (Celery Beat)
                                   Agent dashboard (logs, memory, tools)
```

The `chat` app fires a signal when a user message arrives. The `agent` app listens,
runs the loop, and writes the assistant reply back to `chat.Message`. The `chat` polling
endpoint (`/stream/`) picks it up with no further changes.

---

## App Initialisation (`AgentConfig.ready()`)

```python
class AgentConfig(AppConfig):
    def ready(self):
        import agent.signals          # registers signal receivers
        from agent.workspace import ensure_workspace
        ensure_workspace()            # creates workspace/ + example .md files if absent
```

`ensure_workspace()` creates `workspace/AGENTS.md.example`, `SOUL.md.example`,
`HEARTBEAT.md.example`, and `memory/MEMORY.md` if they do not exist. It never
overwrites existing files.

---

## Agent Loop (OpenClaw pattern)

```
InboundMessage
    │
    ▼
assemble_context       ← Agent.system_prompt + AGENTS.md + SOUL.md
                          + short-term Redis context + MEMORY.md excerpt
    │
    ▼
call_llm               ← litellm (streaming internally, result saved atomically)
    │
    ├── no tool calls → save_result → write chat.Message(role=assistant)
    │
    └── tool calls
            │
            ▼
        check_approval     ← auto / requires human approval per tool policy
            │
            ├── auto → execute_tools → feed results back → call_llm
            │
            └── approval needed
                    │
                    ▼
                save ToolExecution(status=pending)
                pause AgentRun(status=waiting)
                    │
                    ▼ (user approves via chat UI or agent dashboard)
                execute_tools → feed results → call_llm
```

Implemented as a LangGraph `StateGraph`. State is persisted via a custom
`DjangoCheckpointer` that reads/writes `AgentRun.graph_state` (JSON). The checkpointer
is passed to `graph.compile(checkpointer=DjangoCheckpointer(run_id))`. This allows
Celery workers to resume a paused graph from the exact node where it stopped.

### Post-rejection behaviour

When a user rejects a tool approval:
- `ToolExecution(status=rejected)` is saved
- The tool result fed back to the LLM is: `{"error": "Tool execution was rejected by the user."}`
- The LLM decides whether to try an alternative approach or conclude it cannot complete the task
- `AgentRun` status returns to `running`; the loop continues

### Concurrent message handling

Only one Celery task may run per `AgentRun` at a time. Before enqueuing,
`AgentRunner.enqueue()` checks `AgentRun.celery_task_id`. If a task is already
active (inspectable via Celery inspect), the new message is appended to the
conversation history and the running task will process it in its next loop iteration.
If no task is active, a new one is enqueued and `celery_task_id` is updated atomically
using `select_for_update()`.

---

## Directory Structure Changes

The following restructuring is required. Existing top-level files (`graph.py`, `tools.py`)
are replaced by packages.

```
agent/
├── models.py              ← extend: add ToolExecution, HeartbeatLog, Skill
├── views.py               ← extend: add dashboard, logs, memory, tools, skills views
├── urls.py                ← extend
├── signals.py             ← NEW: receives signal from chat, triggers AgentRunner
├── runner.py              ← NEW: AgentRunner entry point (replaces services.py)
│
├── graph/                 ← replaces graph.py
│   ├── __init__.py
│   ├── state.py           ← AgentState TypedDict (extended)
│   ├── nodes.py           ← assemble_context, call_llm, execute_tools, save_result
│   └── graph.py           ← compiled StateGraph
│
├── tools/                 ← replaces tools.py
│   ├── __init__.py
│   ├── base.py            ← BaseTool + approval policy constants
│   ├── shell.py           ← shell command (approval required)
│   ├── browser.py         ← Playwright browser automation (approval required)
│   ├── file.py            ← file read (auto) / file write (approval required)
│   └── api.py             ← HTTP GET (auto) / POST (approval required)
│
├── memory/                ← new package
│   ├── __init__.py
│   ├── short_term.py      ← Redis, TTL 4h, last N messages as JSON
│   └── long_term.py       ← MEMORY.md read/write + sync to Memory model
│
├── skills/                ← new package
│   ├── __init__.py
│   ├── loader.py          ← scans workspace/skills/, parses SKILL.md frontmatter
│   └── registry.py        ← in-memory registry, exposes skills as LangGraph tools
│
├── tasks.py               ← extend: add heartbeat_task, tool_execution_task
│
├── templates/agent/
│   ├── dashboard.html     ← status, last heartbeat, active runs
│   ├── logs.html          ← heartbeat log + tool execution history
│   ├── memory.html        ← view/edit MEMORY.md
│   ├── tools.html         ← enable/disable tools, approval policy
│   └── skills.html        ← installed skills list
│
└── workspace/             ← runtime dir, gitignored except *.example files
    ├── AGENTS.md          ← agent persona and instructions (loaded into context)
    ├── SOUL.md            ← agent values and personality
    ├── HEARTBEAT.md       ← autonomous task checklist (Celery Beat reads this)
    └── memory/
        └── MEMORY.md      ← long-term memory (agent-writable markdown)
```

---

## Database Models

### Extend existing models

`AgentRun` — no schema changes needed. `status=waiting` already supports
human-in-the-loop pause. `graph_state` persists LangGraph state.

`Memory` — no schema changes. Used for vector search (pgvector). Long-term
narrative memory uses `MEMORY.md` (file) mirrored into `Memory` records for search.

### New models

#### `ToolExecution`

Audit log of every tool call the agent made or attempted.

```python
class ToolExecution(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING  = "pending"   # awaiting user approval
        RUNNING  = "running"
        SUCCESS  = "success"
        ERROR    = "error"
        REJECTED = "rejected"  # user denied approval

    id                = UUIDField(primary_key=True)
    run               = FK(AgentRun, related_name="tool_executions")
    tool_name         = CharField(max_length=100)
    input             = JSONField()
    output            = JSONField(null=True)
    status            = CharField(choices=Status)
    requires_approval = BooleanField(default=False)
    approved_at       = DateTimeField(null=True)
    duration_ms       = IntegerField(null=True)
```

#### `HeartbeatLog`

One record per Celery Beat trigger.

```python
class HeartbeatLog(TimeStampedModel):
    class Status(models.TextChoices):
        OK    = "ok"      # checked, nothing to do
        ACTED = "acted"   # ran agent loop, took actions
        ERROR = "error"

    id             = UUIDField(primary_key=True)
    triggered_at   = DateTimeField()
    status         = CharField(choices=Status)
    actions_taken  = JSONField(default=list)
    error_message  = TextField(blank=True)
```

#### `Skill`

Registry of installed skills.

```python
class Skill(TimeStampedModel):
    id           = UUIDField(primary_key=True)
    name         = CharField(max_length=100, unique=True)
    description  = TextField(blank=True)
    path         = CharField(max_length=500)   # filesystem path
    enabled      = BooleanField(default=True)
    installed_at = DateTimeField(auto_now_add=True)
```

---

## Context Window Management

`assemble_context` builds the prompt in priority order and truncates from the bottom
if the total exceeds the model's context limit:

| Priority | Content | Truncation |
|---|---|---|
| 1 (keep) | AGENTS.md + SOUL.md | Never truncated |
| 2 (keep) | Relevant MEMORY.md excerpts (top 5 by similarity) | Never truncated |
| 3 (trim) | Conversation history | Drop oldest messages first |
| 4 (trim) | Tool results | Truncate to `MAX_TOOL_OUTPUT_CHARS` per result |

```python
# config/settings/base.py
AGENT_CONTEXT_BUDGET_TOKENS = 8000   # leave headroom for output
MAX_TOOL_OUTPUT_CHARS = 4000         # per tool result, truncated with "...[truncated]"
```

Token counting uses `litellm.token_counter()` so limits are model-aware.

---

## Tool Execution Constraints

```python
# config/settings/base.py
AGENT_TOOL_TIMEOUT_SECONDS = 30      # hard timeout per tool call
```

- Enforced via `asyncio.wait_for()` in `ToolExecutor`
- Timeout raises `ToolTimeoutError` → `ToolExecution(status=error)` → LLM receives error result and decides next step
- Browser tool has a separate `AGENT_BROWSER_TIMEOUT_SECONDS = 60` override

---

## Workspace Files

Loaded fresh at the start of each agent loop run (no caching — allows live editing).

| File | Purpose | Who writes it |
|---|---|---|
| `AGENTS.md` | Agent persona, role, behaviour rules | Human |
| `SOUL.md` | Values, tone, ethical constraints | Human |
| `HEARTBEAT.md` | Checklist of autonomous tasks | Human |
| `memory/MEMORY.md` | Persistent facts the agent has learned | Agent (autonomously) |

`AGENTS.md` and `SOUL.md` are prepended to the system context on every run.
`MEMORY.md` is summarised (top N relevant lines via keyword match) and included.

---

## Skill System

Skills are portable directories:

```
agent/workspace/skills/web-search/
├── SKILL.md       ← YAML frontmatter + natural language instructions
└── handler.py     ← optional Python entrypoint
```

`SKILL.md` format:
```yaml
---
name: web-search
description: Search the web for current information
tools: [httpx]
approval_required: false
---
## Instructions
Use this skill when the user asks about recent events...
```

`SkillLoader` scans `agent/workspace/skills/` on startup (and on demand via
`/agent/skills/install/`). Skills are registered in `SkillRegistry` and exposed as
LangGraph-compatible tools. The `Agent.tools` JSONField lists enabled skill names
alongside built-in tool names.

---

## Memory System

### Short-term (Redis)
- Key: `agent:run:{run_id}:context`
- TTL: 4 hours
- Content: last 20 messages as JSON
- Read by `assemble_context` node at each LLM call

### Long-term (Markdown + pgvector)
- `MEMORY.md`: human-readable, agent-writable. Included in context as a summary.
- `Memory` model: vector embeddings of each MEMORY.md paragraph via `core/memory.py`.
  Relevant entries retrieved by cosine similarity during `assemble_context`.
- Agent writes new facts to MEMORY.md via the `file_write` tool.
- After `file_write` to `MEMORY.md`, the tool implementation explicitly calls
  `long_term.reembed()` which re-reads the file, diffs changed paragraphs, and
  upserts `Memory` records. This is synchronous within the tool call — no separate
  signal needed.

#### Memory scope

`Memory` records are **globally shared** across all agents and conversations.
`assemble_context` searches the entire `Memory` table by cosine similarity with no
`agent` or `conversation` filter. The `agent` and `conversation` FK fields are
retained for audit/provenance (to know what wrote each record) but are not used as
search filters.

#### Manual reembed

A full reembed can be triggered by a human via `/agent/memory/reembed/` (POST).
This runs `long_term.full_reembed()` which:

1. Reads the current `MEMORY.md` in full
2. Splits into paragraphs
3. Upserts `Memory` records for all current paragraphs (by content hash)
4. **Deletes** any `Memory` records whose content hash no longer exists in the file

This is the correct way to clean up orphaned records after manual edits that remove
paragraphs. It is also available as a Django management command:

```bash
uv run python manage.py reembed_memory
```

The `/agent/memory/` edit page shows a "Re-embed" button that triggers this endpoint
via HTMX, with a status indicator while the task runs (Celery task, non-blocking).

---

## `Agent.is_default` Uniqueness

Enforced in `Agent.save()`:

```python
def save(self, *args, **kwargs):
    if self.is_default:
        Agent.objects.exclude(pk=self.pk).update(is_default=False)
    super().save(*args, **kwargs)
```

Only one Agent can be `is_default=True` at any time. Django admin shows a warning
if no default is set.

---

## Heartbeat System

```
Celery Beat (every 30 min)
    │
    ▼
heartbeat_task()
    │
    ▼
Read HEARTBEAT.md checklist
    │
    ├── nothing to act on → HeartbeatLog(status=ok)
    │
    └── action needed
            │
            ▼
        AgentRunner.run(trigger_source=HEARTBEAT, input=checklist_item)
            │
            ▼
        Execute tools, update MEMORY.md
            │
            ▼
        Notify via Telegram (if TELEGRAM_BOT_TOKEN set)
            │
            ▼
        HeartbeatLog(status=acted, actions_taken=[...])
```

`HEARTBEAT.md` example:
```markdown
## Daily checks
- [ ] Summarise any GitHub notifications
- [ ] Check for failing CI pipelines
```

---

## chat ↔ agent Integration

The `chat` app fires a signal when a user message is saved. The `agent` app connects
to this signal if the conversation has an active `Agent` linked via `AgentRun`.

```python
# chat/signals.py (new small file)
from django.dispatch import Signal
message_created = Signal()   # kwargs: conversation_id, message_id

# chat/views.py — fire after saving user message (MessageCreateView.post)
from chat.signals import message_created
message_created.send(sender=None, conversation_id=..., message_id=...)

# agent/signals.py — listen and trigger runner
from chat.signals import message_created
from agent.runner import AgentRunner

def on_message_created(sender, conversation_id, message_id, **kwargs):
    run = AgentRun.objects.filter(
        conversation_id=conversation_id,
        status__in=[AgentRun.Status.PENDING, AgentRun.Status.WAITING]
    ).first()
    if run:
        AgentRunner.enqueue(run)
```

The runner writes the assistant reply as `chat.Message(role=assistant)`. The existing
`MessageStreamView` polling picks it up with no changes to the chat app.

---

## Tool Approval Policy

| Tool | Default |
|---|---|
| `file_read` | auto |
| `web_search` (skill) | auto |
| `api_get` | auto |
| `file_write` | approval required |
| `shell` | approval required |
| `browser` | approval required |
| `api_post` | approval required |

When approval is required: `ToolExecution(status=pending)` is saved, `AgentRun`
set to `waiting`. The user approves via the agent dashboard (`/agent/approve/<id>/`)
or inline from the chat UI (HTMX partial injected into the conversation). After
approval, the Celery task resumes the LangGraph graph from the checkpointed state.

---

## New API Endpoints

Extends `agent/urls.py`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/agent/` | Dashboard — active runs, last heartbeat |
| `GET` | `/agent/logs/` | Heartbeat + tool execution history |
| `GET` | `/agent/memory/` | View MEMORY.md |
| `POST` | `/agent/memory/` | Edit MEMORY.md (HTMX form) |
| `POST` | `/agent/memory/reembed/` | Trigger full reembed (HTMX, runs as Celery task) |
| `GET` | `/agent/tools/` | Tool list + enable/disable |
| `POST` | `/agent/tools/<name>/toggle/` | Enable/disable a tool (HTMX) |
| `GET` | `/agent/skills/` | Installed skills |
| `POST` | `/agent/skills/install/` | Install skill from path |
| `POST` | `/agent/approve/<tool_id>/` | Approve pending tool execution |

Existing endpoints (`/agent/runs/`, `/agent/runs/<id>/`, etc.) are unchanged.

---

## New Dependencies

```bash
uv add playwright          # browser tool
uv add httpx               # API tool + skill loader
uv add langsmith           # optional tracing (off when LANGSMITH_API_KEY absent)
```

Playwright requires `playwright install chromium` after install.

---

## New Environment Variables

```bash
# Workspace path (defaults to <BASE_DIR>/agent/workspace/)
AGENT_WORKSPACE_DIR=

# Heartbeat interval in minutes (default: 30)
AGENT_HEARTBEAT_INTERVAL_MINUTES=30

# Tool constraints
AGENT_CONTEXT_BUDGET_TOKENS=8000
AGENT_TOOL_TIMEOUT_SECONDS=30
AGENT_BROWSER_TIMEOUT_SECONDS=60
MAX_TOOL_OUTPUT_CHARS=4000

# LangSmith (optional — tracing off when absent)
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=agent
```

---

## Validation

### Model-level

| Model | Rule | Enforcement |
|---|---|---|
| `AgentRun` | `status` transitions must follow: `pending → running → waiting/completed/failed`; completed/failed are terminal | `clean()` raises `ValidationError` on illegal transition |
| `AgentRun` | `agent` must have `is_active=True` at creation time | `clean()` |
| `Agent` | `tools` list entries must be registered names in `ToolExecutor` or `SkillRegistry` | `clean()` with warning (not hard error — tools may load later) |
| `Agent` | Only one `is_default=True` at a time | `save()` override clears others |
| `ToolExecution` | `approved_at` only set when `status=success` | `clean()` |

### Input validation (views)

- `AgentRun` creation: reject if no active `Agent` with `is_default=True` (heartbeat path) or if the specified `Agent.is_active=False`
- `/agent/approve/<tool_id>/`: reject if `ToolExecution.status != pending`; reject if `AgentRun.status != waiting`
- `/agent/memory/` POST: reject empty body; strip and limit to 50,000 characters

---

## Testing

### Test framework

```bash
uv add --dev pytest pytest-django pytest-celery pytest-mock factory-boy
```

```python
# pyproject.toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings.local"
```

### Fixtures (`agent/tests/conftest.py`)

```python
@pytest.fixture
def agent(db):
    return Agent.objects.create(
        name="test-agent", system_prompt="You are helpful.",
        model="openai/gpt-4o-mini", is_active=True, is_default=True
    )

@pytest.fixture
def conversation(db):
    return Conversation.objects.create(interface=Conversation.Interface.WEB)

@pytest.fixture
def agent_run(db, agent, conversation):
    return AgentRun.objects.create(
        agent=agent, conversation=conversation,
        trigger_source=AgentRun.TriggerSource.WEB,
        input="Hello",
    )

@pytest.fixture
def mock_llm(mocker):
    """Patches get_completion to return a fixed response without calling any LLM."""
    return mocker.patch("core.llm.get_completion", return_value=FakeLLMResponse("Done."))
```

### Unit tests

#### Graph nodes (`agent/tests/test_graph_nodes.py`)

| Test | What it verifies |
|---|---|
| `test_assemble_context_includes_agents_md` | AGENTS.md content appears in assembled messages |
| `test_assemble_context_truncates_history` | Oldest messages dropped when budget exceeded |
| `test_assemble_context_includes_memory_excerpts` | Top-N Memory records by cosine similarity included |
| `test_call_llm_no_tool_calls` | Returns `save_result` path when LLM returns plain text |
| `test_call_llm_with_tool_calls` | Returns `execute_tools` path when LLM returns tool calls |
| `test_save_result_writes_chat_message` | `chat.Message(role=assistant)` created with correct content |
| `test_save_result_updates_run_output` | `AgentRun.output` and `status=completed` set |

#### Tools (`agent/tests/test_tools.py`)

| Test | What it verifies |
|---|---|
| `test_file_read_returns_content` | Reads file from workspace, returns text |
| `test_file_write_creates_file` | Creates file, calls `reembed()` if path is MEMORY.md |
| `test_file_write_memory_triggers_reembed` | `long_term.reembed()` called when writing to MEMORY.md |
| `test_shell_timeout` | Raises `ToolTimeoutError` after `AGENT_TOOL_TIMEOUT_SECONDS` |
| `test_tool_output_truncated` | Output longer than `MAX_TOOL_OUTPUT_CHARS` is truncated |
| `test_api_get_returns_response` | HTTP GET executed, response body returned |

#### Memory (`agent/tests/test_memory.py`)

| Test | What it verifies |
|---|---|
| `test_short_term_store_and_retrieve` | Messages stored in Redis with correct TTL |
| `test_short_term_evicts_oldest` | Only last 20 messages kept |
| `test_long_term_reembed_creates_records` | New MEMORY.md paragraphs create `Memory` records |
| `test_long_term_reembed_updates_changed` | Changed paragraphs update existing `Memory` records |
| `test_long_term_search_returns_relevant` | cosine similarity search returns expected entries |
| `test_long_term_search_is_global` | search returns records from different agents/conversations |
| `test_full_reembed_deletes_orphaned_records` | paragraphs removed from MEMORY.md → corresponding `Memory` records deleted |
| `test_full_reembed_preserves_unchanged_records` | unchanged paragraphs retain same DB id (no unnecessary delete+insert) |
| `test_reembed_endpoint_triggers_task` | POST `/agent/memory/reembed/` → Celery task enqueued |
| `test_management_command_reembed` | `manage.py reembed_memory` runs `full_reembed()` successfully |

#### Skills (`agent/tests/test_skills.py`)

| Test | What it verifies |
|---|---|
| `test_loader_scans_workspace` | Skills in `workspace/skills/` are discovered |
| `test_loader_parses_skill_md` | `name`, `description`, `approval_required` read correctly |
| `test_registry_exposes_as_tool` | Skill with `handler.py` callable as LangGraph tool |
| `test_registry_prompt_only_skill` | Skill without `handler.py` still registered with description |

#### Heartbeat (`agent/tests/test_heartbeat.py`)

| Test | What it verifies |
|---|---|
| `test_heartbeat_no_tasks` | Empty HEARTBEAT.md → `HeartbeatLog(status=ok)`, no AgentRun created |
| `test_heartbeat_with_tasks` | Unchecked items → AgentRun created, `HeartbeatLog(status=acted)` |
| `test_heartbeat_no_default_agent` | No `is_default` agent → `HeartbeatLog(status=error)` |

### Integration tests

#### Agent loop (`agent/tests/test_runner.py`)

```python
@pytest.mark.django_db
def test_full_loop_no_tools(agent_run, mock_llm):
    """End-to-end: message in → assistant reply written to chat.Message."""
    AgentRunner.run(agent_run)
    assert chat.Message.objects.filter(
        conversation=agent_run.conversation,
        role=Message.Role.ASSISTANT,
        content="Done.",
    ).exists()
    agent_run.refresh_from_db()
    assert agent_run.status == AgentRun.Status.COMPLETED

@pytest.mark.django_db
def test_tool_approval_flow(agent_run, mock_llm_with_tool_call):
    """Shell tool call → ToolExecution(pending) → approve → run resumes."""
    mock_llm_with_tool_call.side_effect = [
        FakeLLMResponse(tool_call="shell", args={"command": "ls"}),
        FakeLLMResponse("Files listed."),
    ]
    AgentRunner.run(agent_run)
    tool_exec = ToolExecution.objects.get(run=agent_run, tool_name="shell")
    assert tool_exec.status == ToolExecution.Status.PENDING
    agent_run.refresh_from_db()
    assert agent_run.status == AgentRun.Status.WAITING

    # Simulate approval
    tool_exec.approved_at = timezone.now()
    tool_exec.status = ToolExecution.Status.RUNNING
    tool_exec.save()
    AgentRunner.resume(agent_run)

    agent_run.refresh_from_db()
    assert agent_run.status == AgentRun.Status.COMPLETED

@pytest.mark.django_db
def test_tool_rejection_continues_loop(agent_run, mock_llm_with_tool_call):
    """Rejected tool → error fed back to LLM → LLM produces final reply."""
    ...

@pytest.mark.django_db
def test_chat_signal_triggers_agent(agent_run, mock_llm):
    """Sending message_created signal enqueues Celery task for active AgentRun."""
    with patch("agent.runner.AgentRunner.enqueue") as mock_enqueue:
        message_created.send(
            sender=None,
            conversation_id=agent_run.conversation_id,
            message_id=uuid4(),
        )
        mock_enqueue.assert_called_once_with(agent_run)
```

#### Celery tasks (`agent/tests/test_tasks.py`)

```python
@pytest.mark.django_db
def test_execute_agent_run_task(agent_run, mock_llm, celery_worker):
    execute_agent_run.delay(str(agent_run.id))
    agent_run.refresh_from_db()
    assert agent_run.status == AgentRun.Status.COMPLETED

@pytest.mark.django_db
def test_heartbeat_task_creates_log(agent, celery_worker):
    heartbeat_task.delay()
    assert HeartbeatLog.objects.filter(status=HeartbeatLog.Status.OK).exists()
```

### What is NOT tested (and why)

| Excluded | Reason |
|---|---|
| LLM response quality | Non-deterministic; test infrastructure, not AI output |
| Playwright browser tool | Requires real browser; covered by manual smoke test |
| Telegram notification in heartbeat | Tested separately in Telegram integration test suite |

---

## Out of Scope

- Multi-user / multi-agent deployments
- Skill marketplace (ClawHub)
- Voice interface
- Agent-to-agent communication
- LlamaIndex RAG (pgvector is sufficient at this scale)

---

## Acceptance Criteria

- [ ] User message in `chat` triggers agent loop when an active `AgentRun` exists for the conversation
- [ ] Agent assembles context from `AGENTS.md`, `SOUL.md`, `MEMORY.md`, and conversation history
- [ ] Agent executes at least two tools autonomously (file read + web search skill)
- [ ] Tool requiring approval creates a `ToolExecution(status=pending)` and pauses the run
- [ ] User can approve/reject a pending tool from the agent dashboard; run resumes correctly
- [ ] Celery Beat fires `heartbeat_task` every 30 minutes; `HeartbeatLog` records are created
- [ ] Agent can write to `MEMORY.md` autonomously; new content is embedded into the `Memory` model
- [ ] Skills in `agent/workspace/skills/` are loaded on startup and available as tools
- [ ] Agent dashboard shows run status, last heartbeat time, and tool execution history

---

## Open Questions

| # | Question | Decision needed |
|---|---|---|
| 1 | Should `AgentRun` be created automatically when a conversation starts, or only when the user explicitly enables an agent for a conversation? | **Decided: explicit opt-in.** User toggles "Enable agent" in the conversation settings panel (spec 002). This creates an `AgentRun` on demand. Plain conversations remain lightweight chat with no agent overhead. |
| 2 | Approval UI — inline in the chat message stream, or only in the agent dashboard? | **Decided: inline in chat.** Agent inserts an approval card into the conversation via the existing polling mechanism. Dashboard shows history only. |
| 3 | Should MEMORY.md writes be agent-autonomous (via file tool) or also editable by humans via `/agent/memory/`? Both is fine but needs conflict handling. | **Decided: both.** Agent writes via file tool; human edits via `/agent/memory/` textarea. Last write wins — no conflict handling needed. |
| 4 | Which `Agent` DB record is used when the heartbeat triggers? One global default agent, or per-task configuration? | **Decided: single global default agent.** Add `is_default = BooleanField` to `Agent` model; only one can be default at a time. Heartbeat skips with a `HeartbeatLog(status=error)` warning if none is set. |

---

## Implementation Notes

_To be filled in during implementation. Record deviations from the proposed solution and why._
