---
name: web-research
description: Search the web and fetch URLs to find current information. Use when the user asks about recent events, news, prices, statistics, population data, or anything time-sensitive that may not be in training data. Also use when the user provides a URL to read.
metadata:
  triggers: "search | web search | browse | look up | latest | current news | recent news | fetch url | read url | wikipedia | price | population | statistic | what is the latest | find information | news | event | report | update | trend | who is | where is | when did | why did"
  trigger_patterns: "https?:// ;; site:[a-z]+ ;; search for .+ ;; look up .+"
  examples: "What is the current population of Taiwan? | Search for the latest iPhone price | Fetch this URL and summarise it | What happened in the news today? | Find the GDP of South Korea"
  version: "2"
---

## Web Research

### Key conventions

- Your training data has a knowledge cutoff. For anything that changes over time (statistics, prices, news, rankings, population data), always search for the latest information before answering.
- Use `brave-search__brave_web_search` to find relevant sources, then `web_read` to read the full content of the most relevant URLs.
- Do not rely on memorised figures for time-sensitive data — search first.
- Each URL should be read at most once. If content is truncated, extract what you can — do not re-read the same URL.
- Stop searching once you have enough data to answer. Do not keep browsing for a "better" source if you already have usable information.
- When the user asks for "all" items (e.g. all counties, all countries), make sure the data is complete before stopping. Partial data with a note is acceptable only if complete data cannot be found after reasonable effort.

### Standard query patterns

Search for a topic:
```
brave-search__brave_web_search(query="Taiwan population 2025 site:en.wikipedia.org")
```

Read a specific URL:
```
web_read(url="https://en.wikipedia.org/wiki/Taiwan")
```

Search then read the top result:
```
1. brave-search__brave_web_search(query="<topic>")
2. web_read(url=<first relevant result URL>)
```

Direct Wikipedia lookup for statistics:
```
brave-search__brave_web_search(query="site:en.wikipedia.org <topic>")
```

### Source selection

- For statistics or numerical data, prefer Wikipedia (en.wikipedia.org or zh.wikipedia.org) — tables are clean and readable.
- If the first search does not yield clean data, search Wikipedia directly: e.g. `site:en.wikipedia.org <topic>`.
- If `web_read` fails on one URL, try the next URL from your search results — do not give up after a single failed fetch.

### Do NOT use

- **Government statistics pages** (e.g. gov.tw, stat.gov.cn) — they usually require downloading Excel files and cannot be read directly via `web_read`.
- **Re-reading the same URL** — if the content was truncated, extract what you can and move on.
- **Saving raw fetched content to a file** as an intermediate staging step — process data directly from tool output.
- **Memorised figures** for time-sensitive data (prices, population, rankings) — always search first.

### Search strategy

1. Identify whether the request needs a web search or a direct URL fetch.
2. For searches: call `brave-search__brave_web_search` with a precise query. Include `site:en.wikipedia.org` when looking for statistics.
3. Scan result URLs — prefer Wikipedia, news outlets, or authoritative sources over aggregator or government download pages.
4. Call `web_read` on the most relevant URL.
5. If the content is sufficient, answer immediately. If not, try the next URL from the search results.
6. After completing the fetch and analysis, provide a brief insight — highlight the key finding, notable outlier, or most relevant comparison for the user.
