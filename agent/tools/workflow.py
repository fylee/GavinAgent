from __future__ import annotations

from typing import Any

from .base import ApprovalPolicy, BaseTool, ToolResult


class ReloadWorkflowsTool(BaseTool):
    name = "reload_workflows"
    description = (
        "Reload workflow definitions from workflows/*.yml and register them "
        "with the scheduler. Always call this after writing or updating a workflow file. "
        "A successful response (even with count=0) means the call completed — do not retry."
    )
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            from agent.workflows.loader import WorkflowLoader
            loaded = WorkflowLoader().load_all()

            # Re-embed skills in case any SKILL.md files changed too
            try:
                from agent.skills.embeddings import embed_all_skills
                embed_all_skills()
            except Exception:
                pass

            return ToolResult(
                output={"loaded": loaded, "count": len(loaded)},
            )
        except Exception as e:
            return ToolResult(output=None, error=str(e))
