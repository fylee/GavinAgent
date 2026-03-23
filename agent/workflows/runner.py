"""WorkflowRunner — executes a workflow's steps in isolation."""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def _deliver(workflow, output: str) -> None:
    from agent.models import Workflow
    from chat.models import Message

    if workflow.delivery == "silent":
        return

    if workflow.delivery == "telegram" or (
        workflow.delivery == "announce" and not workflow.conversation_id
    ):
        # Send via Telegram
        try:
            from interfaces.telegram.sender import send_message as tg_send
            tg_send(output)
        except Exception as exc:
            logger.error("Workflow %s: Telegram delivery failed: %s", workflow.name, exc)
        return

    if workflow.delivery == "announce" and workflow.conversation_id:
        try:
            Message.objects.create(
                conversation=workflow.conversation,
                role=Message.Role.ASSISTANT,
                content=output,
            )
        except Exception as exc:
            logger.error("Workflow %s: announce delivery failed: %s", workflow.name, exc)


class WorkflowRunner:
    def run(self, workflow) -> list:
        from agent.models import Agent, AgentRun
        from agent.runner import AgentRunner

        steps = workflow.definition.get("steps", [])
        agent = workflow.agent
        if agent is None:
            agent = Agent.objects.filter(is_default=True, is_active=True).first()

        if agent is None:
            logger.error("Workflow %s: no agent available", workflow.name)
            return []

        previous_output: str | None = None
        runs: list[AgentRun] = []

        for i, step in enumerate(steps):
            step_name = step.get("name", f"step-{i + 1}")
            prompt = step.get("prompt", "")
            if not isinstance(prompt, str):
                prompt = str(prompt)
            prompt = prompt.strip()

            if previous_output and i > 0:
                prompt = (
                    f"Previous step output:\n{previous_output}\n\n"
                    f"Current step — {step_name}:\n{prompt}"
                )

            run = AgentRun.objects.create(
                agent=agent,
                conversation=workflow.conversation,
                trigger_source=AgentRun.TriggerSource.WORKFLOW,
                status=AgentRun.Status.PENDING,
                input=prompt,
                workflow=workflow,
                workflow_step=i,
                workflow_step_name=step_name,
            )

            try:
                AgentRunner.run(run)
                run.refresh_from_db()
            except Exception as exc:
                logger.error(
                    "Workflow %s step %s failed with exception: %s",
                    workflow.name, step_name, exc,
                )
                run.status = AgentRun.Status.FAILED
                run.error = str(exc)
                run.save(update_fields=["status", "error"])

            runs.append(run)
            previous_output = run.output or ""

            if run.status == AgentRun.Status.FAILED:
                logger.warning(
                    "Workflow %s: step %s failed, aborting remaining steps",
                    workflow.name, step_name,
                )
                break

        workflow.last_run_at = timezone.now()
        workflow.save(update_fields=["last_run_at"])

        if previous_output:
            _deliver(workflow, previous_output)

        # One-shot: disable after running
        if workflow.definition.get("trigger", {}).get("at"):
            workflow.enabled = False
            workflow.save(update_fields=["enabled"])

        return runs
