from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from agent.graph.state import AgentState

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────


def _read_workspace_file(relative: str) -> str:
    path = Path(settings.AGENT_WORKSPACE_DIR) / relative
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _count_tokens(messages: list[dict], model: str) -> int:
    try:
        import litellm
        return litellm.token_counter(model=model, messages=messages)
    except Exception:
        return sum(len(m.get("content", "") or "") for m in messages) // 4


def _truncate_history(history: list[dict], budget_tokens: int, model: str) -> list[dict]:
    """Drop oldest messages until within budget."""
    while history and _count_tokens(history, model) > budget_tokens:
        history = history[1:]
    return history


def _build_skills_section(query: str) -> tuple[str, list[str]]:
    """Scan workspace/skills/, match against query using embeddings (with keyword fallback).

    Returns (system_prompt_section, triggered_skill_names).
    """
    import re
    import yaml
    from agent.skills.embeddings import find_relevant_skills

    skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
    if not skills_dir.exists():
        return "", []

    query_lower = query.lower()

    # Embedding-based routing (primary)
    embedding_matches: set[str] = set(find_relevant_skills(query))

    index_rows: list[str] = []
    body_sections: list[str] = []
    triggered: list[str] = []

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        text = skill_md.read_text(encoding="utf-8")
        meta: dict = {}
        body = text
        if text.startswith("---"):
            raw_parts = text.split("---", 2)
            if len(raw_parts) >= 3:
                meta = yaml.safe_load(raw_parts[1]) or {}
                body = raw_parts[2].strip()

        name = meta.get("name", skill_dir.name)
        description = meta.get("description", "")
        triggers: list[str] = meta.get("triggers", [])
        trigger_patterns: list[str] = meta.get("trigger_patterns", [])

        # Embedding match (primary) — keyword/regex fallback if no embedding exists
        if name in embedding_matches:
            matched = True
        elif embedding_matches is not None and not embedding_matches and not triggers and not trigger_patterns:
            # No embeddings at all in DB yet — fall back to always-inject for short skills
            matched = len(body.splitlines()) < 50
        else:
            # Keyword fallback
            matched = any(t.lower() in query_lower for t in triggers) if triggers else False
            if not matched and trigger_patterns:
                matched = any(re.search(p, query_lower) for p in trigger_patterns)

        status = "**Active**" if matched else "Available"
        index_rows.append(f"| {name} | {description} | {status} |")

        if matched:
            triggered.append(name)
            heading = f"### {name}"
            if description:
                heading += f"\n{description}"
            body_sections.append(f"{heading}\n\n{body}")

    if not index_rows:
        return "", []

    index = (
        "## Skills\n\n"
        "Full instructions are injected for skills relevant to this task.\n\n"
        "| Skill | Description | Status |\n"
        "|-------|-------------|--------|\n"
        + "\n".join(index_rows)
    )

    if body_sections:
        section = index + "\n\n---\n\n" + "\n\n---\n\n".join(body_sections)
    else:
        section = index

    return section, triggered


def _build_system_context(query: str) -> tuple[str, list[str]]:
    """Assemble system prompt from workspace files, memories, and MCP resources.

    Returns (system_prompt, triggered_skill_names).
    """
    from datetime import datetime
    import zoneinfo
    agent_tz = zoneinfo.ZoneInfo(getattr(settings, "AGENT_TIMEZONE", "UTC"))
    now = datetime.now(agent_tz)
    temporal_context = (
        f"Current date and time: {now.strftime('%Y-%m-%d %H:%M:%S')} {settings.AGENT_TIMEZONE} "
        f"({now.strftime('%A')})\n"
        f"When writing workflow cron expressions, use timezone: {settings.AGENT_TIMEZONE}"
    )

    agents_md = _read_workspace_file("AGENTS.md")
    soul_md = _read_workspace_file("SOUL.md")
    parts = [temporal_context]
    if agents_md:
        parts.append(agents_md)
    if soul_md:
        parts.append(soul_md)

    skills_section, triggered = _build_skills_section(query)
    if skills_section:
        parts.append(skills_section)

    try:
        from agent.memory.long_term import search_long_term
        excerpts = search_long_term(query, limit=5)
        if excerpts:
            parts.append("## Relevant memories\n\n" + "\n\n".join(excerpts))
    except Exception:
        pass

    try:
        from agent.mcp.pool import MCPConnectionPool
        resources = MCPConnectionPool.get().fetch_always_include_resources()
        if resources:
            parts.append("## MCP Resources\n\n" + "\n\n".join(resources))
    except Exception:
        pass

    content = "\n\n---\n\n".join(parts) if parts else "You are a helpful AI assistant."
    return content, triggered


