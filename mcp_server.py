"""
GavinAgent MCP Server

Exposes GavinAgent capabilities as a stdio MCP server so external tools
(Claude Code, other MCP clients) can submit tasks, list agents/skills, and
run skill handlers directly.

Usage:
    uv run python mcp_server.py
"""

from __future__ import annotations

import os
import sys
import time

# ── Protect the MCP stdio transport ────────────────────────────────────────
# The MCP stdio transport reads sys.stdin.buffer and writes sys.stdout.buffer
# (the underlying binary streams), so Python-level redirection of sys.stdout
# is not sufficient.  We redirect at the OS file-descriptor level instead:
#
#   1. Flush and duplicate fd 1 (stdout) so we can restore it later.
#   2. Point fd 1 → fd 2 (stderr) for the duration of django.setup().
#      Any stray write to stdout — print(), logging, C extensions — now goes
#      to stderr and cannot corrupt the JSON-RPC protocol stream.
#   3. Restore fd 1 after setup so mcp.run() has a clean stdout.
#
# Note: the \n parse errors visible when running from a PowerShell terminal
# come from the Windows console injecting an empty line into the asyncio
# stdin reader.  This does NOT happen when Claude's desktop spawns the server
# via a pipe, so no stdin filtering is required here.
sys.stdout.flush()
sys.stderr.flush()
_saved_stdout_fd = os.dup(1)   # duplicate the real stdout fd
os.dup2(2, 1)                   # redirect fd 1 → stderr for setup

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
# Signal to AppConfig.ready() that we're running as an MCP server so it can
# skip expensive startup work (skill DB sync, embedding) that would cause the
# MCP client to time out before mcp.run() is reached.
os.environ["GAVIN_MCP_SERVER"] = "1"

import django  # noqa: E402
django.setup()

# Restore the real stdout fd before the MCP transport opens its streams.
# Flush sys.stderr (currently aliased as sys.stdout) before switching back.
sys.stdout.flush()
os.dup2(_saved_stdout_fd, 1)  # restore fd 1 → real stdout
os.close(_saved_stdout_fd)
# Restore the original Python stdout wrapper — it still wraps fd 1, which
# now points at the real stdout again.  No os.fdopen needed (unreliable on
# Windows) because _real_stdout.buffer already targets the correct fd.
sys.stdout = _real_stdout

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("GavinAgent")

# ── ask_agent ──────────────────────────────────────────────────────────────


@mcp.tool()
def ask_agent(
    task: str,
    agent_name: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    """Submit a task to GavinAgent and wait for the result.

    Args:
        task: The task or question to send to the agent.
        agent_name: Name of the agent to use. Defaults to the is_default agent.
        timeout_seconds: How long to wait for completion (default: AGENT_MCP_TIMEOUT_SECONDS, 300 s).

    Returns:
        {"output": str} on success, {"error": str} on failure or timeout.
    """
    from django.conf import settings
    from agent.models import Agent, AgentRun
    from agent.runner import AgentRunner

    if timeout_seconds is None:
        timeout_seconds = getattr(settings, "AGENT_MCP_TIMEOUT_SECONDS", 300)

    # Resolve agent
    try:
        if agent_name:
            agent = Agent.objects.get(name=agent_name, is_active=True)
        else:
            agent = (
                Agent.objects.filter(is_active=True, is_default=True).first()
                or Agent.objects.filter(is_active=True).first()
            )
        if agent is None:
            return {"error": "No active agent found."}
    except Agent.DoesNotExist:
        return {"error": f"Agent '{agent_name}' not found or inactive."}

    run = AgentRun.objects.create(
        agent=agent,
        trigger_source=AgentRun.TriggerSource.CLI,
        input=task,
    )
    AgentRunner.enqueue(run)

    # Poll until terminal state or timeout
    terminal = {AgentRun.Status.COMPLETED, AgentRun.Status.FAILED}
    deadline = time.monotonic() + timeout_seconds
    poll_interval = 1.0

    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 5.0)  # back off up to 5 s
        run.refresh_from_db()
        if run.status in terminal:
            break

    run_id = str(run.id)
    if run.status == AgentRun.Status.COMPLETED:
        return {"output": run.output or ""}
    if run.status == AgentRun.Status.FAILED:
        return {"error": f"{run.error or 'Agent run failed.'} (run_id: {run_id})"}
    return {"error": f"Timed out after {timeout_seconds} s (status: {run.status}, run_id: {run_id})"}


# ── list_agents ────────────────────────────────────────────────────────────


@mcp.tool()
def list_agents() -> list[dict]:
    """List all active agents.

    Returns:
        List of {name, description, model, is_default}.
    """
    from agent.models import Agent

    return [
        {
            "name": a.name,
            "description": a.description,
            "model": a.model,
            "is_default": a.is_default,
        }
        for a in Agent.objects.filter(is_active=True).order_by("name")
    ]


# ── list_skills ────────────────────────────────────────────────────────────


@mcp.tool()
def list_skills() -> list[dict]:
    """List all enabled skills.

    Returns:
        List of {name, description}.
    """
    from agent.models import Skill

    return [
        {"name": s.name, "description": s.description}
        for s in Skill.objects.filter(enabled=True).order_by("name")
    ]


# ── run_skill ──────────────────────────────────────────────────────────────


@mcp.tool()
def run_skill(skill_name: str, input: str) -> dict:
    """Execute a skill handler directly by name.

    Only works for skills that have a handler.py in their skill directory.

    Args:
        skill_name: The skill directory name (e.g. "weather", "charts").
        input: Input string passed to the skill's handle() function.

    Returns:
        {"result": str} on success, {"error": str} on failure.
    """
    from agent.tools.skill import RunSkillTool

    tool_result = RunSkillTool().execute(skill_name=skill_name, input=input)
    if tool_result.success:
        output = tool_result.output or {}
        return {"result": output.get("result", "")}
    return {"error": tool_result.error or "Skill execution failed."}


# ── entry point ────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
