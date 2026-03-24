from __future__ import annotations

import time
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult


class ApiGetTool(BaseTool):
    name = "api_get"
    description = "Make an HTTP GET request to a URL and return the response body."
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to request."},
            "headers": {
                "type": "object",
                "description": "Optional HTTP headers.",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["url"],
    }

    def execute(self, url: str, headers: dict | None = None, **kwargs: Any) -> ToolResult:
        import httpx

        start = time.monotonic()
        timeout = settings.AGENT_TOOL_TIMEOUT_SECONDS
        max_chars = settings.MAX_TOOL_OUTPUT_CHARS
        try:
            resp = httpx.get(url, headers=headers or {}, timeout=timeout, follow_redirects=True)
            body = resp.text
            if len(body) > max_chars:
                body = body[:max_chars] + "\n...[truncated]"
            if resp.status_code >= 400:
                return ToolResult(
                    output=None,
                    error=f"HTTP {resp.status_code}: {body[:500]}",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            return ToolResult(
                output={"status_code": resp.status_code, "body": body},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:
            return ToolResult(
                output=None,
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
            )


class ApiPostTool(BaseTool):
    name = "api_post"
    description = "Make an HTTP POST request to a URL with an optional JSON body."
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to POST to."},
            "body": {"type": "object", "description": "Optional JSON body to send. Omit or pass {} if no body is needed."},
            "headers": {
                "type": "object",
                "description": "Optional HTTP headers.",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["url"],
    }

    def execute(
        self,
        url: str,
        body: dict | None = None,
        headers: dict | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        import httpx

        start = time.monotonic()
        timeout = settings.AGENT_TOOL_TIMEOUT_SECONDS
        max_chars = settings.MAX_TOOL_OUTPUT_CHARS
        try:
            resp = httpx.post(
                url, json=body or {}, headers=headers or {}, timeout=timeout, follow_redirects=True
            )
            response_body = resp.text
            if len(response_body) > max_chars:
                response_body = response_body[:max_chars] + "\n...[truncated]"
            return ToolResult(
                output={"status_code": resp.status_code, "body": response_body},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:
            return ToolResult(
                output=None,
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
