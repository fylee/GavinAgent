---
description: Searching the web, fetching URLs, and finding current information
---

## Web Research

### Finding information

- Your training data has a knowledge cutoff. For anything that changes over time (statistics, prices, news, rankings, population data, etc.), always search for the latest information before answering.
- Use `brave-search__brave_web_search` to find relevant sources, then `web_read` to read the full content of the most relevant URLs.
- Do not rely on memorised figures for time-sensitive data — search first.

### Source selection

- When searching for statistics or numerical data, prefer Wikipedia (en.wikipedia.org or zh.wikipedia.org) — tables are clean and readable.
- Avoid government statistics pages (e.g. gov.tw) — they usually require downloading Excel files and cannot be read directly via `web_read`.
- If the first search does not yield clean data, search Wikipedia directly: e.g. `site:en.wikipedia.org <topic>`.

### URL hygiene

- Each URL should be read at most once. If the content is truncated, extract what you can — do not re-read the same URL.
- Stop searching once you have enough data to answer. Do not keep browsing for a "better" source if you already have usable information.
- When the user asks for "all" items (e.g. all counties, all countries), make sure the data is complete before stopping. Partial data with a note is acceptable only if complete data cannot be found after reasonable effort.

### Efficiency

- Process data directly from tool output — do not save raw fetched content to a file just to read it back.
- After fetching, perform the requested analysis immediately or write a script to do it.