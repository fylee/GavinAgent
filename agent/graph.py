from __future__ import annotations
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
import operator


class AgentState(TypedDict):
    input: str
    messages: Annotated[list[dict], operator.add]
    tool_calls: list[dict]
    output: str
    waiting_for_human: bool


def call_llm(state: AgentState) -> dict:
    from core.llm import get_completion
    messages = state.get("messages", [])
    if not messages:
        messages = [{"role": "user", "content": state["input"]}]
    response = get_completion(messages)
    content = response.choices[0].message.content
    return {
        "messages": [{"role": "assistant", "content": content}],
        "output": content,
        "waiting_for_human": False,
    }


def should_continue(state: AgentState) -> str:
    if state.get("waiting_for_human"):
        return "wait"
    return END


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("llm", call_llm)
    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", should_continue, {"wait": END, END: END})
    return graph.compile()
