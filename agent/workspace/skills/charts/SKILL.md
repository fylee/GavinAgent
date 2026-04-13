---
name: charts
description: Generate bar, line, pie, and scatter charts from data. Use when visualising data would help the user understand results better than a text table, or when the user explicitly asks for a chart, graph, or plot.
allowed-tools: Bash
metadata:
  triggers: "chart | graph | plot | visuali | bar chart | pie chart | line chart | scatter"
  version: "1"
---

## Charts

### When to use

When visualising data would help the user understand the answer, use the `chart` tool to generate an image instead of showing a text table.

### How to use the chart tool

1. Call `chart` with the data and chart type.
2. The tool returns a `markdown` field — embed it directly in your reply:
   ```
   ![Chart title](url)
   ```
3. Always include the chart markdown in your response so the user can see the image.

### Chart types

Supported: `bar`, `line`, `pie`, `scatter`.

- **bar** — comparing discrete categories (e.g. population by city, sales by product)
- **line** — trends over time (e.g. monthly revenue, temperature over days)
- **pie** — part-to-whole relationships with a small number of categories (≤ 8)
- **scatter** — correlation between two numeric variables

### After generating a chart

Always follow the chart with a brief analysis:
- Which item is largest / smallest?
- Any outliers or surprising patterns?
- What does the distribution suggest?

Do not just embed the image and stop — the chart should be accompanied by insight.
