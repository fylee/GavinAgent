---
description: Processing tabular data, statistics, and comparisons
---

## Data Analysis

### Approach

- Write a Python script to perform complex computations — do not attempt to compute large datasets mentally.
- Always execute the script and include the result in your reply.
- Use `file_write` to save the script, then `shell` to run it.

### Working with tabular data

- Prefer pandas for data manipulation when the dataset has more than ~20 rows.
- For quick calculations (sums, averages, top-N), a simple Python script without pandas is fine.
- Always verify totals or row counts to confirm you have complete data before drawing conclusions.

### Presenting results

- Lead with the key finding, not the raw data.
- If the dataset has a natural ranking (e.g. largest to smallest), sort it before displaying.
- Pair data tables with a chart when the visual makes patterns clearer — load the `charts` skill if needed.
- Note any gaps, anomalies, or caveats in the data prominently.