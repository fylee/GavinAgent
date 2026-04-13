---
name: charts
description: >
  Generate bar, line, pie, and scatter charts from tabular data and embed the
  image in the reply. Use when the user asks for a chart, graph, plot, or
  visualisation, or when displaying data as a chart would be clearer than a
  table.
allowed-tools: Bash
metadata:
  triggers: "chart | graph | plot | visuali | bar chart | pie chart | line chart | scatter | histogram | visualise | visualize"
  trigger_patterns: "show.*chart ;; plot.*data ;; draw.*graph ;; visuali(z|s)e ;; make.*chart"
  examples: "show me a bar chart of sales by region | plot revenue over time | pie chart of market share | scatter plot of height vs weight | visualise this data as a line chart"
  version: "2"
---

## Charts

### Overview

- Use this skill whenever the user asks for a chart, graph, plot, or visualisation of data.
- Supported chart types: `bar`, `line`, `pie`, `scatter`.
- Generate the chart by writing and executing a Python script via Bash using `matplotlib`.
- Save the output image to a temp file and display it using Markdown image syntax.
- Always follow the chart with a brief analysis — do not just embed the image.

### Chart type selection

- **bar** — comparing discrete categories (e.g. population by city, sales by product)
- **line** — trends over time (e.g. monthly revenue, temperature over days)
- **pie** — part-to-whole relationships with a small number of categories (≤ 8)
- **scatter** — correlation between two numeric variables

### Standard query patterns

Write a Python script and execute it with Bash. Example for a bar chart:

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

labels = ['Category A', 'Category B', 'Category C']
values = [42, 78, 35]

fig, ax = plt.subplots()
ax.bar(labels, values)
ax.set_title('Sales by Category')
ax.set_ylabel('Value')
plt.tight_layout()
plt.savefig('chart_output.png', dpi=150)
print('Saved: chart_output.png')
```

Then embed in your reply:

```
![Sales by Category](chart_output.png)
```

For a line chart, replace `ax.bar` with `ax.plot`. For a pie chart, use `ax.pie(values, labels=labels, autopct='%1.1f%%')`. For a scatter plot, use `ax.scatter(x_values, y_values)`.

### Search strategy

1. Identify the chart type from the user's request (bar/line/pie/scatter).
2. Extract labels and values from the user's data or prior tool output.
3. Write the matplotlib Python script, save to `chart_output.png`.
4. Run the script via Bash: `python chart_script.py`
5. Embed the image in your reply using Markdown.
6. Follow with a 2–3 sentence analysis highlighting the key finding.

### After generating a chart

Always follow the chart with a brief analysis:
- Which item is largest / smallest?
- Any outliers or surprising patterns?
- What does the trend or distribution suggest?

Do not just embed the image and stop — the chart must be accompanied by insight.

### Do NOT use

- Do NOT attempt to render charts as ASCII art or text tables — always generate a real image.
- Do NOT use `plt.show()` — it blocks execution in a headless environment; use `plt.savefig()` instead.
- Do NOT use `matplotlib.use('TkAgg')` or any interactive backend — always use `'Agg'`.
- Do NOT assume a `chart` tool exists as a named function — generate charts via Python + matplotlib through Bash.
- Do NOT generate a pie chart with more than 8 slices — aggregate smaller categories into "Other".
