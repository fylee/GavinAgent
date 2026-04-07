#!/usr/bin/env python
"""
GavinAgent MCP Server
=====================
Exposes this project's Agent as an MCP tool so that GitHub Copilot
(and any other MCP-capable client) can invoke it from the IDE.

Usage (stdio transport — default for Copilot):
    uv run mcp_server

The server registers two tools:
  • ask_agent   — submit a task and wait for the result
  • list_agents — list available agents
"""
from __future__ import annotations

import os
import time

# ── Bootstrap Django ──────────────────────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

# ── MCP server ────────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    name="gavin-agent",
    instructions=(
        "GavinAgent is a general-purpose AI agent. "
        "Use ask_agent to delegate tasks that require web search, file access, "
        "code execution, or data analysis. "
        "The agent runs asynchronously; this tool blocks until completion."
    ),
)

_DEFAULT_TIMEOUT = int(os.environ.get("AGENT_MCP_TIMEOUT_SECONDS", 300))
_POLL_INTERVAL = 2  # seconds


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_agent(agent_name: str | None):
    from agent.models import Agent

    if agent_name:
        return Agent.objects.filter(name__iexact=agent_name, is_active=True).first()
    return Agent.objects.filter(is_default=True, is_active=True).first()


def _create_run(agent, task: str):
    from agent.models import AgentRun
    from agent.runner import AgentRunner

    run = AgentRun.objects.create(
        agent=agent,
        trigger_source=AgentRun.TriggerSource.CLI,
        input=task,
    )
    AgentRunner.enqueue(run)
    return run


def _poll_run(run_id: str, timeout: int) -> tuple[str, str | None]:
    """
    Poll until run reaches a terminal state.
    Returns (status, output_or_error).
    """
    from agent.models import AgentRun

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = AgentRun.objects.only("status", "output", "error").get(id=run_id)
        if run.status in (AgentRun.Status.COMPLETED, AgentRun.Status.FAILED):
            return run.status, run.output or run.error
        time.sleep(_POLL_INTERVAL)

    return "timeout", f"Agent did not complete within {timeout}s."


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def ask_agent(
    task: str,
    agent_name: str | None = None,
    timeout_seconds: int | None = None,
) -> str:
    """
    Submit a task to GavinAgent and return the result.

    The agent can search the web, read/write files in its workspace,
    execute shell commands, call external APIs, and use connected MCP tools.

    Args:
        task:            Natural-language description of what to do.
        agent_name:      Name of the agent to use (default: the default agent).
        timeout_seconds: Max seconds to wait (default: 300).

    Returns:
        The agent's final answer, or an error message.
    """
    timeout = timeout_seconds or _DEFAULT_TIMEOUT

    agent = _get_agent(agent_name)
    if agent is None:
        name_hint = f" named '{agent_name}'" if agent_name else " (is_default=True)"
        return f"Error: no active agent found{name_hint}. Check the Django admin."

    run = _create_run(agent, task)
    status, result = _poll_run(str(run.id), timeout)

    if status == "timeout":
        return f"Timeout: agent did not finish within {timeout}s. Run ID: {run.id}"
    if status == "failed":
        return f"Agent failed: {result}\nRun ID: {run.id}"

    return result or "(Agent returned no output.)"


@mcp.tool()
def list_agents() -> str:
    """
    List all active agents in GavinAgent with their names and descriptions.

    Returns:
        A formatted list of available agents.
    """
    from agent.models import Agent

    agents = Agent.objects.filter(is_active=True).order_by("name").values("name", "description", "is_default")
    if not agents:
        return "No active agents found."

    lines = []
    for a in agents:
        default_tag = " [default]" if a["is_default"] else ""
        desc = a["description"] or "(no description)"
        lines.append(f"• {a['name']}{default_tag} — {desc}")
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
