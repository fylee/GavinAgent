---
name: stock-chart
description: Fetch historical stock prices and generate a line chart for any ticker on Yahoo Finance. Use this skill when the user asks for a stock price chart, share price history, or ticker performance over a time period.
allowed-tools: Bash
compatibility: Requires internet access to Yahoo Finance
metadata:
  triggers: "stock price | stock chart | share price | ticker | price history | stock performance | candlestick | equity chart"
  trigger_patterns: "\\b(stock|share)\\s+(price|chart|history)\\b ;; \\b[A-Z]{1,5}(\\.[A-Z]{1,3})?\\b.*(chart|price|stock) ;; draw.*chart.*stock|stock.*chart"
  examples: "what is TSMC's stock price in the past 5 days and draw a chart? | show me Apple's stock chart for the last month | draw a chart of NVDA stock price over 30 days | compare AAPL and MSFT stock prices | 2330.TW price history 10 days"
  version: "1"
  approval_required: "false"
---

## Overview / Key conventions

- Supports any ticker symbol listed on Yahoo Finance (e.g. `AAPL`, `TSMC`, `2330.TW`, `005930.KS`)
- Taiwan Stock Exchange tickers use `.TW` suffix (e.g. `2330.TW` for TSMC)
- Company name aliases are resolved automatically (e.g. "Apple" → `AAPL`, "TSMC" → `TSM` or `2330.TW`)
- Default period when not specified: **30 days**
- Output contains: a line chart image (markdown embed) + a data table with date and closing price
- Data source: Yahoo Finance via `yfinance` Python library — prices are end-of-day close
- Multi-ticker comparison is supported: pass comma-separated tickers or a natural-language request like "compare AAPL and MSFT"
- Pre-market / after-hours prices are NOT available — close prices only

## Standard query patterns

Call `run_skill` with a natural-language `input` describing the ticker(s) and time period:

```
run_skill(skill_name="stock-chart", input="TSMC stock price past 5 days")
run_skill(skill_name="stock-chart", input="AAPL 1 month")
run_skill(skill_name="stock-chart", input="2330.TW 10 days")
run_skill(skill_name="stock-chart", input="compare AAPL and MSFT over 3 months")
```

The skill will:
1. Resolve ticker symbol(s) from company names or aliases
2. Fetch historical close prices from Yahoo Finance via `yfinance`
3. Generate a line chart image
4. Return markdown containing the chart and a price data summary

## Do NOT use

- Do NOT use `web_read` or `web_search` for stock price queries — this skill handles Yahoo Finance end-to-end
- Do NOT use the `chart` skill manually for stock data — use this skill instead
- Do NOT call additional tools after `run_skill` returns — the output already contains the chart and data summary; compose your reply directly from it
- Do NOT attempt to fetch `finance.yahoo.com` URLs directly — the `yfinance` library is the correct access method

## Search strategy

1. When the user asks about a stock price or chart, invoke this skill immediately — do not search the web first.
2. Call `run_skill(skill_name="stock-chart", input="<user request verbatim or lightly paraphrased>")`.
3. When the skill returns, include the chart image and data table in your reply.
4. Add a brief insight: mention the highest/lowest price in the period, the overall trend (up/down/flat), and any notable movement.
5. If the ticker cannot be resolved, ask the user to confirm the exact ticker symbol (e.g. `2330.TW` vs `TSM`).
