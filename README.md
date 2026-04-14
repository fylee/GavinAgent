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
| **Python 3.11+** | Managed by `uv` (project uses 3.12) |
| **uv** | `irm https://astral.sh/uv/install.ps1 \| iex` (Windows) / `curl -LsSf https://astral.sh/uv/install.sh \| sh` (macOS/Linux) |
| **Docker Desktop** | For PostgreSQL + Redis |
| **OpenAI or Anthropic API key** | At least one LLM provider |
| **PowerShell 7+** | Required for running management commands on Windows |

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

This starts **PostgreSQL with pgvector** (port 5432), **Redis** (port 6379), and **SearXNG** web search (port 8888).

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

### Chat UI

1. Go to **http://127.0.0.1:8000/chat/**
2. Click **New Chat**
3. Enable the **Agentic Assistant** via the agent toggle (robot icon in the top bar)
4. Send a message — the agent will autonomously use tools to answer

**Chat features:**
- **Markdown rendering** — replies render headings, bold, code blocks, tables, and syntax highlighting (via highlight.js)
- **Slash-command autocomplete** — type `/` in the input to see and select available skills
- **Auto-generated titles** — conversation titles are automatically summarised from the first question by the LLM
- **Streaming** — responses stream token-by-token; tool calls show in a collapsible reasoning trace

Without the agent toggle, the chat is a plain LLM conversation (no tool access, lower latency).

### Agent dashboard

**http://127.0.0.1:8000/agent/**

| Section | What you can do |
|---|---|
| **Agents** | Create / edit agents, configure tools, model, system prompt |
| **Runs** | View execution history, tool calls, inputs/outputs per run |
| **Monitoring** | Token usage and cost tracking |
| **Logs** | Structured run logs and error traces |
| **Memory** | Browse and search long-term vector memory |
| **Knowledge** | Manage the RAG knowledge base (ingest documents) |
| **Tools** | See all registered built-in tools |
| **Skills** | View and manage workspace skills with source tabs (Workspace / Claude Code) |
| **MCP** | Add MCP servers (stdio subprocess or SSE remote); view connection status |
| **Workspace** | Browse and edit markdown files in the agent workspace |
| **Workflows** | Create and manage scheduled or triggered workflows |

---

## Built-in Tools

| Tool | Description |
|---|---|
| `web_read` | Fetch a web page as clean markdown (via Jina reader / trafilatura) |
| `web_search` | Search the web via SearXNG self-hosted search engine |
| `api_get` / `api_post` | Make HTTP requests to REST APIs |
| `file_read` / `file_write` | Read/write files in the agent workspace |
| `shell` | Execute shell commands (requires approval by default) |
| `chart` | Generate bar/line/pie/scatter charts as embedded images (matplotlib) |
| `get_datetime` | Get the current date and time |
| `skill` | Invoke a named skill from the workspace skill registry |
| `workflow` | Trigger or query scheduled workflows |

MCP server tools are also available automatically once a server is connected and enabled.

---

## Skills

