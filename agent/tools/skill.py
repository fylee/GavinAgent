from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult


class RunSkillTool(BaseTool):
    name = "run_skill"
    description = (
        "Execute a workspace skill handler by name. "
        "Use this to run a skill that has been activated in your context — "
        "for example, call run_skill with skill_name='weather' and "
        "input='Taipei, Kaohsiung' to get weather data. "
        "Always prefer this over web_read or api_get when a skill is available for the task."
    )
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The name of the skill to run (matches the skill directory name).",
            },
            "input": {
                "type": "string",
                "description": "Input string to pass to the skill handler.",
            },
        },
        "required": ["skill_name", "input"],
    }

    def execute(self, skill_name: str, input: str, **kwargs: Any) -> ToolResult:
        start = time.monotonic()
        skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
        handler_path = skills_dir / skill_name / "handler.py"

        if not handler_path.exists():
            return ToolResult(
                output=None,
                error=f"Skill '{skill_name}' has no handler.py.",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        try:
            spec = importlib.util.spec_from_file_location(
                f"skill_{skill_name}", handler_path
            )
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            if not hasattr(module, "handle"):
                return ToolResult(
                    output=None,
                    error=f"Skill '{skill_name}' handler.py has no handle() function.",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            result = module.handle(input)
            return ToolResult(
                output={"result": result},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return ToolResult(
                output=None,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
