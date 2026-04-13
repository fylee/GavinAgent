from __future__ import annotations

import importlib.util
import re
import time
from pathlib import Path
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult

# Pattern to find markdown image syntax in handler results
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


class RunSkillTool(BaseTool):
    name = "run_skill"
    description = (
        "Execute a workspace skill that has a handler.py file. "
        "Only call this when the skill's instructions explicitly say to use run_skill, "
        "or when the skill index shows it has a handler. "
        "Do NOT call this for MCP-based skills (those use edwm__, or similar prefixed tools directly). "
        "Example: run_skill with skill_name='weather' and input='Taipei'."
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
        # Resolve handler across all trusted skill source dirs (Spec 023)
        handler_path: Path | None = None
        try:
            from agent.skills.discovery import collect_all_skills
            all_skills = collect_all_skills(check_db_trust=True)
            for info in all_skills:
                if info["name"] == skill_name and info["trusted"]:
                    candidate = info["skill_dir"] / "handler.py"
                    if candidate.exists():
                        handler_path = candidate
                    break
        except Exception:
            # Fallback to native dir
            skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
            candidate = skills_dir / skill_name / "handler.py"
            if candidate.exists():
                handler_path = candidate

        if handler_path is None:
            return ToolResult(
                output=None,
                error=(
                    f"Skill '{skill_name}' has no handler.py. "
                    "If this is an MCP-based skill, call its MCP tools directly "
                    "(e.g. edwm__get_logical_table_id_by_name) instead of run_skill."
                ),
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
            output_dict: dict[str, Any] = {"result": result}
            # Extract markdown images so collected_markdown tracking works
            if isinstance(result, str):
                md_images = _MD_IMAGE_RE.findall(result)
                if md_images:
                    output_dict["markdown"] = "\n".join(md_images)
            return ToolResult(
                output=output_dict,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return ToolResult(
                output=None,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
