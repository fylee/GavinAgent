from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def execute_agent_run(self, run_id: str):
    """Execute or resume an AgentRun via the AgentRunner."""
    from agent.models import AgentRun
    from agent.runner import AgentRunner

    try:
        run = AgentRun.objects.select_related("agent").get(id=run_id)
        AgentRunner.run(run)
        return run_id
    except AgentRun.DoesNotExist:
        return None
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5)


@shared_task
def heartbeat_task():
    """Celery Beat task: read HEARTBEAT.md and act on unchecked items."""
    from pathlib import Path
    from django.conf import settings
    from agent.models import Agent, AgentRun, HeartbeatLog
    from agent.runner import AgentRunner

    triggered_at = timezone.now()

    try:
        # Get default agent
        agent = Agent.objects.filter(is_default=True, is_active=True).first()
        if agent is None:
            HeartbeatLog.objects.create(
                triggered_at=triggered_at,
                status=HeartbeatLog.Status.ERROR,
                error_message="No active default agent configured.",
            )
            return

        # Read HEARTBEAT.md
        heartbeat_path = Path(settings.AGENT_WORKSPACE_DIR) / "HEARTBEAT.md"
        if not heartbeat_path.exists():
            HeartbeatLog.objects.create(
                triggered_at=triggered_at,
                status=HeartbeatLog.Status.OK,
            )
            return

        content = heartbeat_path.read_text(encoding="utf-8")
        unchecked = [
            line.strip()[len("- [ ]"):].strip()
            for line in content.splitlines()
            if line.strip().startswith("- [ ]")
        ]

        if not unchecked:
            HeartbeatLog.objects.create(
                triggered_at=triggered_at,
                status=HeartbeatLog.Status.OK,
            )
            return

        # Create a run for the heartbeat tasks
        task_summary = "\n".join(f"- {item}" for item in unchecked)
        run = AgentRun.objects.create(
            agent=agent,
            trigger_source=AgentRun.TriggerSource.HEARTBEAT,
            input=f"Heartbeat checklist items to complete:\n{task_summary}",
        )
        AgentRunner.run(run)

        HeartbeatLog.objects.create(
            triggered_at=triggered_at,
            status=HeartbeatLog.Status.ACTED,
            actions_taken=unchecked,
        )

    except Exception as exc:
        logger.exception("Heartbeat task failed: %s", exc)
        HeartbeatLog.objects.create(
            triggered_at=triggered_at,
            status=HeartbeatLog.Status.ERROR,
            error_message=str(exc),
        )


@shared_task
def execute_workflow(workflow_id: str) -> None:
    """Execute a workflow by running all its steps in sequence."""
    from agent.models import Workflow
    from agent.workflows.runner import WorkflowRunner

    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        logger.warning("execute_workflow: workflow %s not found", workflow_id)
        return

    if not workflow.enabled:
        return

    WorkflowRunner().run(workflow)


@shared_task
def reembed_memory_task():
    """Celery task: full reembed of MEMORY.md into the vector store."""
    from agent.memory.long_term import full_reembed
    full_reembed()
