from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from agent.graph.state import AgentState

if TYPE_CHECKING:
    from agent.models import AgentRun

logger = logging.getLogger(__name__)


class AgentRunner:
    """Entry point for executing or resuming an AgentRun."""

    @staticmethod
    def _resolve_approved_tools(run: "AgentRun") -> list[dict]:
        """
        For a WAITING run being resumed: execute approved tools, collect
        results for rejected ones, and return the full tool_results list
        to be fed back into the LLM.
        """
        from agent.models import ToolExecution
        from agent.tools import get_tool
        from agent.tools.base import ToolTimeoutError

        saved = run.graph_state or {}
        pending_tool_calls: list[dict] = saved.get("pending_tool_calls", [])
        previous_tool_results: list[dict] = saved.get("tool_results", [])

        new_results: list[dict] = []

        for tc in pending_tool_calls:
            te_id = tc.get("tool_execution_id")
            tc_id = tc["id"]

            if not te_id:
                new_results.append({"tool_call_id": tc_id, "result": {"error": "No execution record."}})
                continue

            try:
                te = ToolExecution.objects.get(pk=te_id)
            except ToolExecution.DoesNotExist:
                new_results.append({"tool_call_id": tc_id, "result": {"error": "Execution record missing."}})
                continue

            if te.status == ToolExecution.Status.REJECTED:
                new_results.append({
                    "tool_call_id": tc_id,
                    "result": {"error": "Tool execution was rejected by the user."},
                })
                continue

            # status == RUNNING means approved — execute it now
            tool = get_tool(tc["name"])
            if tool is None:
                result = {"error": f"Unknown tool: {tc['name']}"}
                te.status = ToolExecution.Status.ERROR
                te.output = result
                te.save(update_fields=["status", "output"])
            else:
                try:
                    tool_result = tool.execute(**tc.get("arguments", {}))
                    result = tool_result.as_dict()
                    te.status = (
                        ToolExecution.Status.SUCCESS
                        if tool_result.success
                        else ToolExecution.Status.ERROR
                    )
                    te.output = result
                    te.duration_ms = tool_result.duration_ms
                    te.save(update_fields=["status", "output", "duration_ms"])
                except ToolTimeoutError as exc:
                    result = {"error": str(exc)}
                    te.status = ToolExecution.Status.ERROR
                    te.output = result
                    te.save(update_fields=["status", "output"])

            new_results.append({"tool_call_id": tc_id, "result": result})

        return previous_tool_results + new_results

    @staticmethod
    def run(run: "AgentRun") -> None:
        """Execute the agent loop synchronously (called by Celery task)."""
        from agent.models import AgentRun as AgentRunModel
        from agent.graph.graph import build_graph

        # Mark running
        AgentRunModel.objects.filter(pk=run.pk).update(
            status=AgentRunModel.Status.RUNNING,
            started_at=timezone.now(),
        )
        run.refresh_from_db()

        # Detect resume: graph_state has pending_tool_calls saved from a prior WAITING halt
        is_resume = bool((run.graph_state or {}).get("pending_tool_calls"))

        if is_resume:
            saved = run.graph_state or {}
            tool_results = AgentRunner._resolve_approved_tools(run)
            assistant_tool_call_message = saved.get("assistant_tool_call_message")
            failed_tool_signatures = saved.get("failed_tool_signatures") or []
            succeeded_tool_signatures = saved.get("succeeded_tool_signatures") or []
            # Clear saved state so a fresh failure doesn't re-run old tools
            AgentRunModel.objects.filter(pk=run.pk).update(graph_state={})
        else:
            tool_results = []
            assistant_tool_call_message = None
            failed_tool_signatures = []
            succeeded_tool_signatures = []

        initial_state = AgentState(
            run_id=str(run.id),
            agent_id=str(run.agent_id),
            conversation_id=str(run.conversation_id) if run.conversation_id else None,
            input=run.input,
            messages=[],
            pending_tool_calls=[],
            tool_results=tool_results,
            assistant_tool_call_message=assistant_tool_call_message,
            output="",
            waiting_for_approval=False,
            failed_tool_signatures=failed_tool_signatures,
            succeeded_tool_signatures=succeeded_tool_signatures,
            error=None,
        )

        try:
            graph = build_graph()
            graph.invoke(initial_state)
        except Exception as exc:
            logger.exception("AgentRun %s failed: %s", run.id, exc)
            AgentRunModel.objects.filter(pk=run.pk).update(
                status=AgentRunModel.Status.FAILED,
                error=str(exc),
                finished_at=timezone.now(),
            )

    @staticmethod
    def resume(run: "AgentRun") -> None:
        """Resume a paused run after tool approval. Enqueues a new Celery task."""
        AgentRunner.enqueue(run)

    @staticmethod
    def enqueue(run: "AgentRun") -> None:
        """Enqueue a Celery task for the run, guarded against duplicates."""
        from agent.tasks import execute_agent_run
        from agent.models import AgentRun as AgentRunModel

        with transaction.atomic():
            locked = (
                AgentRunModel.objects.select_for_update()
                .filter(pk=run.pk)
                .first()
            )
            if locked is None:
                return

            # Don't double-enqueue if already running
            if locked.status == AgentRunModel.Status.RUNNING and locked.celery_task_id:
                return

            result = execute_agent_run.delay(str(run.id))
            AgentRunModel.objects.filter(pk=run.pk).update(
                celery_task_id=result.id,
                status=AgentRunModel.Status.PENDING,
            )
