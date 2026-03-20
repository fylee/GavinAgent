from __future__ import annotations

import subprocess
import time
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult, ToolTimeoutError


class ShellTool(BaseTool):
    name = "shell"
    description = "Execute a shell command in the agent workspace directory."
    approval_policy = ApprovalPolicy.REQUIRES_APPROVAL
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to run.",
            }
        },
        "required": ["command"],
    }

    def execute(self, command: str, **kwargs: Any) -> ToolResult:
        start = time.monotonic()
        timeout = settings.AGENT_TOOL_TIMEOUT_SECONDS
        workspace = settings.AGENT_WORKSPACE_DIR
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            max_chars = settings.MAX_TOOL_OUTPUT_CHARS
            if len(output) > max_chars:
                output = output[:max_chars] + "\n...[truncated]"
            duration_ms = int((time.monotonic() - start) * 1000)
            if result.returncode != 0:
                return ToolResult(
                    output=output,
                    error=f"Command exited with code {result.returncode}",
                    duration_ms=duration_ms,
                )
            return ToolResult(output=output, duration_ms=duration_ms)
        except subprocess.TimeoutExpired:
            raise ToolTimeoutError(
                f"Shell command timed out after {timeout}s: {command}"
            )
