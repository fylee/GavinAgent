# 030 — Streaming Reasoning Display

## Goal

Show the LLM's reasoning / thinking text in the trace **as it is generated**, rather than waiting for the complete response. Users currently see a blank trace until the entire LLM response finishes (which may take 10–60+ seconds for multi-tool reasoning rounds).

---

## Background

### Current flow

```
call_llm()
  → get_completion(...)       # blocking — waits for full response
  → _handle_llm_response()    # extracts reasoning, builds loop_trace entry
  → _persist_loop_trace()     # writes to graph_state["loop_trace"]
  → HTMX poll (every 1 s)     # first time UI sees reasoning
```

Latency from "LLM starts generating" to "reasoning visible in UI" equals the full LLM response time (~4–60 s depending on reasoning depth and tool count).

### Existing infrastructure

| Component | Location | Status |
|-----------|----------|--------|
| `get_completion_stream()` | `core/llm.py:138` | ✅ Exists, unused by agent |
| HTMX polling, every 1 s | `chat/templates/chat/_tool_progress.html:4` | ✅ Already polls `AgentRun.graph_state` |
| `graph_state` JSONField | `agent/models.py:AgentRun` | ✅ Mutable at any time |
| `loop_trace` in graph_state | `agent/graph/nodes.py` | ✅ Already read by polling view |

### Reasoning content types

| Provider / Mode | How reasoning arrives |
|-----------------|----------------------|
| All models (default) | `message.content` text before tool calls |
| Claude extended thinking (`thinking` budget) | Separate `{"type":"thinking","thinking":"..."}` content block before `{"type":"text"}` |
| OpenAI `o1` / `o3` | `reasoning_content` field (not streamed by OpenAI API; skip special handling) |

---

## Proposed Solution

### Architecture overview

Switch `call_llm` from blocking to **streaming**, write a **transient `_streaming_round`** key to `AgentRun.graph_state` incrementally as tokens arrive, and remove it when the round completes (replacing it with the normal `loop_trace` entry).

```
call_llm() — new flow
  → get_completion_stream(...)      # returns chunk iterator
  → for chunk in stream:
      accumulate reasoning text     # text / thinking block tokens
      accumulate tool call deltas   # function name + arguments
      every 300 ms: write           # graph_state["_streaming_round"] → DB
  → stream ends: assemble response
  → _handle_llm_response()          # builds loop_trace entry (unchanged)
  → _persist_loop_trace()           # writes loop_trace (unchanged)
  → clear graph_state["_streaming_round"]
  → HTMX poll sees completed loop_trace entry
```

HTMX polling (1 s) reads `_streaming_round` during generation and renders it as a live in-progress round header with the accumulated reasoning text.

---

## Detailed Design

### 1. `core/llm.py` — Extend `get_completion_stream()`

Add `run`, `conversation`, `source` parameters (matching `get_completion`) so usage can be recorded from the accumulated final chunk.

```python
def get_completion_stream(
    messages: list[dict],
    model: str | None = None,
    source: str = "unknown",
    run=None,
    conversation=None,
    **kwargs,
) -> Iterator:
    ...
```

Usage recording: `litellm.stream_chunk_builder(chunks)` reconstructs a full response object from the accumulated chunks list, including `usage`. Call `_record_usage()` on that after the stream ends (caller responsibility, not inside the generator).

---

### 2. `agent/graph/nodes.py` — `call_llm` streaming

Replace the `get_completion(...)` call with a streaming loop:

