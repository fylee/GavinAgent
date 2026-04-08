---
name: edwm-wip-movement
description: EDWM WIP and Movement/Move queries for CT (Taichung) and KH (Kaohsiung) FAB - table selection, real column names, and prod_move conventions
triggers: [wip, movement, move, lot_move, wafer_move, step_move, mvmt, fab move, daily move, shift move, taichung move, ct move, ct wip, kh move, kh wip, kaohsiung move]
examples:
  - "search EDWM for movement yesterday in Taichung FAB"
  - "Taichung CT daily wafer move by lot_type"
  - "CT FAB prod_move yesterday"
  - "sum_step_move_prod yesterday Taichung"
  - "KH FAB movement last 7 days"
version: 3
---

## EDWM WIP / Movement Queries ??CT (Taichung) & KH (Kaohsiung) FAB

### Confirmed table schema ??`sum_step_move_prod`

Verified from live query:
```sql
SELECT lot_type, data_date, SUM(prod_move)
FROM <catalog>.reportuser.sum_step_move_prod
GROUP BY lot_type, data_date
```

**Key columns:**
| Column | Description |
|--------|-------------|
| `lot_type` | Lot classification (e.g. PROD, ENG, ?? |
| `data_date` | Production date — stored as **timestamp**; cast with `DATE(data_date)` when filtering |
| `prod_move` | Movement count ??**this is the correct move column** (NOT `move_qty`) |

> ?? The move column is `prod_move`, **not** `move_qty`. Do not use `move_qty` or `wip_qty` ??they do not exist in this table.
> Production lot filter is `lot_type IN ('P', 'PE')` ??**not** `'PROD'`.

---

### Catalog / FAB mapping

| FAB | Catalog | Example full table |
|-----|---------|-------------------|
| **CT (Taichung)** | `ctfabrpt` | `ctfabrpt.reportuser.sum_step_move_prod` |
| **KH (Kaohsiung)** | `khfabrpt` | `khfabrpt.reportuser.sum_step_move_prod` |

- Use `ctfabrpt` for Taichung / CT queries.
- Use `khfabrpt` for Kaohsiung / KH queries.
- If the user does not specify a FAB, default to **CT** and note the assumption.

---

### Key conventions

1. **"Yesterday" means the EDWM production day**: one full day shift runs
   **07:00 -> 07:00 (next day)**, so yesterday = `DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY`.

2. **"Movement" always means production lots** — filter `lot_type IN ('P', 'PE')`
   unless the user explicitly asks for all lot types. Do **not** use `lot_type = 'PROD'`.

3. **`data_date` is a timestamp column** — always wrap with `DATE(data_date)` when filtering,
   e.g. `DATE(data_date) = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY`.
   Comparing a raw timestamp with a date literal returns 0 rows.

4. **Do not guess column names** — if unsure about other columns (e.g. `step_id`,
   `fab_id`), use `get_logical_table_detail` to confirm before writing the query.

---

### Standard query patterns

```sql
-- CT (Taichung): daily prod movement by date, yesterday
SELECT lot_type, DATE(data_date) AS data_date, SUM(prod_move) AS total_move
FROM ctfabrpt.reportuser.sum_step_move_prod
WHERE DATE(data_date) = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND lot_type IN ('P', 'PE')
GROUP BY lot_type, DATE(data_date)
ORDER BY data_date;
```

```sql
-- KH (Kaohsiung): daily prod movement by date, yesterday
SELECT lot_type, DATE(data_date) AS data_date, SUM(prod_move) AS total_move
FROM khfabrpt.reportuser.sum_step_move_prod
WHERE DATE(data_date) = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND lot_type IN ('P', 'PE')
GROUP BY lot_type, DATE(data_date)
ORDER BY data_date;
```

```sql
-- Both FABs combined: union CT + KH
SELECT 'CT' AS fab, lot_type, DATE(data_date) AS data_date, SUM(prod_move) AS total_move
FROM ctfabrpt.reportuser.sum_step_move_prod
WHERE DATE(data_date) = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND lot_type IN ('P', 'PE')
GROUP BY lot_type, DATE(data_date)
UNION ALL
SELECT 'KH' AS fab, lot_type, DATE(data_date) AS data_date, SUM(prod_move) AS total_move
FROM khfabrpt.reportuser.sum_step_move_prod
WHERE DATE(data_date) = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND lot_type IN ('P', 'PE')
GROUP BY lot_type, DATE(data_date)
ORDER BY fab, data_date;
```

---

### Do NOT use these for WIP/Movement

- `move_qty`, `wip_qty` — these columns do **not** exist in `sum_step_move_prod`.
- `ctfabrpt.reportuser.lot_input` — this is for **wafer starts (lot input)**, not movement.
- `data_date = <date>` without `DATE()` cast — `data_date` is a timestamp; bare equality returns 0 rows.

---

### Search strategy

When the user asks about EDWM movement or WIP, **do not scatter-search** the
data dictionary with many individual keywords. Instead:

1. Go directly to `ctfabrpt.reportuser.sum_step_move_prod` (CT) or `khfabrpt.reportuser.sum_step_move_prod` (KH) — the table is already known.
2. Use `get_logical_table_id_by_name` with `"sum_step_move_prod"` only if you need the logical table ID for `get_logical_table_detail`.
3. Write and execute the Trino SQL **in one round** using the confirmed column names above.

This avoids the multi-round investigation seen when the date filter returns 0 rows.
