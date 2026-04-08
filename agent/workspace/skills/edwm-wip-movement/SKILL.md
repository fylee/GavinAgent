---
name: edwm-wip-movement
description: EDWM WIP and Movement/Move queries for CT (Taichung) and KH (Kaohsiung) FAB — table selection, real column names, and prod_move conventions
triggers: [wip, movement, move, lot_move, wafer_move, step_move, mvmt, fab move, daily move, shift move, taichung move, ct move, ct wip, kh move, kh wip, kaohsiung move, 台中 wip, 台中 movement, 台中 move, 高雄 move, 高雄 wip, 移動量, 進出量]
examples:
  - "昨日台中廠 WIP movement"
  - "search EDWM for movement yesterday in Taichung FAB"
  - "Taichung CT daily wafer move by lot_type"
  - "台中廠昨日各 lot_type 移動量"
  - "CT FAB prod_move yesterday"
  - "sum_step_move_prod yesterday Taichung"
  - "KH FAB movement last 7 days"
version: 2
---

## EDWM WIP / Movement Queries — CT (Taichung) & KH (Kaohsiung) FAB

### Confirmed table schema — `sum_step_move_prod`

Verified from live query:
```sql
SELECT lot_type, data_date, SUM(prod_move)
FROM <catalog>.reportuser.sum_step_move_prod
GROUP BY lot_type, data_date
```

**Key columns:**
| Column | Description |
|--------|-------------|
| `lot_type` | Lot classification (e.g. PROD, ENG, …) |
| `data_date` | Production date |
| `prod_move` | Movement count — **this is the correct move column** (NOT `move_qty`) |

> ⚠️ The move column is `prod_move`, **not** `move_qty`. Do not use `move_qty` or `wip_qty` — they do not exist in this table.

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
   **07:00 → 07:00 (next day)**, so `data_date = CURRENT_DATE - 1` in
   Asia/Taipei.

2. **"Movement" always means production lots** — filter `lot_type = 'PROD'`
   unless the user explicitly asks for all lot types.

3. **Do not guess column names** — if unsure about other columns (e.g. `step_id`,
   `fab_id`), use `get_logical_table_detail` to confirm before writing the query.

---

### Standard query patterns

```sql
-- CT (Taichung): daily prod movement by date, yesterday
SELECT lot_type, data_date, SUM(prod_move) AS total_move
FROM ctfabrpt.reportuser.sum_step_move_prod
WHERE data_date = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND lot_type = 'PROD'
GROUP BY lot_type, data_date
ORDER BY data_date;
```

```sql
-- KH (Kaohsiung): daily prod movement by date, yesterday
SELECT lot_type, data_date, SUM(prod_move) AS total_move
FROM khfabrpt.reportuser.sum_step_move_prod
WHERE data_date = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND lot_type = 'PROD'
GROUP BY lot_type, data_date
ORDER BY data_date;
```

```sql
-- Both FABs combined: union CT + KH
SELECT 'CT' AS fab, lot_type, data_date, SUM(prod_move) AS total_move
FROM ctfabrpt.reportuser.sum_step_move_prod
WHERE data_date = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND lot_type = 'PROD'
GROUP BY lot_type, data_date
UNION ALL
SELECT 'KH' AS fab, lot_type, data_date, SUM(prod_move) AS total_move
FROM khfabrpt.reportuser.sum_step_move_prod
WHERE data_date = DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY
  AND lot_type = 'PROD'
GROUP BY lot_type, data_date
ORDER BY fab, data_date;
```

---

### Do NOT use these for WIP/Movement

- `move_qty`, `wip_qty` — these columns do **not** exist in `sum_step_move_prod`.
- `ctfabrpt.reportuser.lot_input` — this is for **wafer starts (lot input)**, not movement.
- Raw ES data dictionary search results — always verify exact column names with
  `get_logical_table_detail` before writing a query.

---

### Search strategy

When the user asks about EDWM movement or WIP, **do not scatter-search** the
data dictionary with many individual keywords. Instead:

1. Go directly to `ctfabrpt.reportuser.sum_step_move_prod` (CT) or `khfabrpt.reportuser.sum_step_move_prod` (KH) — the table is already known.
2. Use `get_logical_table_id_by_name` with `"sum_step_move_prod"` only if you need the logical table ID for `get_logical_table_detail`.
3. Write and execute the Trino SQL in one round using the confirmed column names above.

This avoids the multi-round keyword explosion seen with generic `"move"` / `"movement"` searches.
