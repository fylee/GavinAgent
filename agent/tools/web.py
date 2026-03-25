from __future__ import annotations

import re
import time
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult


def _html_to_text(html: str) -> str:
    """Best-effort HTML → plain text conversion without heavy dependencies."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace common block tags with newlines
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"')
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


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
            if resp.status_code < 400:
                content = resp.text
        except Exception:
            pass

        # 2. Fallback: direct fetch + HTML-to-text
        if not content:
            try:
                resp = httpx.get(
                    url,
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; GavinAgent/1.0)"},
                )
                resp.raise_for_status()
                content = _html_to_text(resp.text)
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
