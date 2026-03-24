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

        # Embedding match (primary)
        if name in embedding_matches:
            matched = True
        elif not embedding_matches and not triggers and not trigger_patterns:
            # No embeddings at all in DB yet and no keywords — fall back to always-inject for short skills
            matched = len(body.splitlines()) < 50
        else:
            # Keyword/regex fallback — only fires if the embedding did NOT match
            # and the skill has explicit triggers or patterns configured
            matched = False
            if triggers:
                matched = any(t.lower() in query_lower for t in triggers)
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


# Tools whose identity should be based on name + stable key arg only, not full args.
# The LLM often rephrases free-text input between rounds, which produces a different
# hash even though it's logically the same call.
_NAME_ONLY_DEDUP_TOOLS: frozenset[str] = frozenset({
    "run_skill",  # deduplicate by skill_name only (ignore the free-text input)
    "chart",      # deduplicate by title only (ignore cosmetic differences in labels/values)
})


def _tool_sig(tool_name: str, args: dict) -> str:
    """Return a stable dedup signature for a tool call.

    For tools in _NAME_ONLY_DEDUP_TOOLS we use a reduced key so that minor
    argument rephrasing by the LLM doesn't produce a different signature.
    """
    import hashlib as _h
    import json as _j
    if tool_name == "run_skill":
        # Dedup on skill_name only — ignore the free-text input the LLM passes in.
        key = {"skill_name": args.get("skill_name", "")}
    elif tool_name == "chart":
        # Dedup on title only — enough to prevent the same chart being re-generated.
        key = {"title": args.get("title", "")}
    else:
        key = args
    return f"{tool_name}|{_h.md5(_j.dumps(key, sort_keys=True).encode()).hexdigest()}"


# ── nodes ──────────────────────────────────────────────────────────────────


def assemble_context(state: AgentState) -> dict:
    """No-op pass-through. Context assembly is done inside call_llm."""
    return {}


