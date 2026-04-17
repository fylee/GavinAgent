# 031 — Token Usage in Trace

## Goal

Display per-round and cumulative token counts (prompt / completion / cost) inside the agent run trace, so users can see how much of their token budget each reasoning round consumes and what the total run cost is.

---

## Background

### What exists today

| Component | Location | Status |
|-----------|----------|--------|
| `LLMUsage` model | `agent/models.py` | ✅ Records tokens per LLM call, linked to `AgentRun` via FK |
| `_record_usage()` | `core/llm.py:37` | ✅ Reads `response.usage`, saves `prompt_tokens`, `completion_tokens`, `total_tokens`, `estimated_cost_usd` |
| `loop_trace` entries | `agent/graph/nodes.py` | ✅ `round`, `decision`, `tools`, `reasoning`, `llm_ms`, `tool_wall_ms` — **no token fields** |
| Trace UI | `chat/templates/chat/_tool_progress.html:87` | Shows `🧠 llm_ms` — **no token display** |

### What is missing

- The `loop_trace` entries do not carry token data, so the UI cannot show per-round usage.
- Token totals for the whole run are only queryable via `LLMUsage.objects.filter(run=run)` — not available in `graph_state` for the polling view.
- There is no summary row at the end of the trace showing total cost for the run.

### Why `graph_state` and not DB joins

The trace is rendered by an HTMX polling endpoint (`chat/views.py:MessageStreamView`) that already reads `graph_state`. Adding a DB join for every 1-second poll would increase query load. Storing derived totals in `graph_state` is consistent with how `loop_trace` and `rag_matches` already work.

---

## Proposed Solution

### Architecture overview

```
_handle_llm_response()
  → extract token counts from response.usage
  → add to trace_entry: prompt_tokens, completion_tokens, cost_usd
  → _persist_loop_trace() — unchanged, writes full entry incl. tokens

_update_token_totals(run_id, loop_trace)   ← NEW helper
  → sum prompt_tokens + completion_tokens + cost_usd across all entries
  → write graph_state["token_totals"] = {prompt, completion, total, cost_usd}
  → called after every _persist_loop_trace() that adds a new entry

Template: per-round token pill + run total footer row
```

---

## Detailed Design

### 1. `agent/graph/nodes.py` — Extend `_handle_llm_response()`

#### 1a. Extract token data from `response.usage`

After `llm_ms` is computed (response already available), extract:

```python
_usage = getattr(response, "usage", None)
_prompt_tokens: int = getattr(_usage, "prompt_tokens", 0) or 0
_completion_tokens: int = getattr(_usage, "completion_tokens", 0) or 0
try:
    import litellm as _litellm
    _cost_usd: float = _litellm.completion_cost(completion_response=response) or 0.0
except Exception:
    _cost_usd = 0.0
```

#### 1b. Add token fields to `trace_entry`

Both the `tool_call` branch (line ~861) and the `answer` branch (line ~908) build a `trace_entry` dict. Add three new fields to each:

```python
trace_entry = {
    "round": current_round,
    "decision": "tool_call",          # or "answer"
    ...existing fields...,
    "prompt_tokens": _prompt_tokens,
    "completion_tokens": _completion_tokens,
    "cost_usd": _cost_usd,
}
```

#### 1c. New helper: `_update_token_totals(run_id, loop_trace)`

```python
def _update_token_totals(run_id: str, loop_trace: list[dict]) -> None:
    """Sum token counts across all loop_trace entries and persist to graph_state."""
    try:
        from agent.models import AgentRun
        prompt = sum(e.get("prompt_tokens") or 0 for e in loop_trace)
        completion = sum(e.get("completion_tokens") or 0 for e in loop_trace)
        cost = sum(e.get("cost_usd") or 0.0 for e in loop_trace)
        totals = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "cost_usd": round(cost, 6),
        }
        gs = (AgentRun.objects.values_list("graph_state", flat=True)
              .filter(pk=run_id).first()) or {}
        gs["token_totals"] = totals
        AgentRun.objects.filter(pk=run_id).update(graph_state=gs)
    except Exception:
        pass
```

Call `_update_token_totals(run_id, loop_trace)` immediately after every `_persist_loop_trace()` call in `_handle_llm_response()` and `mark_forced_conclude()`.

---

### 2. `agent/graph/state.py` — Document new fields (no code change)

The `GraphState` TypedDict already has `loop_trace: list[dict]`. Add a comment noting the new optional fields in each entry:

```python
loop_trace: list[dict]
# Per-round: {round, decision, tools, reasoning, llm_ms, tool_wall_ms,
#             tool_count, forced, ts, elapsed_s,
#             prompt_tokens, completion_tokens, cost_usd}   ← Spec 031
```

`token_totals` is only in `graph_state` (DB), not in the LangGraph `GraphState` TypedDict. No TypedDict change needed.

---

### 3. `chat/views.py` — Pass `token_totals` to template context

In `MessageStreamView` (or the equivalent polling view), after building `loop_trace_with_tes`, extract `token_totals` from `graph_state` and add it to the template context:

```python
token_totals = graph_state.get("token_totals") or {}
```

Pass `token_totals` to the `_tool_progress.html` partial render context.

---

### 4. `chat/templates/chat/_tool_progress.html` — Display tokens

#### 4a. Per-round token pill (inside the round header `div.flex`)

After the existing `🧠 llm_ms` span, add a token pill when token data is present:

