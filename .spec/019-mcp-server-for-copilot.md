# 019 — MCP Server for GitHub Copilot

## Goal

Expose this project's Agent as an MCP tool so that GitHub Copilot (and any
other MCP-capable client) can invoke it directly from the IDE via a single
`ask_agent` tool call.

## Background

GitHub Copilot Agent mode supports MCP servers via `.vscode/mcp.json`.
This project is currently an MCP **client** (it connects to external MCP
servers). Implementing a thin MCP server layer lets Copilot treat GavinAgent
as a tool: Copilot sends a natural-language task, the agent runs to completion,
and the result is returned as tool output.

## Proposed Solution

Add `mcp_server.py` at the project root. It:

1. Starts a standard `mcp` SDK server (`FastMCP`) with **stdio transport**
   (simplest; no network port needed for local Copilot usage).
2. Exposes one tool: **`ask_agent`** — submits a task to the default agent
   and polls until `COMPLETED` or `FAILED`, then returns the output.
3. Exposes one optional tool: **`list_agents`** — returns available agents.
4. Uses Django's ORM directly (sets `DJANGO_SETTINGS_MODULE` before import),
   so it runs inside the same venv with no extra service.

`.vscode/mcp.json` points Copilot to this server via `uv run mcp_server`.

## Architecture

```
GitHub Copilot (Agent mode)
        │  MCP stdio transport
        ▼
mcp_server.py  (FastMCP server, stdio)
        │  Django ORM + AgentRunner.enqueue()
        ▼
AgentRun  ──► Celery worker ──► LangGraph
        │
        └── poll AgentRun.status until COMPLETED
        │
        └── return AgentRun.output
```

## Out of Scope

- SSE / HTTP transport (stdio is sufficient for local IDE use)
- Streaming partial output to Copilot during execution
- Per-tool approval flow (approval is handled inside the agent; MCP call blocks until resolved or timeout)
- Authentication (local stdio, no network exposure)

## Open Questions

- Timeout: default 5 minutes; configurable via `AGENT_MCP_TIMEOUT_SECONDS` env var.
- Which agent to use: always the `is_default=True` agent, or let caller specify?
  → For now: default agent, but `ask_agent` accepts an optional `agent_name` param.
