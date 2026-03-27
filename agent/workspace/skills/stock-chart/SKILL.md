---
name: stock-chart
description: >
  Fetch historical stock prices and generate a line chart. Supports any ticker
  symbol on Yahoo Finance. Always use this skill instead of web_read or
  web_search for stock price queries that need a chart.
approval_required: false
tools: [run_skill]
triggers:
  - stock price
  - stock chart
  - share price
  - ticker
  - 股價
  - 股票
examples:
  - "what is TSMC's stock price in the past 5 days and draw a chart?"
  - "show me Apple's stock chart for the last month"
  - "draw a chart of NVDA stock price over 30 days"
  - "華邦電股價走勢"
  - "compare AAPL and MSFT stock prices"
---

## How to use

Call the `run_skill` tool with `skill_name="stock-chart"` and `input` set to
a natural-language request describing the ticker(s) and time period.

```
run_skill(skill_name="stock-chart", input="TSMC stock price past 5 days")
run_skill(skill_name="stock-chart", input="AAPL 1 month")
run_skill(skill_name="stock-chart", input="2330.TW 10 days")
```

The handler will:
1. Resolve the ticker symbol (supports company names and common aliases)
2. Fetch historical close prices from Yahoo Finance via `yfinance`
3. Generate a line chart image
4. Return markdown with the chart and a data summary

Do NOT use `web_read`, `web_search`, or `chart` manually for stock chart
requests. This skill handles everything end-to-end.

After `run_skill` returns successfully, compose your final answer immediately
using the result. Do NOT call any additional tools (no `file_read`, `file_write`,
or memory updates). The skill output already contains the chart and data summary —
just include it in your reply.
