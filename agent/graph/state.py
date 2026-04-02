from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict):
    run_id: str
    agent_id: str
    conversation_id: str | None
    input: str
    messages: Annotated[list[dict], operator.add]
    pending_tool_calls: list[dict]   # tool calls waiting to be executed
    tool_results: list[dict]  # results for the CURRENT round only; replaced each round
    # The assistant message that made the last round of tool_calls.
    # Must be injected before tool results when calling the LLM again.
    assistant_tool_call_message: dict | None
    output: str
    waiting_for_approval: bool
    tool_call_rounds: int  # incremented each time execute_tools completes
    visited_urls: list[str]  # URLs already fetched via web_read
    failed_tool_signatures: list[str]  # "tool_name|arg_hash" combos that already errored
    succeeded_tool_signatures: list[str]  # "tool_name|arg_hash" combos that already succeeded
    collected_markdown: list[str]  # markdown snippets from tool outputs (e.g. chart images), persisted across rounds
    search_result_urls: list[str]  # URLs from web_search results, used for automatic web_read fallback
    loop_trace: list[dict]  # per-round decision log: [{round, decision, tools, reasoning}]
    blocked_mcp_servers: list[str]  # MCP server names whose tools cannot be resolved this run
    consecutive_failed_rounds: int  # rounds where every tool call failed; resets on any success
    error: str | None
