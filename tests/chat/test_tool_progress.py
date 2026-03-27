"""Unit tests for chat views — tool progress display."""
from __future__ import annotations

import pytest
from django.test import RequestFactory

from agent.models import AgentRun, ToolExecution
from tests.factories import (
    AgentFactory,
    AgentRunFactory,
    ConversationFactory,
    MessageFactory,
    ToolExecutionFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def agent():
    return AgentFactory()


@pytest.fixture
def conversation(agent):
    return ConversationFactory(active_agent=agent)


@pytest.fixture
def user_msg(conversation):
    return MessageFactory(conversation=conversation, role="user", content="Hello")


# ── Tests ──────────────────────────────────────────────────────────────


class TestMessageStreamToolProgress:
    """Verify that tool execution progress is shown during agent runs."""

    def _get(self, rf, conversation, user_msg):
        from chat.views import MessageStreamView

        request = rf.get("/")
        view = MessageStreamView.as_view()
        return view(request, conversation_pk=str(conversation.id), pk=str(user_msg.id))

    def test_typing_indicator_when_no_tools(self, rf, conversation, user_msg, agent):
        """When agent is running but no tool executions yet, show plain typing indicator."""
        AgentRunFactory(
            agent=agent,
            conversation=conversation,
            status=AgentRun.Status.RUNNING,
        )
        response = self._get(rf, conversation, user_msg)
        content = response.content.decode()
        assert "typing-indicator" in content
        # Should NOT contain tool progress elements
        assert "tool_name" not in content

    def test_tool_progress_shown_during_run(self, rf, conversation, user_msg, agent):
        """When agent has tool executions, show progress template."""
        run = AgentRunFactory(
            agent=agent,
            conversation=conversation,
            status=AgentRun.Status.RUNNING,
        )
        ToolExecutionFactory(
            run=run,
            tool_name="web_search",
            status=ToolExecution.Status.SUCCESS,
            duration_ms=1200,
        )
        ToolExecutionFactory(
            run=run,
            tool_name="web_read",
            status=ToolExecution.Status.RUNNING,
        )
        response = self._get(rf, conversation, user_msg)
        content = response.content.decode()
        assert "web_search" in content
        assert "web_read" in content
        assert "1200ms" in content
        # Should still have the polling trigger
        assert "hx-trigger" in content

    def test_triggered_skills_shown(self, rf, conversation, user_msg, agent):
        """When agent run has triggered_skills, show skill badges."""
        run = AgentRunFactory(
            agent=agent,
            conversation=conversation,
            status=AgentRun.Status.RUNNING,
            triggered_skills=["stock_analyst", "data_reporter"],
        )
        response = self._get(rf, conversation, user_msg)
        content = response.content.decode()
        assert "stock_analyst" in content
        assert "data_reporter" in content

    def test_tool_approval_takes_priority(self, rf, conversation, user_msg, agent):
        """When agent is WAITING with pending approval, approval card is shown instead of progress."""
        run = AgentRunFactory(
            agent=agent,
            conversation=conversation,
            status=AgentRun.Status.WAITING,
        )
        te = ToolExecutionFactory(
            run=run,
            tool_name="shell_exec",
            status=ToolExecution.Status.PENDING,
            requires_approval=True,
        )
        response = self._get(rf, conversation, user_msg)
        content = response.content.decode()
        assert "tool-approval" in content
        assert "shell_exec" in content

    def test_progress_with_mixed_statuses(self, rf, conversation, user_msg, agent):
        """Multiple tool executions with different statuses all appear."""
        run = AgentRunFactory(
            agent=agent,
            conversation=conversation,
            status=AgentRun.Status.RUNNING,
        )
        ToolExecutionFactory(run=run, tool_name="web_search", status=ToolExecution.Status.SUCCESS, duration_ms=800)
        ToolExecutionFactory(run=run, tool_name="web_read", status=ToolExecution.Status.ERROR, duration_ms=200)
        ToolExecutionFactory(run=run, tool_name="api_get", status=ToolExecution.Status.RUNNING)
        response = self._get(rf, conversation, user_msg)
        content = response.content.decode()
        assert "web_search" in content
        assert "web_read" in content
        assert "api_get" in content
        assert "Success" in content
        assert "Error" in content
        assert "Running" in content

    def test_loop_trace_reasoning_shown(self, rf, conversation, user_msg, agent):
        """When agent run has loop_trace with reasoning, it appears in progress."""
        run = AgentRunFactory(
            agent=agent,
            conversation=conversation,
            status=AgentRun.Status.RUNNING,
            graph_state={
                "loop_trace": [
                    {
                        "round": 1,
                        "decision": "tool_call",
                        "tools": ["web_search"],
                        "reasoning": "I need to search for Winbond stock data first.",
                    },
                ],
            },
        )
        ToolExecutionFactory(
            run=run,
            tool_name="web_search",
            status=ToolExecution.Status.SUCCESS,
            input={"query": "Winbond 2344 stock price"},
            duration_ms=1500,
        )
        response = self._get(rf, conversation, user_msg)
        content = response.content.decode()
        # Reasoning text should appear
        assert "search for Winbond" in content
        # Round number should appear (may have whitespace around it in the template)
        assert "bg-blue-900/60" in content  # round badge styling for tool_call decision

    def test_tool_input_details_shown(self, rf, conversation, user_msg, agent):
        """Tool input details (query, url, path) appear next to tool name."""
        run = AgentRunFactory(
            agent=agent,
            conversation=conversation,
            status=AgentRun.Status.RUNNING,
        )
        ToolExecutionFactory(
            run=run,
            tool_name="web_search",
            status=ToolExecution.Status.SUCCESS,
            input={"query": "Taiwan stock market data"},
            duration_ms=1000,
        )
        ToolExecutionFactory(
            run=run,
            tool_name="web_read",
            status=ToolExecution.Status.SUCCESS,
            input={"url": "https://example.com/stocks/2344"},
            duration_ms=2000,
        )
        response = self._get(rf, conversation, user_msg)
        content = response.content.decode()
        assert "Taiwan stock market data" in content
        assert "example.com/stocks/2344" in content