def _get_agent_model(agent_id: str) -> str:
    from agent.models import Agent
    try:
        agent = Agent.objects.get(pk=agent_id)
        return agent.model or settings.LITELLM_DEFAULT_MODEL
    except Agent.DoesNotExist:
        return settings.LITELLM_DEFAULT_MODEL


# ── nodes ──────────────────────────────────────────────────────────────────


def assemble_context(state: AgentState) -> dict:
    """No-op pass-through. Context assembly is done inside call_llm."""
    return {}


def call_llm(state: AgentState) -> dict:
    """Assemble context and call the LLM. Returns tool calls or final reply."""
    from core.llm import get_completion
    from agent.tools import all_tools
    from agent.skills import registry as skill_registry

    model = _get_agent_model(state["agent_id"])
    system_content, triggered_skills = _build_system_context(state.get("input", ""))

    # Append conversation_id to system prompt so agent can reference it in workflows
    conversation_id = state.get("conversation_id")
    if conversation_id:
        system_content += f"\n\n---\n\nCurrent conversation ID: `{conversation_id}`"

    # Build message list
    messages: list[dict] = [{"role": "system", "content": system_content}]

    if state.get("conversation_id"):
        from chat.models import Message as ChatMessage
        chat_msgs = list(
            ChatMessage.objects.filter(conversation_id=state["conversation_id"])
            .order_by("created_at")
            .values("role", "content")
        )
        history = [{"role": m["role"], "content": m["content"]} for m in chat_msgs]
        history = _truncate_history(history, settings.AGENT_CONTEXT_BUDGET_TOKENS, model)
        messages.extend(history)
    else:
        messages.append({"role": "user", "content": state["input"]})

    # When tool results exist, the preceding assistant message with tool_calls must
    # appear first — otherwise the API rejects the request.
    # Only include results whose tool_call_id appears in the current assistant
    # message — previous rounds' results have IDs from a different assistant
    # message and would cause an API error.
    tool_results = state.get("tool_results", [])
    assistant_tool_msg = state.get("assistant_tool_call_message")
    if tool_results and assistant_tool_msg:
        valid_ids = {tc["id"] for tc in assistant_tool_msg.get("tool_calls", [])}
        current_results = [tr for tr in tool_results if tr["tool_call_id"] in valid_ids]
        if current_results:
            messages.append(assistant_tool_msg)
            for tr in current_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": json.dumps(tr["result"]),
                })

    # Build tool schemas — filtered to only tools enabled on this agent
    from agent.models import Agent as AgentModel
    try:
        agent_obj = AgentModel.objects.get(pk=state["agent_id"])
        enabled_tools: list[str] = agent_obj.tools or []
    except AgentModel.DoesNotExist:
        enabled_tools = []

    all_builtin = all_tools()
    if enabled_tools:
        tools_schema = [
            t.to_llm_schema()
            for name, t in all_builtin.items()
            if name in enabled_tools
        ]
    else:
        tools_schema = []

    tools_schema.extend(skill_registry.to_llm_tools())
    try:
        from agent.mcp.registry import get_registry as get_mcp_registry
        tools_schema.extend(get_mcp_registry().to_llm_schemas())
    except Exception:
        pass

    # Fetch run object for LLMUsage tracking + triggered_skills
    _run_obj = None
    try:
        from agent.models import AgentRun
        _run_obj = AgentRun.objects.get(pk=state["run_id"])
        if triggered_skills:
            AgentRun.objects.filter(pk=state["run_id"]).update(triggered_skills=triggered_skills)
    except Exception:
        pass

    try:
        response = get_completion(
            messages,
            model=model,
            source="agent",
            run=_run_obj,
            tools=tools_schema if tools_schema else None,
        )
    except Exception as exc:
        logger.exception("LLM call failed in AgentRun %s: %s", state.get("run_id"), exc)
        return {"output": f"LLM error: {exc}", "pending_tool_calls": []}

    choice = response.choices[0]
    message = choice.message

    if message.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments or "{}"),
            }
            for tc in message.tool_calls
        ]
        # Preserve the full assistant message so it can precede tool results next round.
        assistant_tool_call_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in message.tool_calls
            ],
        }
        return {
            "pending_tool_calls": tool_calls,
            "assistant_tool_call_message": assistant_tool_call_message,
        }

    return {"output": message.content or "", "pending_tool_calls": [], "assistant_tool_call_message": None}