Skills are markdown-defined capabilities stored in `agent/workspace/skills/<name>/`. Each skill follows the [Anthropic skill spec](https://docs.anthropic.com/):

```
agent/workspace/skills/
  my-skill/
    SKILL.md          ← YAML frontmatter (name, description, tools, …) + instructions
    scripts/          ← optional: executable Python helpers
    references/       ← optional: reference docs loaded into context
    assets/           ← optional: templates, icons, output assets
```

**SKILL.md frontmatter fields:**

```yaml
name: my-skill
description: What this skill does
tools:                    # MCP or built-in tools this skill uses
  - web_read
  - api_get
compatibility: claude-3-5+
metadata:
  triggers:              # phrases that route to this skill
    - "analyse data"
    - "make a chart"
```

### Workspace skills (19 installed)

| Skill | Description |
|---|---|
| `charts` | Generate charts from data |
| `data-analysis` | Analyse tabular data and compute statistics |
| `web-research` | Search the web and fetch URLs for current information |
| `stock-chart` | Fetch historical stock prices and generate line charts |
| `weather` | Get current weather and forecasts |
| `workflow-management` | Create and manage scheduled workflows |
| `skill-creator` | Write new skills following the Anthropic spec |
| `spec-query` | Query project specs and documentation |
| `dde-history` | Query DDE historical data |
| `edwm-wip-movement` | Query EDWM WIP and lot movement data |
| `cim-router` | Route CIM queries to the right system |
| `cim-eda` | Electrical data analysis via CIM |
| `cim-fabrpt` | FAB report generation via CIM |
| `cim-fdc` | Fault detection and classification via CIM |
| `cp-nss` | CP/NSS process queries |
| `fab-ops-analyst` | FAB operations analysis |
| `mcp-builder` | Build and configure MCP servers |
| `issue-case-retriever` | Retrieve issue cases from knowledge base |
| `ocap-navigator` | Navigate OCAP process data |
| `process-flow-expert` | Expert guidance on process flows |

### Skill sync workflow

```
../skills/.agents/skills/    ← external skills repo (Winbond)
         ↓  import_skills
agent/workspace/skills/       ← source of truth in GavinAgent
         ↓  sync_claude_code --skills-only
~/.claude/skills/             ← Claude Code CLI skill resolution
```

---

## MCP Integration

### Using MCP servers

Add and manage MCP servers at **http://127.0.0.1:8000/agent/mcp/**. Two transport types are supported:

- **SSE (remote HTTP)** — connect to a running MCP server via `http://host/sse`
- **stdio** — launch a local MCP server subprocess

Connection status is shown with a colour-coded dot (🟢 connected / 🔴 error / ⚫ disconnected). Errors show the real HTTP status (e.g., `404 Not Found`) rather than the raw asyncio exception.

### GavinAgent as an MCP server

GavinAgent exposes itself as an MCP server so other tools (e.g., Claude Code) can call GavinAgent tools directly:

```bash
uv run mcp-server          # start the MCP server (stdio transport)
```

Register it in Claude Code by running:

```bash
uv run python manage.py sync_claude_code
```

---

## Management Commands

| Command | Description |
|---|---|
| `sync_claude_code` | Sync MCP server config and skills to `~/.claude/` for Claude Code CLI |
| `sync_claude_code --skills-only` | Sync only workspace skills → `~/.claude/skills/` |
| `sync_claude_code --mcp-only` | Sync only MCP servers → `~/.claude.json` |
| `sync_claude_code --dry-run` | Preview changes without writing files |
| `import_skills` | Import skills from `../skills/.agents/skills/` into workspace |
| `import_skills --only name1 name2` | Import specific skills only |
| `import_skills --dry-run` | Preview what would be imported |
| `import_skills --no-sync` | Import without auto-syncing to Claude Code |
| `embed_skills` | Embed all workspace skills into pgvector for semantic routing |
| `ingest_documents` | Ingest documents into the RAG knowledge base |
| `reembed_memory` | Re-embed all memory vectors (e.g., after model change) |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/agent_db` | PostgreSQL connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis broker + result backend |
| `SECRET_KEY` | *(local default)* | Django secret key — **change in production** |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `AZURE_API_KEY` | — | Azure OpenAI API key |
| `AZURE_API_BASE` | — | Azure OpenAI endpoint URL |
| `AZURE_API_VERSION` | — | Azure OpenAI API version |
| `LITELLM_DEFAULT_MODEL` | `openai/gpt-4o-mini` | Default LLM model |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Embedding model (1536-dim) |
| `FERNET_KEY` | — | Fernet key for encrypting MCP server env vars (required for MCP) |
| `FERNET_KEY_PREVIOUS` | — | Previous Fernet key for key rotation |
| `TELEGRAM_BOT_TOKEN` | — | Optional Telegram bot integration |
| `AGENT_WORKSPACE_DIR` | `agent/workspace/` | Path to agent workspace directory |
| `SEARXNG_URL` | `http://localhost:8888` | SearXNG instance URL for `web_search` tool |
| `LANGSMITH_API_KEY` | — | Optional LangSmith tracing |

### Supported LLM models (via LiteLLM)

| Provider | Models |
|---|---|
| **OpenAI** | `gpt-4o`, `gpt-4o-mini`, `o1`, `o3` |
| **Anthropic** | `claude-sonnet-4-6`, `claude-opus-4-6` |
| **Azure OpenAI** | `azure/gpt-4o`, `azure/gpt-4.1`, `azure/gpt-5` |

Set `LITELLM_DEFAULT_MODEL` to any LiteLLM-compatible model string.

---

## Testing

```bash
# Run all unit tests
uv run pytest

# Run a specific test file
uv run pytest tests/agent/test_sync_claude_code.py -v

# Run only tests that don't need the database
uv run pytest -m "not db"
```

Test markers:
- `db` — requires a live PostgreSQL test database
- `external` — mocks external services (LLM, SearXNG, Jina)
- `e2e` — Playwright browser tests (requires a live server)

---

## Project Structure

```
config/          Django settings (base / local / test / production) and Celery app
agent/           Agent orchestration, tools, memory, MCP, skills, workflows
  graph/         LangGraph nodes and state definition
  tools/         Built-in tools (web, file, shell, chart, search, …)
  memory/        Long-term (pgvector) and short-term memory
  mcp/           MCP client, connection pool, registry
  skills/        Skill loader, embeddings, and registry
  workspace/     Markdown workspace files and skills directory
  management/    Management commands (sync_claude_code, import_skills, embed_skills, …)
chat/            Chat conversations, streaming replies, plain LLM interface
core/            Shared LLM client (LiteLLM), embedding utilities, base models
interfaces/      Telegram bot, CLI, heartbeat interfaces
mcp_server.py    GavinAgent MCP server entry point
.doc/            Detailed architecture and design documentation
.spec/           Feature specs (written before implementation, 026 specs total)
tests/           Pytest test suite (unit + e2e)
```

---

## Development Conventions

- **Specs first** — all significant features require a spec in `.spec/NNN-slug.md` before coding
- **Skills** — edit in `agent/workspace/skills/`, never in `~/.claude/skills/` (overwritten on sync)
- **Packages** — use `uv add <pkg>`, never `pip install`
- **Linting** — `uv run ruff check . && uv run ruff format .`
- **Migrations** — always `makemigrations` + `migrate`; no manual DB changes

See [`CLAUDE.md`](CLAUDE.md) for the full AI assistant coding guidelines.

