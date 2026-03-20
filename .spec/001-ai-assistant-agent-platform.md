# 001 — AI Assistant & Agent Platform

**Status:** Draft
**Created:** 2026-03-18

---

## Goal

Build a Django-based platform with two focused apps — `chat` (conversational AI assistant) and `agent` (autonomous AI agent) — accessible across four interfaces: Web (HTMX), Telegram, CLI, and Heartbeat. The system must support multi-turn conversations, long-running agentic workflows, vector memory, and multi-provider LLM routing through a unified backend.

---

## Background

The platform needs to serve two distinct interaction modes:

1. **Chat**: Stateful, multi-turn conversations where a user sends a message and receives a response. Low latency is important. The assistant uses conversation history as context.
2. **Agent**: Autonomous, multi-step task execution using tools (web search, code execution, file operations, etc.). Runs can be long-lived, may require human-in-the-loop approval, and need reliable async execution with state persistence.

Both modes share infrastructure: LLM routing (litellm), async task queue (Celery + Redis), vector storage (pgvector), and a common interface adapter layer that normalises input from Web, Telegram, CLI, and Heartbeat.

---

## System Overview

```
┌──────────────────────────────────────────────────────────┐
│                        Interfaces                        │
│   Web (HTMX)  │  Telegram Bot  │  CLI  │  Heartbeat      │
└──────┬─────────────────┬────────────┬──────────┬─────────┘
       │                 │            │          │
       ▼                 ▼            ▼          ▼
┌──────────────────────────────────────────────────────────┐
│                   Interface Adapters                     │
│         (normalise to a common InboundEvent)            │
└──────────────────────┬───────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
  ┌───────────────┐        ┌─────────────────┐
  │   chat app    │        │   agent app     │
  │ (assistant)   │        │ (LangGraph)     │
  └───────┬───────┘        └────────┬────────┘
          │                         │
          ▼                         ▼
  ┌───────────────────────────────────────────┐
  │           Shared Services                 │
  │  litellm router │ Celery │ pgvector mem   │
  └───────────────────────────────────────────┘
          │
          ▼
  ┌───────────────────────────────────────────┐
  │     PostgreSQL (+ pgvector extension)     │
  │     Redis (broker + result backend)       │
  └───────────────────────────────────────────┘
```

---

## Directory Structure

```
project_root/
├── manage.py
├── pyproject.toml            # uv-managed dependencies
├── uv.lock
├── .env.example
├── .spec/
├── .doc/
│
├── config/                   # Django project package
│   ├── __init__.py
│   ├── settings/
│   │   ├── base.py
│   │   ├── local.py
│   │   └── production.py
│   ├── urls.py
│   ├── celery.py             # Celery app init + beat schedule
│   └── wsgi.py
│
├── chat/                     # Conversational assistant app
│   ├── models.py             # Conversation, Message
│   ├── views.py              # HTMX views + JSON API
│   ├── services.py           # ChatService (LLM call, history management)
│   ├── tasks.py              # Async message processing (Celery)
│   ├── urls.py
│   ├── admin.py
│   ├── templates/chat/
│   │   ├── conversation.html
│   │   ├── _message.html     # HTMX partial — single message bubble
│   │   └── _message_list.html
│   └── migrations/
│
├── agent/                    # Autonomous agent app
│   ├── models.py             # Agent, AgentRun, Memory
│   ├── views.py              # Run management + SSE status stream
│   ├── services.py           # AgentService (LangGraph orchestration)
│   ├── graph.py              # LangGraph graph definition & nodes
│   ├── tools.py              # Tool registry & implementations
│   ├── tasks.py              # execute_agent_run Celery task
│   ├── urls.py
│   ├── admin.py
│   ├── templates/agent/
│   │   ├── run_detail.html
│   │   └── _run_status.html  # HTMX partial — live status polling
│   └── migrations/
│
├── interfaces/               # Adapter layer (not a Django app)
│   ├── __init__.py
│   ├── base.py               # InboundEvent dataclass, BaseAdapter
│   ├── web.py                # Extracts InboundEvent from HttpRequest
│   ├── telegram.py           # Telegram webhook handler + reply sender
│   ├── cli.py                # Click-based CLI entry point
│   └── heartbeat.py          # Celery beat task definitions
│
├── core/                     # Shared utilities Django app
│   ├── llm.py                # litellm router config & helper
│   ├── memory.py             # pgvector read/write helpers
│   ├── pagination.py
│   └── models.py             # TimeStampedModel abstract base
│
└── static/
    └── js/htmx.min.js
```

---

## Database Models

### `core` — Abstract base

```python
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
```

---

### `chat` app