def check_approval(state: AgentState) -> dict:
    """Check each pending tool call against its approval policy."""
    from agent.tools import get_tool
    from agent.tools.base import ApprovalPolicy
    from agent.models import AgentRun, ToolExecution

    pending = state.get("pending_tool_calls", [])
    run = AgentRun.objects.get(pk=state["run_id"])

    needs_approval = []
    auto_execute = []

    for tc in pending:
        tool_name = tc["name"]
        tool = get_tool(tool_name)
        if tool is not None:
            requires = tool.approval_policy == ApprovalPolicy.REQUIRES_APPROVAL
        else:
            # Check MCP registry
            try:
                from agent.mcp.registry import get_registry as get_mcp_registry
                from agent.models import MCPServer
                mcp_entry = get_mcp_registry().get(tool_name)
                if mcp_entry:
                    server = MCPServer.objects.filter(name=mcp_entry.server_name).first()
                    requires = server is None or mcp_entry.tool_name not in (server.auto_approve_tools or [])
                else:
                    requires = True  # unknown tool → always require approval
            except Exception:
                requires = True
        if requires:
            te = ToolExecution.objects.create(
                run=run,
                tool_name=tool_name,
                input=tc.get("arguments", {}),
                status=ToolExecution.Status.PENDING,
                requires_approval=True,
            )
            needs_approval.append({**tc, "tool_execution_id": str(te.id)})
        else:
            # Create an audit record for auto-approved tools too so they appear in the run trace.
            te = ToolExecution.objects.create(
                run=run,
                tool_name=tool_name,
                input=tc.get("arguments", {}),
                status=ToolExecution.Status.RUNNING,
                requires_approval=False,
            )
            auto_execute.append({**tc, "tool_execution_id": str(te.id)})

    if needs_approval:
        run.status = AgentRun.Status.WAITING
        run.graph_state = {
            "pending_tool_calls": needs_approval,
            "tool_results": [],
            "assistant_tool_call_message": state.get("assistant_tool_call_message"),
        }
        run.save(update_fields=["status", "graph_state"])
        return {
            "pending_tool_calls": needs_approval,
            "waiting_for_approval": True,
        }

    return {
        "pending_tool_calls": auto_execute,
        "waiting_for_approval": False,
    }


