"""Tests for web_read auto-fallback in execute_tools."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.graph.nodes import execute_tools
from agent.tools.base import ToolResult

pytestmark = pytest.mark.django_db

PATCH_GET_TOOL = "agent.tools.get_tool"


@pytest.fixture
def _base_state(db):
    """Minimal AgentState-like dict with an AgentRun in the DB."""
    from tests.factories import AgentRunFactory

    run = AgentRunFactory(status="running")
    return {
        "run_id": str(run.id),
        "agent_id": str(run.agent_id),
        "conversation_id": str(run.conversation_id) if run.conversation_id else None,
        "input": "test",
        "pending_tool_calls": [],
        "tool_results": [],
        "visited_urls": [],
        "failed_tool_signatures": [],
        "succeeded_tool_signatures": [],
        "collected_markdown": [],
        "search_result_urls": [],
        "tool_call_rounds": 0,
    }


class TestWebReadAutoFallback:
    """When web_read fails, execute_tools should automatically try URLs
    from the search_result_urls pool without the LLM being involved."""

    def test_fallback_succeeds_on_second_url(self, _base_state):
        """web_read fails on requested URL ??auto-tries fallback ??succeeds."""
        state = {
            **_base_state,
            "search_result_urls": [
                "https://blocked-site.com/data",
                "https://good-site.com/data",
                "https://another-site.com/data",
            ],
            "pending_tool_calls": [
                {
                    "id": "call_1",
                    "name": "web_read",
                    "arguments": {"url": "https://blocked-site.com/data"},
                },
            ],
        }

        error_result = ToolResult(output=None, error="Blocked", duration_ms=100)
        success_result = ToolResult(
            output={"url": "https://good-site.com/data", "content": "Stock prices..."},
            duration_ms=500,
        )

        call_count = {"n": 0}

        def fake_execute(url, **kwargs):
            call_count["n"] += 1
            if url == "https://blocked-site.com/data":
                return error_result
            return success_result

        mock_tool = MagicMock()
        mock_tool.execute = fake_execute

        with patch(PATCH_GET_TOOL, return_value=mock_tool):
            result = execute_tools(state)

        # Should have tried the original + 1 fallback
        assert call_count["n"] == 2

        # The final result for call_1 should be the successful fallback
        call_1_result = next(r for r in result["tool_results"] if r["tool_call_id"] == "call_1")
        assert "error" not in call_1_result["result"]
        assert call_1_result["result"]["output"]["content"] == "Stock prices..."

        # Both URLs should be in visited
        assert "https://blocked-site.com/data" in result["visited_urls"]
        assert "https://good-site.com/data" in result["visited_urls"]

        # search_result_urls should be preserved
        assert len(result["search_result_urls"]) == 3

    def test_fallback_skips_already_visited_urls(self, _base_state):
        """Already-visited URLs in the fallback pool are skipped."""
        state = {
            **_base_state,
            "search_result_urls": [
                "https://already-visited.com",
                "https://also-visited.com",
                "https://fresh-site.com/data",
            ],
            "visited_urls": [
                "https://already-visited.com",
                "https://also-visited.com",
            ],
            "pending_tool_calls": [
                {
                    "id": "call_1",
                    "name": "web_read",
                    "arguments": {"url": "https://blocked.com"},
                },
            ],
        }

        error_result = ToolResult(output=None, error="Blocked", duration_ms=100)
        success_result = ToolResult(
            output={"url": "https://fresh-site.com/data", "content": "Data here"},
            duration_ms=300,
        )

        def fake_execute(url, **kwargs):
            if url == "https://fresh-site.com/data":
                return success_result
            return error_result

        mock_tool = MagicMock()
        mock_tool.execute = fake_execute

        with patch(PATCH_GET_TOOL, return_value=mock_tool):
            result = execute_tools(state)

        call_1_result = next(r for r in result["tool_results"] if r["tool_call_id"] == "call_1")
        assert call_1_result["result"]["output"]["content"] == "Data here"

    def test_fallback_respects_limit(self, _base_state, settings):
        """Only tries up to AGENT_WEB_READ_FALLBACK_LIMIT URLs."""
        settings.AGENT_WEB_READ_FALLBACK_LIMIT = 2

        state = {
            **_base_state,
            "search_result_urls": [
                "https://bad1.com",
                "https://bad2.com",
                "https://bad3.com",
                "https://good.com",
            ],
            "pending_tool_calls": [
                {
                    "id": "call_1",
                    "name": "web_read",
                    "arguments": {"url": "https://original-bad.com"},
                },
            ],
        }

        call_urls = []

        def fake_execute(url, **kwargs):
            call_urls.append(url)
            return ToolResult(output=None, error="Blocked", duration_ms=100)

        mock_tool = MagicMock()
        mock_tool.execute = fake_execute

        with patch(PATCH_GET_TOOL, return_value=mock_tool):
            result = execute_tools(state)

        # Original + 2 fallbacks = 3 total calls (not 4, because limit=2)
        assert len(call_urls) == 3
        assert call_urls == ["https://original-bad.com", "https://bad1.com", "https://bad2.com"]

    def test_no_fallback_when_pool_empty(self, _base_state):
        """No fallback attempted when search_result_urls is empty."""
        state = {
            **_base_state,
            "search_result_urls": [],
            "pending_tool_calls": [
                {
                    "id": "call_1",
                    "name": "web_read",
                    "arguments": {"url": "https://blocked.com"},
                },
            ],
        }

        call_count = {"n": 0}

        def fake_execute(url, **kwargs):
            call_count["n"] += 1
            return ToolResult(output=None, error="Blocked", duration_ms=100)

        mock_tool = MagicMock()
        mock_tool.execute = fake_execute

        with patch(PATCH_GET_TOOL, return_value=mock_tool):
            result = execute_tools(state)

        assert call_count["n"] == 1  # only the original call
        call_1_result = next(r for r in result["tool_results"] if r["tool_call_id"] == "call_1")
        assert "error" in call_1_result["result"]

    def test_search_results_stored_in_pool(self, _base_state):
        """When web_search succeeds, its result URLs are added to search_result_urls."""
        state = {
            **_base_state,
            "pending_tool_calls": [
                {
                    "id": "call_1",
                    "name": "web_search",
                    "arguments": {"query": "winbond stock price"},
                },
            ],
        }

        search_result = ToolResult(
            output={
                "query": "winbond stock price",
                "results": [
                    {"title": "Yahoo", "url": "https://finance.yahoo.com/2344", "snippet": "..."},
                    {"title": "MarketWatch", "url": "https://marketwatch.com/2344", "snippet": "..."},
                ],
                "total": 2,
            },
            duration_ms=1000,
        )

        mock_tool = MagicMock()
        mock_tool.execute = MagicMock(return_value=search_result)

        with patch(PATCH_GET_TOOL, return_value=mock_tool):
            result = execute_tools(state)

        assert "https://finance.yahoo.com/2344" in result["search_result_urls"]
        assert "https://marketwatch.com/2344" in result["search_result_urls"]

    def test_no_fallback_for_non_web_read_tools(self, _base_state):
        """Fallback logic only applies to web_read, not other tools."""
        state = {
            **_base_state,
            "search_result_urls": ["https://fallback.com"],
            "pending_tool_calls": [
                {
                    "id": "call_1",
                    "name": "api_get",
                    "arguments": {"url": "https://api.example.com/data"},
                },
            ],
        }

        call_count = {"n": 0}

        def fake_execute(url, **kwargs):
            call_count["n"] += 1
            return ToolResult(output=None, error="API error", duration_ms=100)

        mock_tool = MagicMock()
        mock_tool.execute = fake_execute

        with patch(PATCH_GET_TOOL, return_value=mock_tool):
            result = execute_tools(state)

        assert call_count["n"] == 1  # no fallback attempted
