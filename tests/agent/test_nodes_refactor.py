"""Tests for Spec 028 — Refactor Agent Loop: assemble_context + call_llm."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# _is_cancelled
# ══════════════════════════════════════════════════════════════════════════════

class TestIsCancelled:
    @pytest.mark.django_db
    def test_failed_run_returns_true(self):
        """AgentRun status FAILED → True."""
        from agent.graph.nodes import _is_cancelled
        from tests.factories import AgentRunFactory

        run = AgentRunFactory(status="failed")
        assert _is_cancelled(str(run.pk)) is True

    @pytest.mark.django_db
    def test_running_run_returns_false(self):
        """AgentRun status RUNNING → False."""
        from agent.graph.nodes import _is_cancelled
        from tests.factories import AgentRunFactory

        run = AgentRunFactory(status="running")
        assert _is_cancelled(str(run.pk)) is False

    def test_db_error_returns_false(self):
        """DB query failure → does not raise, returns False."""
        from agent.graph.nodes import _is_cancelled

        with patch("agent.models.AgentRun") as mock_ar:
            mock_ar.objects.filter.side_effect = Exception("DB down")
            assert _is_cancelled("nonexistent-id") is False


# ══════════════════════════════════════════════════════════════════════════════
# _assemble_messages
# ══════════════════════════════════════════════════════════════════════════════

class TestAssembleMessages:
    def _base_state(self, **kwargs) -> dict:
        return {
            "run_id": "run-1",
            "input": "hello",
            "conversation_id": None,
            "tool_results": [],
            "assistant_tool_call_message": None,
            "collected_markdown": [],
            **kwargs,
        }

    def test_no_conversation_appends_user_message(self):
        """conversation_id=None → input appended as user message."""
        from agent.graph.nodes import _assemble_messages

        state = self._base_state()
        msgs, stats = _assemble_messages(state, system_content="SYS", model="gpt-4o")
        assert msgs[0] == {"role": "system", "content": "SYS"}
        assert any(m["role"] == "user" and m["content"] == "hello" for m in msgs)

    def test_error_prefix_messages_filtered(self, settings):
        """Assistant messages beginning with error prefixes are removed from history."""
        from agent.graph.nodes import _assemble_messages

        chat_msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "LLM error: timeout"},
            {"role": "user", "content": "try again"},
            {"role": "assistant", "content": "Sure!"},
        ]
        state = self._base_state(conversation_id="conv-1")
        settings.AGENT_HISTORY_WINDOW = 20
        settings.AGENT_CONTEXT_BUDGET_TOKENS = 100000

        with patch("agent.graph.nodes._fetch_chat_history", return_value=chat_msgs):
            msgs, stats = _assemble_messages(state, system_content="SYS", model="gpt-4o")

        contents = [m["content"] for m in msgs if m["role"] == "assistant"]
        assert "LLM error: timeout" not in contents
        assert "Sure!" in contents

    def test_history_window_truncation(self, settings):
        """AGENT_HISTORY_WINDOW=2 → only the last 2 messages are kept."""
        from agent.graph.nodes import _assemble_messages

        chat_msgs = [{"role": "user", "content": f"msg-{i}"} for i in range(10)]
        state = self._base_state(conversation_id="conv-1")
        settings.AGENT_HISTORY_WINDOW = 2
        settings.AGENT_CONTEXT_BUDGET_TOKENS = 100000

        with patch("agent.graph.nodes._fetch_chat_history", return_value=chat_msgs):
            msgs, stats = _assemble_messages(state, system_content="SYS", model="gpt-4o")

        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert len(user_msgs) <= 2

    def test_tool_results_injected_when_ids_match(self):
        """required_ids ⊆ result_ids → assistant msg + tool results injected."""
        from agent.graph.nodes import _assemble_messages

        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "tc-1", "function": {"name": "foo", "arguments": "{}"}}],
        }
        tool_results = [{"tool_call_id": "tc-1", "result": {"ok": True}}]
        state = self._base_state(
            assistant_tool_call_message=assistant_msg,
            tool_results=tool_results,
        )
        msgs, _ = _assemble_messages(state, system_content="SYS", model="gpt-4o")
        roles = [m["role"] for m in msgs]
        assert "tool" in roles

    def test_tool_results_skipped_when_ids_missing(self):
        """Incomplete result_ids → assistant msg and tool results NOT injected."""
        from agent.graph.nodes import _assemble_messages

        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc-1", "function": {"name": "foo", "arguments": "{}"}},
                {"id": "tc-2", "function": {"name": "bar", "arguments": "{}"}},
            ],
        }
        tool_results = [{"tool_call_id": "tc-1", "result": {}}]  # tc-2 missing
        state = self._base_state(
            assistant_tool_call_message=assistant_msg,
            tool_results=tool_results,
        )
        msgs, _ = _assemble_messages(state, system_content="SYS", model="gpt-4o")
        assert all(m["role"] != "tool" for m in msgs)

    def test_collected_markdown_appended_concluding_round(self):
        """collected_markdown present with no tool_results → verbatim reminder injected."""
        from agent.graph.nodes import _assemble_messages

        state = self._base_state(
            collected_markdown=["![chart](data:image/png;base64,abc)"],
            tool_results=[],
        )
        msgs, _ = _assemble_messages(state, system_content="SYS", model="gpt-4o")
        last = msgs[-1]["content"]
        assert "verbatim" in last.lower()
        assert "data:image/png" in last

    def test_history_stats_returned(self, settings):
        """Returned stats dict contains history_messages and history_dropped."""
        from agent.graph.nodes import _assemble_messages

        chat_msgs = [{"role": "user", "content": f"m{i}"} for i in range(5)]
        state = self._base_state(conversation_id="conv-1")
        settings.AGENT_HISTORY_WINDOW = 3
        settings.AGENT_CONTEXT_BUDGET_TOKENS = 100000

        with patch("agent.graph.nodes._fetch_chat_history", return_value=chat_msgs):
            _, stats = _assemble_messages(state, system_content="SYS", model="gpt-4o")

        assert "history_messages" in stats
        assert "history_dropped" in stats


# ══════════════════════════════════════════════════════════════════════════════
# _persist_loop_trace
# ══════════════════════════════════════════════════════════════════════════════

class TestPersistLoopTrace:
    @pytest.mark.django_db
    def test_writes_loop_trace_to_graph_state(self):
        """loop_trace is written to AgentRun.graph_state['loop_trace']."""
        from agent.graph.nodes import _persist_loop_trace
        from tests.factories import AgentRunFactory

        run = AgentRunFactory(graph_state={})
        trace = [{"round": 1, "decision": "tool_call"}]
        _persist_loop_trace(str(run.pk), trace)

        run.refresh_from_db()
        assert run.graph_state["loop_trace"] == trace

    def test_nonexistent_run_does_not_raise(self):
        """Non-existent run_id → silently ignored (no exception raised)."""
        from agent.graph.nodes import _persist_loop_trace

        _persist_loop_trace("nonexistent-99999", [{"round": 1}])


# ══════════════════════════════════════════════════════════════════════════════
# assemble_context
# ══════════════════════════════════════════════════════════════════════════════

class TestAssembleContext:
    def test_returns_all_seven_state_fields(self):
        """assemble_context returns a dict with all 7 _* fields."""
        from agent.graph.nodes import assemble_context

        state = {
            "run_id": "run-1",
            "agent_id": "agent-1",
            "input": "hello",
            "conversation_id": None,
        }
        with patch("agent.graph.nodes._build_system_context") as mock_ctx, \
             patch("agent.graph.nodes._build_tools_schema", return_value=[]), \
             patch("agent.graph.nodes._get_agent_model", return_value="test-model"):
            mock_ctx.return_value = ("SYS", [], [], {}, {})
            result = assemble_context(state)

        expected_keys = {
            "_system_content", "_triggered_skills", "_skill_dir_map",
            "_rag_matches", "_context_trace", "_tools_schema", "_model",
        }
        assert expected_keys.issubset(result.keys())

    def test_conversation_id_appended_to_system_content(self):
        """conversation_id present → appended to _system_content."""
        from agent.graph.nodes import assemble_context

        state = {
            "run_id": "run-1",
            "agent_id": "agent-1",
            "input": "hello",
            "conversation_id": "conv-abc",
        }
        with patch("agent.graph.nodes._build_system_context") as mock_ctx, \
             patch("agent.graph.nodes._build_tools_schema", return_value=[]), \
             patch("agent.graph.nodes._get_agent_model", return_value="test-model"):
            mock_ctx.return_value = ("BASE_SYS", [], [], {}, {})
            result = assemble_context(state)

        assert "conv-abc" in result["_system_content"]

    def test_no_conversation_id_system_content_unchanged(self):
        """conversation_id=None → _system_content returned as-is."""
        from agent.graph.nodes import assemble_context

        state = {
            "run_id": "run-1",
            "agent_id": "agent-1",
            "input": "hello",
            "conversation_id": None,
        }
        with patch("agent.graph.nodes._build_system_context") as mock_ctx, \
             patch("agent.graph.nodes._build_tools_schema", return_value=[]), \
             patch("agent.graph.nodes._get_agent_model", return_value="test-model"):
            mock_ctx.return_value = ("BASE_SYS", [], [], {}, {})
            result = assemble_context(state)

        assert result["_system_content"] == "BASE_SYS"


# ══════════════════════════════════════════════════════════════════════════════
# call_llm — tool-approval resumption fallback
# ══════════════════════════════════════════════════════════════════════════════

class TestCallLlmResumptionFallback:
    def test_empty_system_content_triggers_rebuild(self):
        """
        When _system_content is empty (old state from tool-approval resumption),
        call_llm falls back to _build_system_context() and still returns a reply.
        """
        from agent.graph.nodes import call_llm

        state = {
            "run_id": "run-1",
            "agent_id": "agent-1",
            "input": "hello",
            "conversation_id": None,
            "_system_content": "",
            "_tools_schema": [],
            "_model": "",
            "tool_results": [],
            "assistant_tool_call_message": None,
            "collected_markdown": [],
            "tool_call_rounds": 0,
            "loop_trace": [],
            "failed_tool_signatures": [],
            "succeeded_tool_signatures": [],
            "search_result_urls": [],
            "blocked_mcp_servers": [],
        }
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].message.content = "Hello back"

        with patch("agent.graph.nodes._build_system_context") as mock_ctx, \
             patch("core.llm.get_completion", return_value=mock_response), \
             patch("agent.graph.nodes._is_cancelled", return_value=False), \
             patch("agent.graph.nodes._get_agent_model", return_value="test-model"), \
             patch("agent.graph.nodes._persist_first_round_context"), \
             patch("agent.graph.nodes._get_run_obj", return_value=None), \
             patch("agent.graph.nodes._persist_loop_trace"), \
             patch("agent.graph.nodes._build_tools_schema", return_value=[]):
            mock_ctx.return_value = ("REBUILT_SYS", [], [], {}, {})
            result = call_llm(state)

        mock_ctx.assert_called_once()
        assert result.get("output") == "Hello back"
