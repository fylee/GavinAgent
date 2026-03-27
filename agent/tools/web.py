from __future__ import annotations

import time
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult


class WebReadTool(BaseTool):
    name = "web_read"
    description = (
        "Fetch a web page and return its content as clean, readable markdown. "
        "IMPORTANT: Only use this when you already have a specific URL (e.g. from "
        "web_search results). If you need to find information but don't know the "
        "exact URL, use web_search first to discover relevant pages."
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

    def _fetch_jina(self, url: str, timeout: float) -> str | None:
        """Try Jina Reader — returns markdown or None."""
        import httpx

        try:
            resp = httpx.get(
                f"https://r.jina.ai/{url}",
                headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
                timeout=timeout,
                follow_redirects=True,
            )
            if resp.status_code < 400 and resp.text.strip():
                return resp.text
        except Exception:
            pass
        return None

    def _fetch_direct(self, url: str, timeout: float, max_chars: int) -> str | None:
        """Direct fetch + trafilatura extraction — returns text or None."""
        import httpx

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
            return extracted or resp.text[:max_chars]
        except Exception:
            return None

    def execute(self, url: str, **kwargs: Any) -> ToolResult:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        start = time.monotonic()
        max_chars = getattr(settings, "MAX_TOOL_OUTPUT_CHARS", 8000)
        timeout = getattr(settings, "AGENT_TOOL_TIMEOUT_SECONDS", 30)
        jina_timeout = min(timeout, 10)  # cap Jina at 10s — it's fast or it won't work

        content = None

        # Race Jina vs direct fetch in parallel — use whichever finishes first
        # with a valid result.  This eliminates the sequential waterfall.
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_jina = pool.submit(self._fetch_jina, url, jina_timeout)
            future_direct = pool.submit(self._fetch_direct, url, timeout, max_chars)

            for future in as_completed([future_jina, future_direct], timeout=timeout + 2):
                result = future.result()
                if result:
                    content = result
                    break

        if not content:
            return ToolResult(
                output=None,
                error=(
                    f"Both Jina reader and direct fetch failed for {url}. "
                    "This site may block automated access. Try web_read on a "
                    "different URL from your search results instead."
                ),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n...[content truncated at limit — do not re-read this URL]"

        return ToolResult(
            output={"url": url, "content": content},
            duration_ms=int((time.monotonic() - start) * 1000),
        )
