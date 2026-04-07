---
name: edwm-wip-movement
description: EDWM WIP and Movement/Move queries for Taichung FAB — table selection, time window, and prod-only conventions
triggers: [wip, movement, move, lot_move, wafer_move, step_move, mvmt, fab move, daily move, shift move, taichung move, ct move, ct wip, 台中 wip, 台中 movement, 台中 move, 移動量, 進出量]
examples:
  - "昨日台中廠 WIP movement"
  - "search EDWM for movement yesterday in Taichung FAB"
  - "Taichung CT daily wafer move by shift"
  - "台中廠昨日各時段 WIP 流動"
  - "CT FAB move by 4-hour bucket"
  - "sum_step_move_prod yesterday Taichung"
version: 1
---

## EDWM WIP / Movement Queries — Taichung (CT) FAB

### Key conventions

1. **"Yesterday" means the EDWM production day**: one full day shift runs
   **07:00 → 07:00 (next day)**, so `data_date = CURRENT_DATE - 1` in
   Asia/Taipei. Use `wf_start_dt` or `data_date` depending on the table.

2. **"Movement" or "Move" always means production lots only** (`prod_type = 'PROD'`
   or equivalent filter). Engineering (E) lots are excluded unless the user
   explicitly asks for them.

3. **Default FAB filter**: Taichung = `fab_id = 'CT'` or `site = 'CT'` or
   `ww_fab = 'CT'` depending on the table schema. Always confirm the exact
   column name with `get_logical_table_detail` or `sample_table_data` before
   writing the WHERE clause.

---

### Table selection guide

| Need | Recommended table | Schema | Notes |
|------|-------------------|--------|-------|
| **Daily or shift (4-hour bucket) WIP flow & output** | `ctfabrpt.reportuser.rpt_sum_mvmt4hrs` | `data_date`, `shift_id` (0711, 1115, 1519, 1923, 2303, 0307), `fab_id`, `prod_type` | Best for per-shift breakdown |
| **Daily prod movement summary (step-level)** | `ctfabrpt.reportuser.sum_step_move_prod` | `data_date`, `fab_id`, `step_id`, `prod_type = 'PROD'` | Use for day-total move counts per step; already prod-filtered by design but add `prod_type` filter to be safe |
| **Hourly WIP + Movement + Rework + Hold (fine-grained)** | `ctfabrpt.reportuser.sum_step_wip_move_prod` | `data_date`, `hour_id`, `fab_id`, `prod_type` | Richest table — WIP, move, rework, hold all in one; use when per-hour granularity is needed |

> **Default choice**: unless the user asks for hourly data, start with
> `sum_step_move_prod` (daily totals) or `rpt_sum_mvmt4hrs` (shift breakdown).
> Avoid `sum_step_wip_move_prod` unless the user explicitly needs per-hour detail,
> because it is much larger and slower.

---

### Shift ID reference (`rpt_sum_mvmt4hrs`)

| `shift_id` | Time window (Asia/Taipei) |
|------------|--------------------------|
| `0711` | 07:00 – 11:00 |
| `1115` | 11:00 – 15:00 |
| `1519` | 15:00 – 19:00 |
| `1923` | 19:00 – 23:00 |
| `2303` | 23:00 – 03:00 (+1 day) |
| `0307` | 03:00 – 07:00 |

---

### Standard query pattern

```sql
-- Daily prod movement total for Taichung CT, yesterday
SELECT
    data_date,
    fab_id,
    SUM(move_qty)   AS total_move,
    SUM(wip_qty)    AS total_wip,
    COUNT(DISTINCT lot_id) AS lot_count
FROM ctfabrpt.reportuser.sum_step_move_prod
WHERE data_date = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND fab_id    = 'CT'
  AND prod_type = 'PROD'
GROUP BY data_date, fab_id
ORDER BY data_date;
```

```sql
-- Shift breakdown (4-hour buckets), yesterday, Taichung
SELECT
    shift_id,
    SUM(move_qty) AS move,
    SUM(wip_qty)  AS wip
FROM ctfabrpt.reportuser.rpt_sum_mvmt4hrs
WHERE data_date = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND fab_id    = 'CT'
  AND prod_type = 'PROD'
GROUP BY shift_id
ORDER BY shift_id;
```

---

### Do NOT use these for WIP/Movement

- `ctfabrpt.reportuser.lot_input` — this is for **wafer starts (lot input)**, not movement.
- Raw ES data dictionary search results — always verify the exact table/column
  names with `get_logical_table_detail` before writing a query.

---

### Search strategy

When the user asks about EDWM movement or WIP, **do not scatter-search** the
data dictionary with many individual keywords. Instead:

1. Search with 2–3 broad terms in parallel: `"sum_step_move_prod"`, `"rpt_sum_mvmt4hrs"`, `"sum_step_wip_move_prod"`.
2. Use `get_logical_table_detail` on the matching table to confirm column names.
3. Write and execute the Trino SQL in one round.

This avoids the multi-round keyword explosion seen with generic `"move"` / `"movement"` searches.