```python
class Conversation(TimeStampedModel):
    class Interface(models.TextChoices):
        WEB       = "web"
        TELEGRAM  = "telegram"
        CLI       = "cli"

    id            = models.UUIDField(primary_key=True, default=uuid4)
    interface     = models.CharField(max_length=20, choices=Interface)
    external_id   = models.CharField(max_length=255, blank=True)
    # external_id: Telegram chat_id, CLI session token, etc.
    title         = models.CharField(max_length=255, blank=True)
    system_prompt = models.TextField(blank=True)
    metadata      = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=["interface", "external_id"]),
        ]


class Message(TimeStampedModel):
    class Role(models.TextChoices):
        SYSTEM    = "system"
        USER      = "user"
        ASSISTANT = "assistant"
        TOOL      = "tool"

    id              = models.UUIDField(primary_key=True, default=uuid4)
    conversation    = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                        related_name="messages")
    role            = models.CharField(max_length=20, choices=Role)
    content         = models.TextField()
    model           = models.CharField(max_length=100, blank=True)  # model used
    input_tokens    = models.IntegerField(null=True)
    output_tokens   = models.IntegerField(null=True)
    metadata        = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at"]
```

---

### `agent` app

```python
class Agent(TimeStampedModel):
    id            = models.UUIDField(primary_key=True, default=uuid4)
    name          = models.CharField(max_length=100, unique=True)
    description   = models.TextField(blank=True)
    system_prompt = models.TextField()
    tools         = models.JSONField(default=list)   # list of enabled tool names
    model         = models.CharField(max_length=100) # litellm model string
    is_active     = models.BooleanField(default=True)
    metadata      = models.JSONField(default=dict)


class AgentRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING   = "pending"
        RUNNING   = "running"
        WAITING   = "waiting"   # human-in-the-loop pause
        COMPLETED = "completed"
        FAILED    = "failed"

    class TriggerSource(models.TextChoices):
        WEB       = "web"
        TELEGRAM  = "telegram"
        CLI       = "cli"
        HEARTBEAT = "heartbeat"

    id             = models.UUIDField(primary_key=True, default=uuid4)
    agent          = models.ForeignKey(Agent, on_delete=models.PROTECT,
                                       related_name="runs")
    conversation   = models.ForeignKey("chat.Conversation", null=True, blank=True,
                                       on_delete=models.SET_NULL)
    # links a run back to the conversation it was initiated from
    trigger_source = models.CharField(max_length=20, choices=TriggerSource)
    status         = models.CharField(max_length=20, choices=Status,
                                      default=Status.PENDING)
    input          = models.TextField()
    output         = models.TextField(blank=True)
    graph_state    = models.JSONField(default=dict)  # serialised LangGraph state
    celery_task_id = models.CharField(max_length=255, blank=True)
    error          = models.TextField(blank=True)
    started_at     = models.DateTimeField(null=True)
    finished_at    = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["agent", "status"]),
        ]


class Memory(TimeStampedModel):
    """Vector memory store. Requires pgvector extension."""
    id          = models.UUIDField(primary_key=True, default=uuid4)
    agent       = models.ForeignKey(Agent, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="memories")
    conversation = models.ForeignKey("chat.Conversation", null=True, blank=True,
                                     on_delete=models.SET_NULL)
    content     = models.TextField()
    embedding   = VectorField(dimensions=1536)  # pgvector; dimensions match model
    source      = models.CharField(max_length=50, blank=True)
    metadata    = models.JSONField(default=dict)

    class Meta:
        indexes = [
            HnswIndex(                          # pgvector HNSW for ANN search
                name="memory_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            )
        ]
```

---

## API Endpoints

### Chat app (`/chat/`)

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `GET` | `/chat/` | Conversation list (web) | Full HTML |
| `POST` | `/chat/conversations/` | Create new conversation | Redirect / JSON |
| `GET` | `/chat/conversations/<uuid>/` | Conversation view | Full HTML |
| `POST` | `/chat/conversations/<uuid>/messages/` | Send user message; enqueue reply | HTMX partial `_message.html` |
| `GET` | `/chat/conversations/<uuid>/messages/<uuid>/stream/` | SSE stream for pending assistant reply | `text/event-stream` |
| `DELETE` | `/chat/conversations/<uuid>/` | Delete conversation | 204 |

### Agent app (`/agent/`)

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `GET` | `/agent/runs/` | Run list | Full HTML |
| `POST` | `/agent/runs/` | Trigger a new agent run | Redirect to run detail |
| `GET` | `/agent/runs/<uuid>/` | Run detail + live status | Full HTML |
| `GET` | `/agent/runs/<uuid>/status/` | HTMX poll: current status partial | `_run_status.html` |
| `POST` | `/agent/runs/<uuid>/respond/` | Submit human-in-the-loop response | HTMX partial |
| `POST` | `/agent/runs/<uuid>/cancel/` | Cancel a pending/running run | HTMX partial |

### Integration endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/integrations/telegram/webhook/` | Telegram Bot API webhook receiver |

### Internal / admin

- Celery beat handles Heartbeat triggers — no HTTP endpoint needed
- Django admin exposes `Agent`, `AgentRun`, `Conversation`, `Message`, `Memory`

---

## Key Technical Decisions

