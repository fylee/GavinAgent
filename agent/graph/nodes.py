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


def _build_system_context(query: str) -> str:
    """Assemble system prompt from workspace files and relevant memories."""
    agents_md = _read_workspace_file("AGENTS.md")
    soul_md = _read_workspace_file("SOUL.md")
    parts = []
    if agents_md:
        parts.append(agents_md)
    if soul_md:
        parts.append(soul_md)

    try:
        from agent.memory.long_term import search_long_term
        excerpts = search_long_term(query, limit=5)
        if excerpts:
            parts.append("## Relevant memories\n\n" + "\n\n".join(excerpts))
    except Exception:
        pass

    return "\n\n---\n\n".join(parts) if parts else "You are a helpful AI assistant."


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
    system_content = _build_system_context(state.get("input", ""))

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

    # Build tool schemas
    tools_schema = [t.to_llm_schema() for t in all_tools().values()]
    tools_schema.extend(skill_registry.to_llm_tools())

    # Fetch run object for LLMUsage tracking
    _run_obj = None
    try:
        from agent.models import AgentRun
        _run_obj = AgentRun.objects.get(pk=state["run_id"])
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
        requires = tool is None or tool.approval_policy == ApprovalPolicy.REQUIRES_APPROVAL
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

    for tc in pending:
        tool_name = tc["name"]
        args = tc.get("arguments", {})
        tc_id = tc["id"]

        te_id = tc.get("tool_execution_id")
        te = None
        if te_id:
            try:
                te = ToolExecution.objects.get(pk=te_id)
            except ToolExecution.DoesNotExist:
                pass

        tool = get_tool(tool_name)
        if tool is None:
            result = {"error": f"Unknown tool: {tool_name}"}
            if te:
                te.status = ToolExecution.Status.ERROR
                te.output = result
                te.save(update_fields=["status", "output"])
        else:
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

        tool_results.append({"tool_call_id": tc_id, "result": result})

    return {"tool_results": tool_results, "pending_tool_calls": []}


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
