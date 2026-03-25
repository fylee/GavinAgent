from __future__ import annotations

import time
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web using a search engine and return a list of results with "
        "titles, URLs, and snippets. Use this FIRST to find relevant pages, then "
        "use web_read to fetch the full content of a specific result. "
        "Tips: use English keywords for international topics, use the target "
        "language for local topics (e.g. Chinese for Taiwan stocks)."
    )
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query. Be specific and use relevant keywords.",
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (default 10, max 20).",
            },
            "language": {
                "type": "string",
                "description": (
                    "Search language hint, e.g. 'zh-TW' for Traditional Chinese, "
                    "'en' for English, 'ja' for Japanese. Defaults to 'auto'."
                ),
            },
        },
        "required": ["query"],
    }

    def execute(
        self,
        query: str,
        num_results: int = 10,
        language: str = "auto",
        **kwargs: Any,
    ) -> ToolResult:
        import httpx

        start = time.monotonic()
        base_url = getattr(settings, "SEARXNG_URL", "http://localhost:8888")
        timeout = getattr(settings, "AGENT_TOOL_TIMEOUT_SECONDS", 30)
        num_results = max(1, min(num_results or 10, 20))

        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "categories": "general",
        }
        if language and language != "auto":
            params["language"] = language

        try:
            resp = httpx.get(
                f"{base_url}/search",
                params=params,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            return ToolResult(
                output=None,
                error=(
                    f"Cannot connect to SearXNG at {base_url}. "
                    "Make sure the SearXNG container is running "
                    "(docker compose up -d searxng)."
                ),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:
            return ToolResult(
                output=None,
                error=f"Search failed: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        raw_results = data.get("results", [])[:num_results]

        results = []
        for r in raw_results:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "engine": r.get("engine", ""),
            })

        if not results:
            return ToolResult(
                output={"query": query, "results": [], "total": 0},
                error="No results found. Try different keywords or language.",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        return ToolResult(
            output={
                "query": query,
                "results": results,
                "total": len(results),
            },
            duration_ms=int((time.monotonic() - start) * 1000),
        )
