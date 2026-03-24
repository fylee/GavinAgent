from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph

from agent.graph.nodes import (
    assemble_context,
    call_llm,
    check_approval,
    execute_tools,
    force_conclude,
    save_result,
)
from agent.graph.state import AgentState


# ── Routing functions ───────────────────────────────────────────────────────


def _after_call_llm(state: AgentState) -> str:
    if state.get("pending_tool_calls"):
        return "check_approval"
    return "save_result"


def _after_check_approval(state: AgentState) -> str:
    if state.get("waiting_for_approval"):
        return END  # pause until human approves
    if not state.get("pending_tool_calls"):
        # All tool calls were pre-filtered (already succeeded/failed) — skip execute_tools
        # and go straight to call_llm so the LLM sees the "already completed" responses.
        return "call_llm"
    return "execute_tools"


def _after_execute_tools(state: AgentState) -> str:
    from django.conf import settings
    max_rounds = getattr(settings, "AGENT_MAX_TOOL_CALL_ROUNDS", 20)
    if state.get("tool_call_rounds", 0) >= max_rounds:
        return "force_conclude"
    return "call_llm"


# ── Graph builder ───────────────────────────────────────────────────────────


def build_graph() -> Any:
    """Build and compile the agent StateGraph."""
    graph = StateGraph(AgentState)

    graph.add_node("assemble_context", assemble_context)
    graph.add_node("call_llm", call_llm)
    graph.add_node("check_approval", check_approval)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("force_conclude", force_conclude)
    graph.add_node("save_result", save_result)

    graph.set_entry_point("assemble_context")
    graph.add_edge("assemble_context", "call_llm")
    graph.add_conditional_edges(
        "call_llm",
        _after_call_llm,
        {"check_approval": "check_approval", "save_result": "save_result"},
    )
    graph.add_conditional_edges(
        "check_approval",
        _after_check_approval,
        {"execute_tools": "execute_tools", "call_llm": "call_llm", END: END},
    )
    graph.add_conditional_edges(
        "execute_tools",
        _after_execute_tools,
        {"call_llm": "call_llm", "force_conclude": "force_conclude"},
    )
    graph.add_edge("force_conclude", "save_result")
    graph.add_edge("save_result", END)

    return graph.compile()


# ── DjangoCheckpointer ──────────────────────────────────────────────────────


class DjangoCheckpointer:
    """Persists LangGraph state into AgentRun.graph_state (JSON)."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def load(self) -> dict | None:
        from agent.models import AgentRun

        try:
            run = AgentRun.objects.get(pk=self.run_id)
            return run.graph_state if run.graph_state else None
        except AgentRun.DoesNotExist:
            return None

    def save(self, state: dict) -> None:
        from agent.models import AgentRun

        AgentRun.objects.filter(pk=self.run_id).update(graph_state=state)
