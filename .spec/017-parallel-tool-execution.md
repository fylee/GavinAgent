# 017 — Parallel Tool Execution

## Goal

Reduce agent response latency when the LLM issues multiple independent tool
calls in the same round by executing them concurrently rather than sequentially.

## Background

Currently the agent loop processes tool calls one-by-one:

```
Round N: LLM issues [search "wafer start", search "lot start", search "first step"]
execute_tools: for tc in pending → call sequentially
  → search "wafer start"   (1.2 s)
  → search "lot start"     (1.1 s)
  → search "first step"    (1.0 s)
Total: ~3.3 s
```

With parallelism the same round would take ~1.2 s (the slowest single call).

There are two independent layers where parallelism can be introduced:

1. **LLM layer** — instruct the LLM to emit multiple tool calls in a single
   response instead of one at a time.
2. **execute_tools layer** — run the pending tool calls from a single LLM
   response concurrently using threads.

Both should be implemented together for maximum benefit, but they are
independently valuable.

## Proposed Solution

### Layer 1 — LLM prompt: encourage parallel tool calls

Add a standing instruction to the agent system prompt (in `_build_system_context`
inside `agent/graph/nodes.py`) that explicitly permits and encourages the LLM
to emit multiple tool calls at once when the calls are independent:

```
When you need to gather information from multiple independent sources, call all
the relevant tools in a single response rather than waiting for one result before
requesting the next. Only call tools sequentially when a later call depends on
the output of an earlier one.
```

This is a zero-code-change to execution logic — just a prompt addition.
Azure OpenAI supports parallel tool calls natively; the API already accepts and
returns multiple `tool_calls` in one assistant message.

### Layer 2 — execute_tools: concurrent execution with ThreadPoolExecutor

Replace the sequential `for tc in pending` loop in `execute_tools`
(`agent/graph/nodes.py`) with a `ThreadPoolExecutor` that dispatches all
independent tool calls simultaneously.

#### Rules for parallelism

Not all tool calls can be parallelised safely:

| Condition | Handling |
|-----------|----------|
| Tool calls with no dependency on each other | Run in parallel |
| `file_write` calls | Run sequentially (avoid interleaved writes to the same file) |
| Tool calls that were already deduped (failed/succeeded sig) | Skip before dispatch, as today |
| Approval-required calls | Already filtered out before `execute_tools` runs |

A simple safe default: **run all tool calls in the same round in parallel**,
except `file_write` calls which are serialised after all parallel calls finish.

#### Implementation sketch

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _execute_single(tc, ...) -> tuple[str, dict]:
    """Execute one tool call; return (tool_call_id, result)."""
    ...

parallel = [tc for tc in pending if tc["name"] != "file_write"]
serial   = [tc for tc in pending if tc["name"] == "file_write"]

with ThreadPoolExecutor(max_workers=min(len(parallel), 8)) as pool:
    futures = {pool.submit(_execute_single, tc, ...): tc for tc in parallel}
    for future in as_completed(futures):
        tc_id, result = future.result()
        tool_results.append({"tool_call_id": tc_id, "result": result})

for tc in serial:
    tc_id, result = _execute_single(tc, ...)
    tool_results.append({"tool_call_id": tc_id, "result": result})
```

`max_workers=8` is a reasonable upper bound; MCP SSE connections are I/O-bound
so threads will spend most time waiting on the network.

#### Thread safety considerations

- `MCPConnectionPool.call_tool` is already thread-safe (uses
  `run_coroutine_threadsafe` on the pool's dedicated asyncio loop).
- Built-in tools (`web_read`, `web_search`, `api_get`, etc.) are stateless and
  thread-safe.
- `ToolExecution` DB writes use per-row `save(update_fields=[...])` — safe.
- `visited_urls`, `failed_sigs`, `succeeded_sigs` are mutated during execution.
  These must be collected from futures after all threads finish, not mutated
  concurrently. Each `_execute_single` returns its mutations; the caller merges.

## Out of Scope

- Dynamic dependency graph between tool calls (e.g. detecting that call B reads
  the output of call A). The LLM is expected to handle sequencing across rounds
  when there are dependencies.
- Cancelling in-flight parallel calls if one fails (all calls in a round run to
  completion regardless).
- Parallelising across LLM rounds (each round still waits for all tools before
  the next LLM call).

## Expected Impact

For a query like "Search EDWM for wafer start yesterday in Taichung FAB" that
issues 5 independent `search_elasticsearch_datadictionary` calls:

| Metric | Before | After |
|--------|--------|-------|
| execute_tools wall time | ~6 s | ~1.5 s |
| Total agent latency | ~12 s | ~6 s |

## Open Questions

1. Should `shell_exec` be allowed to run in parallel? It mutates the filesystem
   and could have side effects. Initial proposal: **no** (treat like file_write).
2. What is the right `max_workers` cap? 8 is a guess; should be tunable via
   `settings.AGENT_TOOL_PARALLELISM` (default 8).
3. Should the parallel execution be opt-in per agent, or global?
   Initial proposal: **global** (always on).