def execute_tools(state: AgentState) -> dict:
    """Execute all pending tool calls and collect results."""
    from agent.tools import get_tool
    from agent.tools.base import ToolTimeoutError
    from agent.models import AgentRun, ToolExecution

    pending = state.get("pending_tool_calls", [])
    tool_results = []
    visited_urls = list(state.get("visited_urls") or [])

    for tc in pending:
        tool_name = tc["name"]
        args = tc.get("arguments", {})

        # Block duplicate web_read calls for the same URL
        if tool_name == "web_read":
            url = args.get("url", "")
            if url in visited_urls:
                tc_id = tc["id"]
                tool_results.append({
                    "tool_call_id": tc_id,
                    "result": {"error": f"Already read {url} — use the content from the previous call."},
                })
                continue
            visited_urls.append(url)
        tc_id = tc["id"]

        te_id = tc.get("tool_execution_id")
        te = None
        if te_id:
            try:
                te = ToolExecution.objects.get(pk=te_id)
            except ToolExecution.DoesNotExist:
                pass

        tool = get_tool(tool_name)
        if tool is not None:
            # Built-in tool
            if te:
                te.status = ToolExecution.Status.RUNNING
                te.save(update_fields=["status"])
            try:
                tool_result = tool.execute(**args)
                result = tool_result.as_dict()
                if te:
                    te.status = (
                        ToolExecution.Status.SUCCESS
                        if tool_result.success
                        else ToolExecution.Status.ERROR
                    )
                    te.output = result
                    te.duration_ms = tool_result.duration_ms
                    te.save(update_fields=["status", "output", "duration_ms"])
            except ToolTimeoutError as exc:
                result = {"error": str(exc)}
                if te:
                    te.status = ToolExecution.Status.ERROR
                    te.output = result
                    te.save(update_fields=["status", "output"])
        else:
            # Try MCP registry
            try:
                from agent.mcp.registry import get_registry as get_mcp_registry
                from agent.mcp.pool import MCPConnectionPool
                from agent.mcp.client import MCPTimeoutError as MCPTimeout
                import time as _time
                mcp_entry = get_mcp_registry().get(tool_name)
                if mcp_entry:
                    if te:
                        te.status = ToolExecution.Status.RUNNING
                        te.save(update_fields=["status"])
                    start = _time.monotonic()
                    try:
                        mcp_result = MCPConnectionPool.get().call_tool(
                            mcp_entry.server_name, mcp_entry.tool_name, args
                        )
                        result = {"output": mcp_result}
                        duration = int((_time.monotonic() - start) * 1000)
                        if te:
                            te.status = ToolExecution.Status.SUCCESS
                            te.output = result
                            te.duration_ms = duration
                            te.save(update_fields=["status", "output", "duration_ms"])
                    except MCPTimeout as exc:
                        result = {"error": str(exc)}
                        if te:
                            te.status = ToolExecution.Status.ERROR
                            te.output = result
                            te.save(update_fields=["status", "output"])
                else:
                    result = {"error": f"Unknown tool: {tool_name}"}
                    if te:
                        te.status = ToolExecution.Status.ERROR
                        te.output = result
                        te.save(update_fields=["status", "output"])
            except Exception as exc:
                result = {"error": f"Tool execution error: {exc}"}
                if te:
                    te.status = ToolExecution.Status.ERROR
                    te.output = result
                    te.save(update_fields=["status", "output"])

        tool_results.append({"tool_call_id": tc_id, "result": result})

    rounds = state.get("tool_call_rounds", 0) + 1
    return {
        "tool_results": tool_results,
        "pending_tool_calls": [],
        "tool_call_rounds": rounds,
        "visited_urls": visited_urls,
    }


def force_conclude(state: AgentState) -> dict:
    """Called when tool_call_rounds hits the limit. Ask the LLM to conclude with what it has."""
    from core.llm import get_completion
    from agent.models import AgentRun

    model = _get_agent_model(state["agent_id"])
    system_content, _ = _build_system_context(state.get("input", ""))

    messages: list[dict] = [{"role": "system", "content": system_content}]
    if state.get("conversation_id"):
        from chat.models import Message as ChatMessage
        chat_msgs = list(
            ChatMessage.objects.filter(conversation_id=state["conversation_id"])
            .order_by("created_at")
            .values("role", "content")
        )
        history = [{"role": m["role"], "content": m["content"]} for m in chat_msgs]
        history = _truncate_history(history, settings.AGENT_CONTEXT_BUDGET_TOKENS, model)
        messages.extend(history)
    else:
        messages.append({"role": "user", "content": state["input"]})

    messages.append({
        "role": "user",
        "content": (
            "You have reached the maximum number of tool-use rounds. "
            "Based on all the information you have collected so far, "
            "provide the best possible answer to the original request. "
            "If data is incomplete, state what you found and what is missing."
        ),
    })

    _run_obj = None
    try:
        from agent.models import AgentRun
        _run_obj = AgentRun.objects.get(pk=state["run_id"])
    except Exception:
        pass

    try:
        response = get_completion(messages, model=model, source="agent", run=_run_obj)
        output = response.choices[0].message.content or ""
    except Exception as exc:
        output = f"Reached tool-use limit. Error generating summary: {exc}"

    return {"output": output, "pending_tool_calls": []}


def save_result(state: AgentState) -> dict:
    """Save the final output as a chat.Message and mark AgentRun completed."""
    from chat.models import Message as ChatMessage
    from agent.models import AgentRun

    output = state.get("output", "")
    run = AgentRun.objects.get(pk=state["run_id"])

    if state.get("conversation_id"):
        ChatMessage.objects.create(
            conversation_id=state["conversation_id"],
            role=ChatMessage.Role.ASSISTANT,
            content=output,
        )

    run.output = output
    run.status = AgentRun.Status.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["output", "status", "finished_at"])

    return {}
