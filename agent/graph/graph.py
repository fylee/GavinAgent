from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph

from agent.graph.nodes import (
    assemble_context,
    call_llm,
    check_approval,
    execute_tools,
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
    return "execute_tools"


def _after_execute_tools(state: AgentState) -> str:
    # Feed tool results back to LLM
    return "call_llm"


# ── Graph builder ───────────────────────────────────────────────────────────


def build_graph() -> Any:
    """Build and compile the agent StateGraph."""
    graph = StateGraph(AgentState)

    graph.add_node("assemble_context", assemble_context)
    graph.add_node("call_llm", call_llm)
    graph.add_node("check_approval", check_approval)
    graph.add_node("execute_tools", execute_tools)
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
        {"execute_tools": "execute_tools", END: END},
    )
    graph.add_edge("execute_tools", "call_llm")
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