### 1. litellm as the LLM abstraction layer
**Decision:** All LLM calls go through litellm rather than calling provider SDKs directly.
**Rationale:** Single interface for OpenAI, Anthropic, Gemini, local Ollama, etc. Model strings like `"openai/gpt-4o"` or `"anthropic/claude-opus-4-6"` are configured per-agent, swappable without code changes. litellm also provides cost tracking, rate limit handling, and fallbacks.
**Trade-off:** Adds a dependency and can lag provider feature releases by a few days.

### 2. LangGraph for agent orchestration
**Decision:** The `agent` app uses LangGraph to define stateful, resumable agent workflows rather than a simple ReAct loop.
**Rationale:** LangGraph supports cycles, branching, human-in-the-loop interrupts, and serialisable state — necessary for long-running tasks that may pause for approval or tool results. Graph state is persisted to `AgentRun.graph_state` so a Celery task can resume a run after a worker restart.
**Trade-off:** More complex than a simple chain; graph definition in `agent/graph.py` must be versioned carefully alongside migrations.

### 3. Celery + Redis for async execution
**Decision:** All LLM calls (both chat replies and agent runs) are processed as Celery tasks. Redis is both the broker and result backend.
**Rationale:** LLM responses can take 10–60+ seconds. Offloading to Celery keeps Django views responsive and enables retries, prioritisation, and concurrency control. The web interface polls for results via HTMX (`hx-trigger="every 2s"`) or uses SSE for chat streaming.
**Trade-off:** Operational complexity — requires running a Celery worker process alongside Django.

### 4. PostgreSQL + pgvector for vector storage
**Decision:** Vector memory is stored in the same PostgreSQL database using the `pgvector` extension rather than a dedicated vector database (Pinecone, Qdrant, etc.).
**Rationale:** Reduces infrastructure footprint. pgvector with HNSW indexing is sufficient for this scale. Keeps all persistent data in one place, simplifying backups, transactions, and deployment.
**Trade-off:** Not suitable if memory corpus exceeds tens of millions of vectors; revisit if scale demands it.

### 5. Interface adapter pattern
**Decision:** All four interfaces (Web, Telegram, CLI, Heartbeat) normalise inbound events to a shared `InboundEvent` dataclass before routing to `chat` or `agent` services.
**Rationale:** Core business logic in `chat/services.py` and `agent/services.py` remains interface-agnostic. Adding a new interface (e.g., Slack) requires only a new adapter in `interfaces/`, not changes to app logic.

### 6. Two separate Django apps, not one
**Decision:** `chat` and `agent` are distinct apps with their own models, views, and services, sharing only `core` utilities.
**Rationale:** Their data models and execution patterns differ significantly — chat is synchronous-feeling and low-latency; agent runs are long, async, and stateful. Keeping them separate makes each easier to reason about and test. An `AgentRun` can optionally link to a `Conversation` for context, but neither app depends on the other's internals.

### 7. HTMX + SSE for real-time UI (no WebSockets)
**Decision:** Chat streaming uses Server-Sent Events (SSE); agent status uses HTMX polling (`hx-trigger="every 2s"`). No WebSockets.
**Rationale:** SSE is simpler to deploy (works over standard HTTP, no sticky sessions required at the load balancer). HTMX polling is sufficient for agent status where sub-second latency is not required.
**Trade-off:** SSE is unidirectional; for true bidirectional needs, revisit Django Channels + WebSockets.

---

## Out of Scope

- User authentication and multi-tenancy (deferred to a later spec)
- Fine-tuning or model training
- File/image uploads and multimodal inputs
- Agent-to-agent communication
- A dedicated vector database (pgvector is sufficient for now)

---

## Acceptance Criteria

- [ ] `chat` app: a user can start a conversation via the web UI, send a message, and receive a streamed assistant reply
- [ ] `chat` app: Telegram messages are received via webhook, processed, and replied to in the same Telegram chat
- [ ] `chat` app: the CLI interface can initiate and continue a conversation
- [ ] `agent` app: an agent run can be triggered from web, Telegram, CLI, or Heartbeat
- [ ] `agent` app: run status is visible in real time on the web UI via HTMX polling
- [ ] `agent` app: a run can be paused for human-in-the-loop input and resumed after a response is submitted
- [ ] Vector memory: `Memory` records can be written and retrieved by cosine similarity
- [ ] All LLM calls route through litellm; the model can be changed per-agent via the admin UI without code changes
- [ ] Celery workers process tasks independently of the Django request/response cycle

---

## Open Questions

| # | Question | Owner | Target |
|---|----------|-------|--------|
| 1 | Which LLM model is the default for new agents? | — | Before 002 |
| 2 | Does the Heartbeat interface need cron-like scheduling per-agent or a single global interval? | — | Before implementation |
| 3 | Should `Memory` be scoped per-agent, per-conversation, or globally shared? | — | Before implementation |
| 4 | Token budget / cost limits per run — hard cap or soft alert? | — | Before implementation |
| 5 | Telegram bot token and webhook URL management — single bot or per-agent bots? | — | Before implementation |

---

## Implementation Notes

_To be filled in during implementation. Record deviations from the proposed solution and why._