```python
from core.llm import get_completion_stream

stream = get_completion_stream(messages, model=model, source="agent", run=run_obj,
                               tools=tools_schema or None)

reasoning_buf: str = ""          # accumulated thinking/text before tool calls
tool_deltas: dict = {}           # {index: {id, name, arguments_so_far}}
chunks: list = []                # all chunks for stream_chunk_builder
_last_write = time.monotonic()
WRITE_INTERVAL = 0.3             # seconds between DB flushes

for chunk in stream:
    chunks.append(chunk)
    delta = chunk.choices[0].delta if chunk.choices else None
    if not delta:
        continue

    # Accumulate reasoning text (plain text content or thinking blocks)
    if delta.content:
        if isinstance(delta.content, list):          # Claude thinking blocks
            for block in delta.content:
                if block.get("type") == "thinking":
                    reasoning_buf += block.get("thinking", "")
                elif block.get("type") == "text":
                    reasoning_buf += block.get("text", "")
        else:
            reasoning_buf += delta.content           # standard string delta

    # Accumulate tool call deltas
    for tc_delta in (delta.tool_calls or []):
        idx = tc_delta.index
        if idx not in tool_deltas:
            tool_deltas[idx] = {"id": "", "name": "", "arguments": ""}
        if tc_delta.id:
            tool_deltas[idx]["id"] += tc_delta.id
        if tc_delta.function.name:
            tool_deltas[idx]["name"] += tc_delta.function.name
        if tc_delta.function.arguments:
            tool_deltas[idx]["arguments"] += tc_delta.function.arguments

    # Periodic DB flush: write _streaming_round
    now = time.monotonic()
    if now - _last_write >= WRITE_INTERVAL and reasoning_buf:
        _write_streaming_round(state["run_id"], current_round, reasoning_buf)
        _last_write = now

# Reconstruct full response for _handle_llm_response()
response = litellm.stream_chunk_builder(chunks, messages=messages)
_record_usage_from_stream(response, model, run_obj)
_clear_streaming_round(state["run_id"])
```

#### Helper: `_write_streaming_round(run_id, round_num, reasoning_so_far)`

```python
def _write_streaming_round(run_id: str, round_num: int, reasoning: str) -> None:
    """Write in-progress reasoning to graph_state for live display."""
    try:
        from agent.models import AgentRun
        obj = AgentRun.objects.get(pk=run_id)
        gs = obj.graph_state or {}
        gs["_streaming_round"] = {
            "round": round_num,
            "reasoning": reasoning,
            "ts": time.time(),
        }
        AgentRun.objects.filter(pk=run_id).update(graph_state=gs)
    except Exception:
        pass
```

#### Helper: `_clear_streaming_round(run_id)`

```python
def _clear_streaming_round(run_id: str) -> None:
    try:
        from agent.models import AgentRun
        obj = AgentRun.objects.get(pk=run_id)
        gs = obj.graph_state or {}
        gs.pop("_streaming_round", None)
        AgentRun.objects.filter(pk=run_id).update(graph_state=gs)
    except Exception:
        pass
```

---

### 3. `agent/graph/state.py` — New field

```python
_streaming_round: NotRequired[dict]
# Transient. Set during LLM streaming: {round, reasoning, ts}.
# Cleared when the round completes and loop_trace entry is written.
```

---

### 4. `chat/views.py` — Surface `_streaming_round` in `MessageStreamView`

During an active run, after building `loop_trace_with_tes`, check for an in-progress round and prepend it as a "pending" entry:

```python
streaming_round = graph_state.get("_streaming_round")
if streaming_round:
    # Build a synthetic in-progress loop_trace entry
    pending_entry = {
        "round": streaming_round["round"],
        "decision": "streaming",        # new decision type
        "reasoning": streaming_round["reasoning"],
        "ts": streaming_round["ts"],
        "tool_executions": [],
        "llm_ms": None,
        "elapsed_s": round(streaming_round["ts"] - active_agent_run.started_at.timestamp(), 1)
                     if active_agent_run.started_at else None,
    }
    # Only show if this round is not already in loop_trace_with_tes
    completed_rounds = {e.get("round") for e in loop_trace_with_tes}
    if streaming_round["round"] not in completed_rounds:
        loop_trace_with_tes = loop_trace_with_tes + [pending_entry]
```

---

### 5. `chat/templates/chat/_tool_progress.html` — Live reasoning display

Add a visual style for `decision == "streaming"` rounds:

```html
{# Round header #}
<span class="flex-shrink-0 text-[10px] font-bold w-4 h-4 rounded-full flex items-center justify-center mt-0.5
  {% if entry.decision == 'tool_call' %}bg-blue-900/60 text-blue-300
  {% elif entry.decision == 'streaming' %}bg-yellow-900/60 text-yellow-400 animate-pulse
  {% else %}bg-green-900/60 text-green-300{% endif %}">
  {{ entry.round }}
</span>
```

