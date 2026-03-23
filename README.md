# GavinAgent

An autonomous AI agent platform built with Django, HTMX, LangGraph, and Celery. Supports multi-step tool use, long-term memory, a workspace-driven skill system, MCP server integration, and a real-time web chat UI.

---

## Architecture

```mermaid
graph TD
    subgraph Clients
        Browser["Browser (HTMX)"]
        Telegram["Telegram Bot"]
        CLI["CLI"]
    end

    subgraph Django
        chat["chat — conversations & LLM chat"]
        agent["agent — orchestration, tools, memory"]
        core["core — LLM client, embeddings"]
    end

    subgraph Workers["Celery Workers"]
        exec["execute_agent_run (LangGraph loop)"]
        proc["process_chat_message"]
        hb["heartbeat_task"]
    end

    subgraph Storage
        pg["PostgreSQL + pgvector"]
        redis["Redis (broker + results)"]
    end

    LiteLLM["LiteLLM (OpenAI / Anthropic / …)"]
    MCP["MCP Servers (stdio / SSE)"]

    Browser -- HTTP/HTMX --> Django
    Telegram -- Webhook --> Django
    Django -- "task.delay()" --> Workers
    Workers -- LangGraph --> LiteLLM
    Workers -- tool calls --> MCP
    Workers -- ORM --> pg
    Workers -- broker --> redis
```

**Agent loop (summary):** user message → Celery task → LangGraph state machine → LLM call → tool execution (web, file, shell, chart, …) → repeat until final answer → save reply → HTMX polling delivers it to browser.

See [`.doc/architecture.md`](.doc/architecture.md) for the full component breakdown and [`.doc/agent-loop.md`](.doc/agent-loop.md) for the detailed LangGraph flowchart.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Managed by `uv` |
| **uv** | `irm https://astral.sh/uv/install.ps1 \| iex` (Windows) / `curl -LsSf https://astral.sh/uv/install.sh \| sh` (macOS/Linux) |
| **Docker Desktop** | For PostgreSQL + Redis |
| **OpenAI or Anthropic API key** | At least one LLM provider |

---

## Quick Start

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd GavinAgent
uv sync
```

### 2. Start PostgreSQL and Redis

```bash
docker compose up -d
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```dotenv
OPENAI_API_KEY=sk-...          # or ANTHROPIC_API_KEY
FERNET_KEY=<generated-key>     # encrypt MCP server secrets
```

Generate a Fernet key:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4. Run migrations

```bash
uv run python manage.py migrate --settings=config.settings.local
```

This also seeds a default **Agentic Assistant** agent with all built-in tools enabled.

### 5. Create a superuser

```bash
uv run python manage.py createsuperuser --settings=config.settings.local
```

### 6. Start all services

Open **three terminals** in the project directory:

**Terminal 1 — Django dev server**
```bash
uv run python manage.py runserver --settings=config.settings.local
```

**Terminal 2 — Celery worker** (use `--pool=solo` on Windows)
```bash
uv run celery -A config worker -l info --pool=solo
```

**Terminal 3 — Celery Beat scheduler**
```bash
uv run celery -A config beat -l info
```

Open **http://127.0.0.1:8000/chat/** in your browser.

---

## Using the App

### Chat with an agent

1. Go to **http://127.0.0.1:8000/chat/**
2. Click **New Chat**
3. Enable the **Agentic Assistant** via the agent toggle (robot icon in the top bar)
4. Send a message — the agent will autonomously use tools to answer

Without the agent toggle, the chat is a plain LLM conversation with no tool access.

### Agent dashboard

**http://127.0.0.1:8000/agent/**

| Section | What you can do |
|---|---|
| **Agents** | Create / edit agents, configure tools, model, system prompt |
| **Runs** | View execution history, tool calls, inputs/outputs per run |
| **Memory** | Browse and search long-term vector memory |
| **Skills** | View workspace skills loaded from `workspace/skills/` |
| **Tools** | See all registered built-in tools |
| **MCP** | Add MCP servers (stdio subprocess or SSE remote) |
| **Monitoring** | Token usage and cost tracking |
| **Workspace** | Browse and edit markdown files in the agent workspace |

### Built-in tools

| Tool | Description |
|---|---|
| `web_read` | Fetch a web page as clean markdown (via Jina reader) |
| `api_get` / `api_post` | Make HTTP requests to REST APIs |
| `file_read` / `file_write` | Read/write files in the agent workspace |
| `shell` | Execute shell commands (requires approval by default) |
| `chart` | Generate bar/line/pie/scatter charts as embedded images |
| `get_datetime` | Get the current date and time |

### Skills

Skills are markdown-defined capabilities stored in `workspace/skills/<name>/SKILL.md`. They can optionally include a `handler.py` for direct execution. Add a skill:

```
workspace/
  skills/
    my-skill/
      SKILL.md       ← YAML frontmatter (name, description, triggers) + instructions
      handler.py     ← optional Python handler(input: str) -> str
```

The skill system ships with `web-research`, `data-analysis`, `charts`, and `workflow-management` skills pre-installed.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/agent_db` | PostgreSQL connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis broker + result backend |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `LITELLM_DEFAULT_MODEL` | `openai/gpt-4o-mini` | Default LLM model |
| `FERNET_KEY` | — | Fernet key for encrypting MCP server env vars |
| `TELEGRAM_BOT_TOKEN` | — | Optional Telegram bot integration |
| `AGENT_WORKSPACE_DIR` | `agent/workspace/` | Path to agent workspace directory |
| `SECRET_KEY` | *(local default)* | Django secret key — **change in production** |

---

## Project Structure

```
config/          Django settings and Celery app
agent/           Agent orchestration, tools, memory, MCP, skills, workflows
  graph/         LangGraph nodes and state definition
  tools/         Built-in tools (web, file, shell, chart, …)
  memory/        Long-term (pgvector) and short-term memory
  mcp/           MCP client, connection pool, registry
  skills/        Skill loader and registry
  workspace/     Markdown workspace files and skills directory
chat/            Chat conversations and plain LLM interface
core/            Shared LLM client, embedding utilities, base models
interfaces/      Telegram bot, CLI, heartbeat interfaces
.doc/            Detailed architecture and design documentation
.spec/           Feature specs (written before implementation)
```
