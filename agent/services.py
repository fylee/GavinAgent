from __future__ import annotations
from .models import Agent, AgentRun


class AgentService:
    def __init__(self, run: AgentRun):
        self.run = run

    def execute(self) -> AgentRun:
        from .graph import build_graph
        from django.utils import timezone

        self.run.status = AgentRun.Status.RUNNING
        self.run.started_at = timezone.now()
        self.run.save(update_fields=["status", "started_at"])
        try:
            graph = build_graph()
            initial_state = {
                "input": self.run.input,
                "messages": [],
                "tool_calls": [],
                "output": "",
                "waiting_for_human": False,
            }
            # Merge any persisted state
            if self.run.graph_state:
                initial_state.update(self.run.graph_state)

            result = graph.invoke(initial_state)
            self.run.graph_state = result
            self.run.output = result.get("output", "")
            if result.get("waiting_for_human"):
                self.run.status = AgentRun.Status.WAITING
            else:
                self.run.status = AgentRun.Status.COMPLETED
        except Exception as exc:
            self.run.status = AgentRun.Status.FAILED
            self.run.error = str(exc)
        finally:
            self.run.finished_at = timezone.now()
            self.run.save()
        return self.run
