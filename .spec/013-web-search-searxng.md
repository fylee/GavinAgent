# 013 — Web Search via SearXNG

## Goal

Give the agent a proper **web search** capability so it can discover relevant URLs
instead of guessing them. Currently the agent can only read known URLs (`web_read`)
or call APIs (`api_get`). Adding a `web_search` tool backed by a self-hosted
SearXNG instance lets the LLM search first, then read.

## Background

The agent frequently needs to look up current information (earnings calls, news,
documentation). Without a search tool, the LLM must guess full URLs, which often
leads to:

- Wrong websites (e.g. Yahoo Finance blocking Jina reader)
- Missing results (guessed URL doesn't exist)
- No ability to retry with different keywords

SearXNG is a free, self-hosted meta-search engine that aggregates results from
Google, Bing, DuckDuckGo, and others. It exposes a JSON API that the agent can
call programmatically.

## Proposed Solution

### New tool: `web_search`

| Field | Value |
|---|---|
| Name | `web_search` |
| File | `agent/tools/search.py` |
| Approval | Auto |
| Backend | SearXNG JSON API |
| Parameters | `query` (required), `num_results` (default 10, max 20), `language` (default "auto") |

### Infrastructure

- SearXNG added to `docker-compose.yml` on port `8888`
- Settings file `searxng-settings.yml` mounted read-only, enables JSON format
- Setting `SEARXNG_URL` in Django settings (default `http://localhost:8888`)

### Expected agent workflow

```
Round 1: web_search("台積電 2024 Q4 法說會") → [url1, url2, ...]
Round 2: web_read(url1) → full content
Round 3: 💬 Final answer using the content
```

## Out of Scope

- Replacing `web_read` or `api_get` — they remain for direct URL access
- Paid search APIs (Serper, Tavily) — can be added later if SearXNG quality is insufficient
- Search result caching — not needed at current usage scale

## Open Questions

None — implementation complete.
