from __future__ import annotations

import subprocess
import time
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult, ToolTimeoutError


class ShellTool(BaseTool):
    name = "shell"
    description = (
        "Execute a shell command in the agent workspace directory. "
        "Runs via PowerShell on Windows — use PowerShell syntax, not bash/Unix. "
        "Use 'python' (not 'python3'). "
        "For complex text processing, prefer writing a Python script and running it."
    )
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
        import platform
        start = time.monotonic()
        timeout = settings.AGENT_TOOL_TIMEOUT_SECONDS
        workspace = settings.AGENT_WORKSPACE_DIR
        try:
            # On Windows use PowerShell so the agent can write PS-compatible commands.
            if platform.system() == "Windows":
                args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
                run_shell = False
            else:
                args = command
                run_shell = True
            result = subprocess.run(
                args,
                shell=run_shell,
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