def call_llm(state: AgentState) -> dict:
    """Assemble context and call the LLM. Returns tool calls or final reply."""
    from core.llm import get_completion
    from agent.tools import all_tools
    from agent.skills import registry as skill_registry

    # Cancellation check — if the run was marked FAILED (e.g. by Cancel Run button)
    # while the Celery task was in-flight, abort here before calling the LLM.
    try:
        from agent.models import AgentRun as _AgentRun
        _current = _AgentRun.objects.filter(pk=state["run_id"]).values_list("status", flat=True).first()
        if _current == _AgentRun.Status.FAILED:
            logger.info("AgentRun %s cancelled — aborting call_llm", state["run_id"])
            return {"output": "", "pending_tool_calls": []}
    except Exception:
        pass

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
        # Strip assistant messages that are capability disclaimers or LLM errors —
        # they cause the agent to re-attempt already-completed tasks on every round.
        _error_prefixes = (
            "llm error:",
            "i currently cannot",
            "i can't execute",
            "i can't create",
            "i am unable",
            "i'm unable",
            "since i can't",
            "currently, i'm unable",
            "i cannot execute any further",
            "unfortunately, i'm unable",
            "unfortunately, i cannot",
            "i apologize",
        )
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in chat_msgs
            if not (
                m["role"] == "assistant"
                and any((m["content"] or "").lower().strip().startswith(p) for p in _error_prefixes)
            )
        ]
        # Only keep the last AGENT_HISTORY_WINDOW turns (default 10 messages = ~5 exchanges)
        # to prevent poisoned or irrelevant old history from confusing the agent.
        history_window = getattr(settings, "AGENT_HISTORY_WINDOW", 10)
        if len(history) > history_window:
            history = history[-history_window:]
        history = _truncate_history(history, settings.AGENT_CONTEXT_BUDGET_TOKENS, model)
        messages.extend(history)
    else:
        messages.append({"role": "user", "content": state["input"]})

    # When tool results exist, the preceding assistant message with tool_calls must
    # appear first — otherwise the API rejects the request.
    # Only inject when EVERY tool_call_id in the assistant message has a matching
    # result — a partial match would cause an API error ("tool_call_ids did not have
    # response messages").  Stale state from a prior round is silently skipped.
    tool_results = state.get("tool_results", [])
    assistant_tool_msg = state.get("assistant_tool_call_message")
    if tool_results and assistant_tool_msg:
        required_ids = {tc["id"] for tc in assistant_tool_msg.get("tool_calls", [])}
        result_ids = {tr["tool_call_id"] for tr in tool_results}
        if required_ids and required_ids.issubset(result_ids):
            # All tool calls have results — safe to inject.
            current_results = [tr for tr in tool_results if tr["tool_call_id"] in required_ids]
            messages.append(assistant_tool_msg)
            for tr in current_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": json.dumps(tr["result"]),
                })
        else:
            logger.warning(
                "assemble_context: skipping stale assistant_tool_call_message — "
                "missing results for ids: %s",
                required_ids - result_ids,
            )

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
        # Auto-inject tools declared as required by triggered skills but absent
        # from the agent's enabled_tools list.
        if triggered_skills:
            import yaml as _yaml
            skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
            auto_inject: set[str] = set()
            for skill_name in triggered_skills:
                skill_md = skills_dir / skill_name / "SKILL.md"
                if skill_md.exists():
                    try:
                        raw = skill_md.read_text(encoding="utf-8")
                        if raw.startswith("---"):
                            parts = raw.split("---", 2)
                            if len(parts) >= 3:
                                meta = _yaml.safe_load(parts[1]) or {}
                                for t in meta.get("tools", []):
                                    if t not in enabled_tools:
                                        auto_inject.add(t)
                    except Exception:
                        pass
                # run_skill is always needed when a handler exists
                if (skills_dir / skill_name / "handler.py").exists():
                    if "run_skill" not in enabled_tools:
                        auto_inject.add("run_skill")
            for tool_name in auto_inject:
                if tool_name in all_builtin:
                    tools_schema.append(all_builtin[tool_name].to_llm_schema())
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
            # Clear stale results from the previous round so they don't cause an
            # ID mismatch when call_llm is invoked again after execute_tools.
            "tool_results": [],
        }

    return {"output": message.content or "", "pending_tool_calls": [], "assistant_tool_call_message": None, "tool_results": []}


def check_approval(state: AgentState) -> dict:
    """Check each pending tool call against its approval policy."""
    from agent.tools import get_tool
    from agent.tools.base import ApprovalPolicy
    from agent.models import AgentRun, ToolExecution

    pending = state.get("pending_tool_calls", [])
    run = AgentRun.objects.get(pk=state["run_id"])

    failed_sigs = list(state.get("failed_tool_signatures") or [])
    succeeded_sigs = list(state.get("succeeded_tool_signatures") or [])

    # Pre-filter: drop any tool calls that already succeeded or failed this run.
    # This prevents ToolExecution rows being created for calls we'll never execute.
    filtered_pending = []
    dropped_results = []
    for tc in pending:
        sig = _tool_sig(tc["name"], tc.get("arguments", {}))
        if sig in succeeded_sigs:
            dropped_results.append({
                "tool_call_id": tc["id"],
                "result": {"error": f"Tool '{tc['name']}' already ran successfully with these arguments this session. Do not call it again — use the previous result to compose your final answer."},
            })
        elif sig in failed_sigs:
            dropped_results.append({
                "tool_call_id": tc["id"],
                "result": {"error": f"Tool '{tc['name']}' already failed with these arguments. Do not retry — use a different approach or report the error."},
            })
        else:
            filtered_pending.append(tc)

    # If ALL calls were dropped, skip execution entirely and feed results back to LLM.
    if dropped_results and not filtered_pending:
        return {
            "pending_tool_calls": [],
            "tool_results": dropped_results,
            "waiting_for_approval": False,
        }

    # Replace pending with the filtered list for the rest of approval logic.
    # dropped_results will be returned alongside pending_tool_calls so that
    # execute_tools can merge them with the results it actually runs.
    pending = filtered_pending

    needs_approval = []
    auto_execute = []

    for tc in pending:
        tool_name = tc["name"]
        tool = get_tool(tool_name)
        if tool is not None:
            # Allow per-call approval logic (e.g. file_write only requires approval for workflows/)
            if hasattr(tool, "requires_approval_for"):
                requires = tool.requires_approval_for(tc.get("arguments", {}))
            else:
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
            "failed_tool_signatures": state.get("failed_tool_signatures") or [],
            "succeeded_tool_signatures": state.get("succeeded_tool_signatures") or [],
        }
        run.save(update_fields=["status", "graph_state"])
        return {
            "pending_tool_calls": needs_approval,
            "waiting_for_approval": True,
        }

    return {
        "pending_tool_calls": auto_execute,
        "waiting_for_approval": False,
        # Pre-populate tool_results with responses for any calls that were dropped
        # (already succeeded/failed); execute_tools will merge its own results in.
        "tool_results": dropped_results,
    }