Reasoning text during streaming should display with a streaming cursor:

```html
{% if entry.reasoning %}
<p class="text-[11px] text-gray-200 leading-relaxed">
  <span class="text-gray-500 select-none">💭 </span>{{ entry.reasoning }}{% if entry.decision == 'streaming' %}<span class="inline-block w-1.5 h-3 bg-gray-400 ml-0.5 animate-pulse"></span>{% endif %}
</p>
{% endif %}
```

Note: remove `|truncatewords:50` for streaming entries so partial text is shown in full.

---

### 6. Error handling & edge cases

| Scenario | Handling |
|----------|----------|
| Stream interrupted (network error) | `get_completion_stream()` raises; `call_llm` catches, clears `_streaming_round`, returns `{"output": f"LLM error: {exc}"}` |
| `stream_chunk_builder` fails | Fall back to `get_completion()` (non-streaming) for that round only |
| Model doesn't support streaming | `litellm` raises; caught, falls through to non-streaming path |
| Agent cancelled mid-stream | Check `_is_cancelled()` periodically in chunk loop (every 5 chunks); break early |
| `_streaming_round` left over (crash) | `_clear_streaming_round()` called in `save_result` as cleanup guard |
| OpenAI o1/o3 reasoning | `reasoning_content` not in streaming delta; treat same as plain text — no special handling |

---

### 7. Backward compatibility

- `_handle_llm_response()` signature is unchanged — receives the same reconstructed response object.
- `_persist_loop_trace()` is unchanged.
- Old `graph_state` blobs without `_streaming_round` continue to work (key is optional).
- Non-streaming fallback path (`get_completion`) retained for use in `force_conclude` and approval resumption.

---

## Out of Scope

- **Token-level streaming to the browser** — the existing HTMX 1 s poll is sufficient; true character-by-character streaming would require WebSockets or SSE and is a separate spec.
- **OpenAI o1/o3 reasoning tokens** — these are not exposed via streaming by the OpenAI API; no change.
- **`process_chat_message` (plain chat)** — plain LLM chat replies are not part of the trace; streaming for that is a separate concern.
- **`force_conclude` node** — already uses non-streaming `get_completion()`; no change.
- **Usage cost tracking accuracy** — `stream_chunk_builder` recovers token counts; detailed streaming usage tracking is a later improvement.

---

## Open Questions

1. **DB write frequency**: 300 ms flushes mean up to ~3 DB writes per second per active run. Acceptable for current load? Could increase to 500 ms if needed.
2. **Claude extended thinking enablement**: Do we want to pass `thinking={"type":"enabled","budget_tokens":8000}` to Anthropic models? This is a separate opt-in — not part of this spec.
3. **Reasoning truncation in streaming**: Currently `|truncatewords:50` is applied to completed entries. Should streaming entries show full text (potentially very long) or apply a higher limit (e.g., 200 words)?
4. **`stream_chunk_builder` availability**: Verify LiteLLM version in `uv.lock` supports this utility for all configured providers.

---

## Test Plan

See `.testreport/030-streaming-reasoning-display.md` after implementation.

| Test | Description |
|------|-------------|
| `test_streaming_writes_streaming_round` | Mock stream chunks arriving; verify `_write_streaming_round` is called with accumulated text |
| `test_streaming_round_cleared_on_completion` | After stream ends, verify `_streaming_round` is absent from graph_state |
| `test_streaming_view_shows_pending_entry` | `MessageStreamView` with `_streaming_round` in graph_state returns loop_trace with synthetic entry |
| `test_streaming_view_no_duplicate_when_completed` | Completed round in loop_trace suppresses `_streaming_round` for same round |
| `test_thinking_block_accumulation` | Claude-style `[{"type":"thinking","thinking":"..."}]` delta content is accumulated into reasoning_buf |
| `test_stream_error_clears_streaming_round` | LLM stream error triggers `_clear_streaming_round()` |
| `test_save_result_clears_streaming_round` | `save_result` node clears `_streaming_round` as guard |
