"""Shared Playwright fixtures and seed data for e2e tests."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from django.utils import timezone

from tests.factories import (
    AgentFactory,
    AgentRunFactory,
    ConversationFactory,
    HeartbeatLogFactory,
    KnowledgeDocumentFactory,
    LLMUsageFactory,
    MessageFactory,
    SkillFactory,
    ToolExecutionFactory,
    WorkflowFactory,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# pytest-playwright marks all tests as needing the DB, but we use
# pytest-django's live_server which handles that.  We just add our marker.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Workspace fixture — point AGENT_WORKSPACE_DIR at a temp copy
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _workspace(tmp_path, settings):
    src = FIXTURES_DIR / "workspace"
    dst = tmp_path / "workspace"
    shutil.copytree(src, dst)
    # Ensure workflows dir exists
    (dst / "workflows").mkdir(exist_ok=True)
    settings.AGENT_WORKSPACE_DIR = str(dst)
    return dst


# ---------------------------------------------------------------------------
# Helper to build the full live-server URL
# ---------------------------------------------------------------------------


@pytest.fixture()
def url(live_server):
    """Return a helper that builds full URLs for the live test server."""

    def _url(path: str = "/") -> str:
        return f"{live_server.url}{path}"

    return _url


# ---------------------------------------------------------------------------
# Seed data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def default_agent(db):
    """Create a default, active agent."""
    return AgentFactory(
        name="E2E Default Agent",
        is_default=True,
        is_active=True,
        system_prompt="You are a test agent.",
        tools=["web_read", "shell_exec"],
    )


@pytest.fixture()
def second_agent(db):
    """Create a second non-default agent."""
    return AgentFactory(
        name="E2E Secondary Agent",
        is_default=False,
        is_active=True,
        system_prompt="Secondary test agent.",
    )


@pytest.fixture()
def conversation(db, default_agent):
    """Create a conversation with the agent enabled."""
    return ConversationFactory(
        title="Test Conversation",
        interface="web",
        active_agent=default_agent,
    )


@pytest.fixture()
def conversation_with_messages(conversation):
    """Create a conversation with some messages."""
    MessageFactory(conversation=conversation, role="user", content="Hello there!")
    MessageFactory(
        conversation=conversation, role="assistant", content="Hi! How can I help?"
    )
    return conversation


@pytest.fixture()
def completed_run(default_agent, conversation):
    """Create a completed agent run with tool executions."""
    run = AgentRunFactory(
        agent=default_agent,
        conversation=conversation,
        input="Search for Python tutorials",
        status="completed",
        output="Here are some Python tutorials I found.",
        trigger_source="web",
        triggered_skills=["research_skill"],
        graph_state={
            "rag_matches": [
                {"title": "Python Basics", "similarity": "0.87"},
                {"title": "Django Guide", "similarity": "0.72"},
            ],
            "loop_trace": [
                {
                    "round": 1,
                    "decision": "tool_call",
                    "tools": ["web_search"],
                    "reasoning": "Need to search for tutorials",
                },
                {
                    "round": 2,
                    "decision": "answer",
                    "tools": [],
                    "reasoning": "Got results, composing answer",
                },
            ],
        },
        started_at=timezone.now() - timezone.timedelta(seconds=30),
        finished_at=timezone.now(),
    )
    ToolExecutionFactory(
        run=run,
        tool_name="web_search",
        input={"query": "python tutorials"},
        output={"results": [{"title": "Learn Python"}]},
        status="success",
        duration_ms=450,
    )
    return run


@pytest.fixture()
def pending_run(default_agent, conversation):
    """Create a pending agent run."""
    return AgentRunFactory(
        agent=default_agent,
        conversation=conversation,
        input="Pending task",
        status="pending",
        trigger_source="web",
    )


@pytest.fixture()
def running_run(default_agent, conversation):
    """Create a running agent run."""
    return AgentRunFactory(
        agent=default_agent,
        conversation=conversation,
        input="Running task",
        status="running",
        trigger_source="web",
        started_at=timezone.now(),
    )


@pytest.fixture()
def failed_run(default_agent, conversation):
    """Create a failed agent run."""
    return AgentRunFactory(
        agent=default_agent,
        conversation=conversation,
        input="Failed task",
        status="failed",
        trigger_source="web",
        error="Something went wrong",
        started_at=timezone.now() - timezone.timedelta(seconds=10),
        finished_at=timezone.now(),
    )


@pytest.fixture()
def waiting_run_with_approval(default_agent, conversation):
    """Create a run waiting for tool approval."""
    run = AgentRunFactory(
        agent=default_agent,
        conversation=conversation,
        input="Needs approval",
        status="waiting",
        trigger_source="web",
        started_at=timezone.now(),
    )
    ToolExecutionFactory(
        run=run,
        tool_name="shell_exec",
        input={"command": "ls -la"},
        status="pending",
        requires_approval=True,
    )
    return run


@pytest.fixture()
def skill(db):
    """Create a test skill."""
    return SkillFactory(name="test_skill", enabled=True)


@pytest.fixture()
def knowledge_doc(db):
    """Create a ready knowledge document."""
    return KnowledgeDocumentFactory(
        title="Test Knowledge Doc",
        source_type="text",
        raw_content="This is test knowledge content for e2e testing.",
        status="ready",
        is_active=True,
        chunk_count=3,
    )


@pytest.fixture()
def llm_usage(db):
    """Create some LLM usage records for monitoring."""
    LLMUsageFactory(
        source="agent",
        total_tokens=1000,
        estimated_cost_usd=0.01,
    )
    LLMUsageFactory(
        source="chat",
        total_tokens=500,
        estimated_cost_usd=0.005,
    )


@pytest.fixture()
def workflow(db, default_agent, _workspace):
    """Create a workflow with a YAML file on disk."""
    import yaml

    wf = WorkflowFactory(
        name="e2e-test-workflow",
        agent=default_agent,
        enabled=True,
        delivery="announce",
        filename="e2e-test-workflow.yml",
        definition={
            "name": "e2e-test-workflow",
            "description": "Test workflow for e2e",
            "trigger": {"cron": "0 9 * * 1", "timezone": "UTC"},
            "steps": [
                {"name": "step-1", "prompt": "Do the first thing"},
                {"name": "step-2", "prompt": "Do the second thing"},
            ],
        },
    )
    # Write YAML file to disk
    yml_dir = Path(_workspace) / "workflows"
    yml_dir.mkdir(exist_ok=True)
    yml_path = yml_dir / "e2e-test-workflow.yml"
    yml_path.write_text(
        yaml.dump(wf.definition, allow_unicode=True), encoding="utf-8"
    )
    return wf