```html
{% if entry.prompt_tokens or entry.completion_tokens %}
<span class="text-[10px] text-emerald-500/70 tabular-nums">
  ↓{{ entry.prompt_tokens }} ↑{{ entry.completion_tokens }}
</span>
{% endif %}
{% if entry.cost_usd %}
<span class="text-[10px] text-amber-500/60 tabular-nums">
  ${{ entry.cost_usd|floatformat:5 }}
</span>
{% endif %}
```

Legend: `↓` = prompt tokens (tokens sent to the model), `↑` = completion tokens (tokens generated).

#### 4b. Run total footer row

After the `{% endfor %}` that closes the loop_trace loop, add a summary row when `token_totals` is populated:

```html
{% if token_totals %}
<div class="flex items-center gap-3 pt-1.5 mt-1.5 border-t border-gray-700/40 text-[10px] text-gray-500 tabular-nums">
  <span class="font-semibold text-gray-400">Run total</span>
  <span>↓{{ token_totals.prompt_tokens }} ↑{{ token_totals.completion_tokens }}</span>
  <span class="text-gray-600">{{ token_totals.total_tokens }} tokens</span>
  {% if token_totals.cost_usd %}
  <span class="text-amber-500/70">${{ token_totals.cost_usd|floatformat:5 }}</span>
  {% endif %}
</div>
{% endif %}
```

---

### 5. Spec 030 streaming path compatibility

Spec 030 (`_streaming_round`) adds a synthetic `pending_entry` to `loop_trace_with_tes` during streaming. That entry does **not** have token fields (tokens are not known until the stream ends). The template guards with `{% if entry.prompt_tokens or entry.completion_tokens %}` already handle the missing-field case gracefully — no change needed for the streaming path.

---

## Data flow summary

```
LLM response
  ↓
_handle_llm_response()
  ├── extract prompt_tokens, completion_tokens, cost_usd from response.usage
  ├── append to trace_entry
  ├── _persist_loop_trace()   → graph_state["loop_trace"]
  └── _update_token_totals()  → graph_state["token_totals"]

HTMX poll (1 s)
  ↓
MessageStreamView
  ├── reads graph_state["loop_trace"]   → per-round pills in template
  └── reads graph_state["token_totals"] → run total footer
```

---

## Out of Scope

- **Streaming token counts mid-round** — token counts are only available after the full response. Per-round tokens appear once the round completes.
- **Cost breakdown by tool** — tool executions don't consume LLM tokens directly; the per-round cost already covers the full round.
- **Historical backfill** — existing `AgentRun` records will not have `token_totals` in their `graph_state`. They can still be computed on demand from `LLMUsage` records if needed, but that is a separate concern.
- **Chat (non-agent) token display** — `process_chat_message` also calls `_record_usage`. Showing tokens in the chat message UI is a separate spec.
- **Model-level cost rate table** — costs come from `litellm.completion_cost()` which already handles per-model pricing. No custom rate table needed.
- **`LLMUsage` schema changes** — the existing `LLMUsage` model is unchanged. Token data in `graph_state` is a derived cache, not a replacement.

---

## Open Questions

1. **`↓ ↑` vs `in/out` labels** — Arrow notation is compact but may not be immediately obvious to all users. Consider a tooltip or legend somewhere on the page.
2. **Cost display precision** — `$0.00012` via `floatformat:5`. Sub-cent costs are meaningful for monitoring; but if the agent runs many rounds they add up. Should we show milli-cents instead (e.g., `0.12¢`)?
3. **Zero-cost models** — When `estimated_cost_usd = 0.0` (Ollama, Azure AI Catalog with flat pricing), suppress the `$` pill entirely to avoid misleading `$0.00000` display. The `{% if entry.cost_usd %}` guard already handles this.

---

## Test Plan

See `.testreport/031-token-usage-in-trace.md` after implementation.

| # | Test | Description |
|---|------|-------------|
| 1 | `test_trace_entry_includes_token_fields` | Mock `response.usage` with known counts; verify `trace_entry` contains `prompt_tokens`, `completion_tokens`, `cost_usd` |
| 2 | `test_trace_entry_zero_tokens_when_no_usage` | `response.usage = None`; verify fields default to `0` / `0.0` without error |
| 3 | `test_update_token_totals_sums_correctly` | Two loop_trace entries with known tokens; verify `graph_state["token_totals"]` sums match |
| 4 | `test_update_token_totals_overwrites` | Called twice; verify second call overwrites, not appends |
| 5 | `test_update_token_totals_noop_on_no_entries` | Empty loop_trace; verify totals are all zero, no exception |
| 6 | `test_message_stream_view_passes_token_totals` | `graph_state` with `token_totals`; verify view context contains it |
| 7 | `test_message_stream_view_no_token_totals_key` | `graph_state` without `token_totals`; verify context has empty dict, no KeyError |
| 8 | `test_template_renders_per_round_tokens` | Render `_tool_progress.html` with entry containing token fields; assert pills present |
| 9 | `test_template_hides_tokens_when_absent` | Entry without token fields; assert no token pill rendered |
| 10 | `test_template_renders_run_total_footer` | Context with `token_totals`; assert footer row present with correct values |
| 11 | `test_template_hides_cost_when_zero` | `cost_usd = 0.0`; assert `$` pill is absent from both round and footer |
| 12 | `test_streaming_entry_no_tokens` | Spec 030 synthetic streaming entry (no token fields); assert no pill, no error |
