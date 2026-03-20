from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult, ToolTimeoutError


def _workspace_path(relative_path: str) -> Path:
    workspace = Path(settings.AGENT_WORKSPACE_DIR)
    target = (workspace / relative_path).resolve()
    # Prevent path traversal outside workspace
    if not str(target).startswith(str(workspace.resolve())):
        raise ValueError(f"Path '{relative_path}' is outside the workspace.")
    return target


class FileReadTool(BaseTool):
    name = "file_read"
    description = "Read the contents of a file in the agent workspace."
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path within the workspace (e.g. 'memory/MEMORY.md').",
            }
        },
        "required": ["path"],
    }

    def execute(self, path: str, **kwargs: Any) -> ToolResult:
        start = time.monotonic()
        try:
            target = _workspace_path(path)
            if not target.exists():
                return ToolResult(
                    output=None,
                    error=f"File not found: {path}",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            content = target.read_text(encoding="utf-8")
            max_chars = settings.MAX_TOOL_OUTPUT_CHARS
            if len(content) > max_chars:
                content = content[:max_chars] + "\n...[truncated]"
            return ToolResult(
                output=content,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except ValueError as e:
            return ToolResult(
                output=None,
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
            )


class FileWriteTool(BaseTool):
    name = "file_write"
    description = "Write content to a file in the agent workspace. Creates the file if it doesn't exist."
    approval_policy = ApprovalPolicy.REQUIRES_APPROVAL
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path within the workspace.",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file.",
            },
        },
        "required": ["path", "content"],
    }

    def execute(self, path: str, content: str, **kwargs: Any) -> ToolResult:
        start = time.monotonic()
        try:
            target = _workspace_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

            # If writing to MEMORY.md, trigger reembed
            if path.endswith("MEMORY.md"):
                from agent.memory.long_term import reembed
                reembed()

            return ToolResult(
                output=f"Written to {path}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except ValueError as e:
            return ToolResult(
                output=None,
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