def execute_tools(state: AgentState) -> dict:
    """Execute all pending tool calls and collect results."""
    from agent.tools import get_tool
    from agent.tools.base import ToolTimeoutError
    from agent.models import AgentRun, ToolExecution

    # Cancellation check — abort before running any tools if run was cancelled.
    try:
        _status = AgentRun.objects.filter(pk=state["run_id"]).values_list("status", flat=True).first()
        if _status == AgentRun.Status.FAILED:
            logger.info("AgentRun %s cancelled — aborting execute_tools", state["run_id"])
            return {"tool_results": [], "pending_tool_calls": [], "tool_call_rounds": state.get("tool_call_rounds", 0) + 1, "visited_urls": list(state.get("visited_urls") or []), "failed_tool_signatures": list(state.get("failed_tool_signatures") or [])}
    except Exception:
        pass

    import hashlib as _hashlib
    import json as _json

    pending = state.get("pending_tool_calls", [])
    # Pre-populated by check_approval for any calls that were dropped (already succeeded/failed).
    tool_results = list(state.get("tool_results") or [])
    visited_urls = list(state.get("visited_urls") or [])
    failed_sigs = list(state.get("failed_tool_signatures") or [])
    succeeded_sigs = list(state.get("succeeded_tool_signatures") or [])

    for tc in pending:
        tool_name = tc["name"]
        args = tc.get("arguments", {})

        # Block duplicate URL fetches across ALL url-based tools (web_read, api_get, api_post).
        # A URL already fetched by any tool should not be re-fetched by another.
        if tool_name in ("web_read", "api_get", "api_post"):
            url = args.get("url", "")
            if url and url in visited_urls:
                tc_id = tc["id"]
                tool_results.append({
                    "tool_call_id": tc_id,
                    "result": {"error": f"Already fetched {url} — use the content from the previous call. Do not retry this URL with any tool."},
                })
                continue
            if url:
                visited_urls.append(url)

        # Block retrying a tool call that already failed with the same arguments
        sig = _tool_sig(tool_name, args)
        if sig in failed_sigs:
            tc_id = tc["id"]
            tool_results.append({
                "tool_call_id": tc_id,
                "result": {"error": f"Tool '{tool_name}' already failed with these arguments. Do not retry — use a different approach or report the error."},
            })
            continue
        if sig in succeeded_sigs:
            tc_id = tc["id"]
            tool_results.append({
                "tool_call_id": tc_id,
                "result": {"error": f"Tool '{tool_name}' already ran successfully with these arguments this session. Do not call it again — use the previous result to complete your response."},
            })
            continue
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
        if result.get("error"):
            failed_sigs.append(sig)
        else:
            succeeded_sigs.append(sig)

    rounds = state.get("tool_call_rounds", 0) + 1
    return {
        "tool_results": tool_results,
        "pending_tool_calls": [],
        "tool_call_rounds": rounds,
        "visited_urls": visited_urls,
        "failed_tool_signatures": failed_sigs,
        "succeeded_tool_signatures": succeeded_sigs,
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

    # Don't overwrite a cancellation — if already FAILED, leave it as-is.
    if run.status == AgentRun.Status.FAILED:
        return {}

    if output and state.get("conversation_id"):
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
