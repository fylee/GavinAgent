from __future__ import annotations

import time
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult


class WebReadTool(BaseTool):
    name = "web_read"
    description = (
        "Fetch a web page and return its content as clean, readable markdown. "
        "Use this for news sites, documentation, articles, or any human-readable page. "
        "For JSON/REST APIs use api_get instead."
    )
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL of the web page to read.",
            },
        },
        "required": ["url"],
    }

    def execute(self, url: str, **kwargs: Any) -> ToolResult:
        import httpx

        start = time.monotonic()
        max_chars = getattr(settings, "MAX_TOOL_OUTPUT_CHARS", 8000)
        timeout = getattr(settings, "AGENT_TOOL_TIMEOUT_SECONDS", 30)

        reader_url = f"https://r.jina.ai/{url}"
        headers = {
            "Accept": "text/plain",
            "X-Return-Format": "markdown",
        }

        try:
            resp = httpx.get(
                reader_url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
            content = resp.text
            if len(content) > max_chars:
                content = content[:max_chars] + "\n\n...[content truncated at limit — do not re-read this URL]"
            return ToolResult(
                output={"url": url, "content": content},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:
            return ToolResult(
                output=None,
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
