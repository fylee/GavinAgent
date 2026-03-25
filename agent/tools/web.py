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

        content = None

        # 1. Try Jina reader first (produces clean markdown)
        try:
            resp = httpx.get(
                f"https://r.jina.ai/{url}",
                headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
                timeout=timeout,
                follow_redirects=True,
            )
            if resp.status_code < 400:
                content = resp.text
        except Exception:
            pass

        # 2. Fallback: direct fetch + trafilatura content extraction
        if not content:
            try:
                import trafilatura

                resp = httpx.get(
                    url,
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
                )
                resp.raise_for_status()
                extracted = trafilatura.extract(
                    resp.text,
                    include_links=True,
                    include_tables=True,
                    output_format="txt",
                )
                content = extracted or resp.text[:max_chars]
            except Exception as e:
                return ToolResult(
                    output=None,
                    error=f"Both Jina reader and direct fetch failed for {url}: {e}",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n...[content truncated at limit — do not re-read this URL]"

        return ToolResult(
            output={"url": url, "content": content},
            duration_ms=int((time.monotonic() - start) * 1000),
        )
