---
name: edwm-wip-movement
description: Query EDWM WIP and movement data for CT (Taichung) and KH (Kaohsiung) FAB. Use for fab production move counts, lot movement queries, and daily move summaries.
compatibility: Requires EDWM MCP server (SSE transport)
metadata:
  triggers: "edwm | wip | movement | move | prod move | production move | move count | lot_move | wafer_move | wafer movement | step_move | mvmt | fab move | fab wip | daily move | shift move | taichung move | ct move | ct wip | kh move | kh wip | kaohsiung move"
  examples: "search EDWM for movement yesterday in Taichung FAB | Taichung CT daily wafer move by lot_type | CT FAB prod_move yesterday | sum_step_move_prod yesterday Taichung | KH FAB movement last 7 days | how many wafers moved in KH yesterday | CT daily production move count"
  version: "6"
---

## EDWM WIP / Movement Queries -- CT (Taichung) & KH (Kaohsiung) FAB

### Overview / Key conventions

- Table: `sum_step_move_prod` in `ctfabrpt.reportuser` (CT) or `khfabrpt.reportuser` (KH)
- Move column: `prod_move` -- **not** `move_qty` or `wip_qty` (those columns do not exist)
- Production lot filter: `lot_type IN ('P', 'PE')` -- **not** `'PROD'`
- Date column: `data_date` is a **timestamp** -- always use `DATE(data_date)` in WHERE clauses
- "Yesterday" in EDWM = `DATE(NOW() AT TIME ZONE 'Asia/Taipei') - INTERVAL '1' DAY`
- Default to CT (Taichung) if the user does not specify a FAB; note the assumption
- Do not guess column names -- use `get_logical_table_detail` to confirm any column not listed here

---

### Confirmed table schema -- `sum_step_move_prod`

Verified from live query:
```sql
SELECT lot_type, data_date, SUM(prod_move)
FROM ctfabrpt.reportuser.sum_step_move_prod
GROUP BY lot_type, data_date
```

**Key columns:**
| Column | Description |
|--------|-------------|
| `lot_type` | Lot classification -- production lots are `'P'` and `'PE'` |
| `data_date` | Production date -- stored as **timestamp**; cast with `DATE(data_date)` when filtering |
| `prod_move` | Movement count -- **this is the correct move column** (NOT `move_qty`) |

> The move column is `prod_move`, **not** `move_qty`. Do not use `move_qty` or `wip_qty` -- they do not exist in this table.
> Production lot filter is `lot_type IN ('P', 'PE')` -- **not** `'PROD'`.

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

- `move_qty`, `wip_qty` -- these columns do **not** exist in `sum_step_move_prod`.
- `ctfabrpt.reportuser.lot_input` -- this is for **wafer starts (lot input)**, not movement.
- `data_date = <date>` without `DATE()` cast -- `data_date` is a timestamp; bare equality returns 0 rows.
- `lot_type = 'PROD'` -- production lots are `'P'` and `'PE'`, not `'PROD'`.

---

### Search strategy

When the user asks about EDWM movement or WIP, **do not scatter-search** the
data dictionary with many individual keywords. Instead:

1. Go directly to `ctfabrpt.reportuser.sum_step_move_prod` (CT) or `khfabrpt.reportuser.sum_step_move_prod` (KH) -- the table is already known.
2. Use `get_logical_table_id_by_name` with `"sum_step_move_prod"` only if you need the logical table ID for `get_logical_table_detail`.
3. Write and execute the Trino SQL **in one round** using the confirmed column names above.

This avoids the multi-round investigation seen when the date filter returns 0 rows.

---

### When EDWM tools are not in your tool list

**Step 1 — Check your actual tool list first.**
Look at the tools available to you in this session. If you can see `edwm__execute_query` or any other `edwm__*` tool, **use it immediately** — do not consult the MCP status section. The status section is only a fallback for when `edwm__*` tools are genuinely absent.

**Step 2 — Only if `edwm__*` tools are absent**, check the **MCP Server Status** section of your system context for the `edwm` server:

- **"connected — N tools available"** — tools are registered but may not have propagated yet. Try calling `edwm__execute_query` directly; if it works, proceed. If it fails, treat as "tools loading".
- **"connected — tools loading"** — the server just restarted; tools haven't populated yet. Tell the user: *"EDWM is connecting, please retry in ~15 seconds."* Do NOT say "not connected".
- **"disconnected"** — the server is genuinely offline. Use the **exact** response template below. Do not add SQL, plans, or clarifying questions.
- **Status section absent** — treat as disconnected.

**Disconnected response template** (use verbatim, fill in `[FAB]` and `[period]` from the user's request):
> EDWM MCP is not connected — I can't query [FAB] movement data right now.
> Please reconnect it via **GavinAgent → MCP settings**, then ask me again and I'll run the query immediately.

**Do NOT:**
- Show SQL, execution plans, or "what I will run once connected" — that's noise while the user is blocked
- Ask clarifying questions (P+PE combined? CT only?) — answer those after reconnection when you can actually run the query
- Say "the skill handler isn't available" — this confuses skill handlers with MCP
- Offer to chart "cached data" — you have no cached EDWM data
- Tell the user to "open MCP settings" if the status shows "tools loading" — just ask them to retry
- Refuse to call EDWM tools based on the MCP status section alone — always check your actual tool list first

---

### MCP connection error handling

EDWM MCP uses SSE transport with server-side session management. Sessions can expire mid-conversation, causing all tool calls to fail silently with a misleading error code.

**Error signatures that indicate a dead session (not a SQL problem):**

| Error | Meaning |
|-------|---------|
| `MCP error -32602: Invalid request parameters` | Session expired server-side; `-32602` is mis-used as a catch-all |
| `ClosedResourceError` | SSE stream was closed by the server |

**How to confirm it's a session issue, not a SQL issue:**
- Call `get_current_date` (no parameters). If it also returns `-32602`, the session is dead -- it cannot be a parameter problem.

**Recovery steps (Claude cannot auto-reconnect -- no CLI restart command exists):**

1. Tell the user: "EDWM MCP session has expired. Please reconnect:"
   ```
   Option A: Restart Claude Code -- the only reliable fix; forces a fresh SSE handshake
   Option B: Click the MCP icon in Claude Code -> reconnect EDWM MCP (if available in your version)
   ```
   > Do NOT suggest `! claude mcp list` -- it only pings the SSE endpoint and does not re-establish the session.
2. After the user confirms reconnection, **retry the original query immediately** -- do not ask the user to repeat themselves.
3. If the session keeps expiring mid-conversation, ask the EDWM MCP admin to increase the server-side SSE session timeout.
