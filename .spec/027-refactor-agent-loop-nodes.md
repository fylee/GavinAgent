# 027 — Refactor Agent Loop: assemble_context + call_llm

## Goal

Reduce `call_llm` from 365 lines doing 7 unrelated things to a focused ~80-line function
that reads pre-built context from state and calls the LLM. Activate the currently
dead `assemble_context` node to do actual work, and eliminate the ~80 lines of
duplicated message-assembly logic between `call_llm` and `force_conclude`.

## Background

`agent/graph/nodes.py` has grown to 1,307 lines. The root cause is that `call_llm`
accumulated responsibilities over time without extraction:

| Responsibility | Approx lines |
|----------------|-------------|
| Cancellation check | 10 |
| Build system prompt (`_build_system_context`) | 7 |
| Build message list (history, tool results, markdown) | 100 |
| Build tools schema (enabled tools + auto-inject + MCP) | 58 |
| Persist triggered_skills / rag_matches / context_trace to DB | 32 |
| Actual LLM call (`get_completion`) | 12 |
| Parse response + persist loop_trace (two branches × 2 DB writes) | 134 |
| **Total** | **365** |

Additional problems:

- `assemble_context` (line 450) is a 2-line no-op (`return {}`); wastes a graph node slot.
- `force_conclude` (line 1332) duplicates ~80 lines of message-assembly logic from `call_llm` verbatim.
- The `loop_trace` DB-persistence block is copy-pasted twice inside `call_llm` (tool-calls branch
  lines 749–757 and final-answer branch lines 809–816).
- Cancellation check is copy-pasted in both `call_llm` and `execute_tools`.

Spec 016 (code-refactoring.md) describes the broader god-file breakup; this spec
covers the focused, self-contained work on the graph loop nodes.

## Proposed Solution

### Phase 1 — Extract helpers (no state or graph changes)

Extract four private helpers from `call_llm`. No changes to `AgentState`, `graph.py`,
or the LangGraph edge routing. All existing behaviour is preserved.

#### `_is_cancelled(run_id: str) -> bool`

```python
def _is_cancelled(run_id: str) -> bool:
    """Return True if the AgentRun was marked FAILED externally (Cancel button)."""
    try:
        from agent.models import AgentRun
        status = AgentRun.objects.filter(pk=run_id).values_list("status", flat=True).first()
        return status == AgentRun.Status.FAILED
    except Exception:
        return False
```

Replaces the identical 8-line try/except in `call_llm` (line 463) **and** in
`execute_tools`. Both call sites become:

```python
if _is_cancelled(state["run_id"]):
    return {"output": "", "pending_tool_calls": []}
```

---

#### `_assemble_messages(state, system_content, model) -> list[dict]`

Encapsulates the full message-list construction:

1. Start with `[{"role": "system", "content": system_content}]`
2. Fetch `ChatMessage` history; filter error-prefix messages (`_error_prefixes`)
3. Apply `AGENT_HISTORY_WINDOW` limit and `_truncate_history` token budget
4. If no conversation, append `{"role": "user", "content": state["input"]}`
5. Validate and inject `assistant_tool_call_message` + tool results (the `required_ids` check)
6. Inject `collected_markdown` reminder (two variants: concluding vs. mid-flight)

Returns `(messages: list[dict], history_stats: dict)` where `history_stats` carries
`history_messages` and `history_dropped` for the context_trace.

Both `call_llm` and `force_conclude` call this instead of duplicating the logic.
**~80 lines removed from `force_conclude`; `call_llm` loses ~100 lines.**

---

#### `_build_tools_schema(state, triggered_skills, skill_dir_map) -> list[dict]`

Encapsulates:

1. Fetch agent's `enabled_tools` list from DB
2. Build schemas for enabled built-in tools
3. Auto-inject tools declared in triggered skills' YAML `tools:` list
4. Auto-inject `run_skill` when a triggered skill has `handler.py`
5. Extend with MCP tool schemas from `MCPRegistry`

Returns `list[dict]` (LLM function schemas).
**~58 lines removed from `call_llm`.**

---

#### `_persist_loop_trace(run_id: str, loop_trace: list[dict]) -> None`

```python
def _persist_loop_trace(run_id: str, loop_trace: list[dict]) -> None:
    try:
        from agent.models import AgentRun
        ar = AgentRun.objects.get(pk=run_id)
        gs = ar.graph_state or {}
        gs["loop_trace"] = loop_trace
        AgentRun.objects.filter(pk=run_id).update(graph_state=gs)
    except Exception:
        pass
```

Replaces the two identical try/except blocks at lines 749–757 and 809–816 in `call_llm`.
Also used by `force_conclude` to replace its equivalent block (lines 1409–1431 are
more complex but share the same DB write pattern).

---

#### Result after Phase 1

| Function | Before | After (est.) |
|----------|-------:|------------:|
| `call_llm` | 365 | ~160 |
| `force_conclude` | 103 | ~30 |
| `execute_tools` | -8 (cancellation) | saves 8 |

---

### Phase 2 — Activate assemble_context

Move context assembly out of `call_llm` and into `assemble_context`. Context is
built **once per run** (the query doesn't change between rounds) and stored in
`AgentState`. Subsequent `call_llm` invocations read from state instead of
re-computing.

#### `AgentState` additions

```python
# Populated by assemble_context; read by call_llm on every round.
# Underscore prefix signals these are internal graph fields, not agent I/O.
_system_content: str          # fully assembled system prompt
_triggered_skills: list[str]  # skills matched by embedding/keyword
_skill_dir_map: dict          # name → Path, for tool auto-inject in call_llm
_rag_matches: list[dict]      # RAG document matches (for UI display)
_context_trace: dict          # token/component stats for first-round display
_tools_schema: list[dict]     # pre-built LLM function schemas
_model: str                   # resolved model name (avoids repeat DB reads)
```

> **Note**: These fields must be added to `state.py` with appropriate defaults
> (empty string / empty list / empty dict). They are **write-once** from
> `assemble_context`; `call_llm` only reads them.
>
> The existing `messages: Annotated[list[dict], operator.add]` field is NOT used
> for pre-built messages — it is a LangGraph accumulator used elsewhere. The new
> `_system_content` is a string stored once; `_assemble_messages` builds the
> per-round message list using `_system_content` from state on each call.

#### `assemble_context` (after)

```python
def assemble_context(state: AgentState) -> dict:
    """Pre-build context that is stable across all rounds of this run."""
    query = state.get("input", "")
    model = _get_agent_model(state)
    system_content, triggered_skills, rag_matches, context_trace, skill_dir_map = (
        _build_system_context(query)
    )
    # Append conversation_id to system prompt (stable for the run)
    if state.get("conversation_id"):
        system_content += f"\n\n---\n\nCurrent conversation ID: `{state['conversation_id']}`"
    tools_schema = _build_tools_schema(
        state, triggered_skills=triggered_skills, skill_dir_map=skill_dir_map
    )
    return {
        "_system_content": system_content,
        "_triggered_skills": triggered_skills,
        "_skill_dir_map": skill_dir_map,
        "_rag_matches": rag_matches,
        "_context_trace": context_trace,
        "_tools_schema": tools_schema,
        "_model": model,
    }
```

#### `call_llm` (after Phase 2, ~80 lines)

```python
def call_llm(state: AgentState) -> dict:
    from core.llm import get_completion

    if _is_cancelled(state["run_id"]):
        return {"output": "", "pending_tool_calls": []}

    model = state.get("_model") or _get_agent_model(state)
    system_content = state.get("_system_content") or ""
    tools_schema = state.get("_tools_schema") or []

    messages, history_stats = _assemble_messages(state, system_content, model)

    # Persist context_trace + triggered_skills on first round
    _persist_first_round_context(state, history_stats)

    try:
        _round_start = timezone.now().timestamp()
        response = get_completion(messages, model=model, source="agent",
                                  run=_get_run_obj(state),
                                  tools=tools_schema or None)
    except Exception as exc:
        logger.exception("LLM call failed in AgentRun %s: %s", state.get("run_id"), exc)
        return {"output": f"LLM error: {exc}", "pending_tool_calls": []}

    _llm_ms = round((timezone.now().timestamp() - _round_start) * 1000)
    return _handle_llm_response(state, response, _round_start, _llm_ms)
```

#### `_handle_llm_response` (new private helper)

Extracts the response-parsing + loop_trace logic (the largest remaining block).
Returns the dict that `call_llm` returns to LangGraph:

- If `message.tool_calls`: parse calls, build trace entry, call `_persist_loop_trace`, return tool-calls dict
- Else: build final-answer trace entry, call `_persist_loop_trace`, return output dict

**~130 lines extracted from `call_llm`; fully testable in isolation.**

---

### Phase 3 — Move to `context.py` module (optional, follow-on)

After Phase 1 + 2, `nodes.py` will be ~600 lines. The context-building functions
can be moved to a new `agent/graph/context.py` as a follow-on (covered by spec 016 H1):

```
agent/graph/
  nodes.py        # ~400 lines — 6 node fns + immediate helpers
  context.py      # NEW — _build_system_context, _build_skills_section,
                  #        _build_knowledge_section, _assemble_messages,
                  #        _build_tools_schema, _read_workspace_file
```

This phase is **out of scope for this spec** but should be done in the same PR or
the following one to avoid nodes.py re-accumulating.

---

## New Function Map

| Function | Phase | Location | Lines (est.) |
|----------|-------|----------|-------------|
| `_is_cancelled(run_id)` | 1 | `nodes.py` | 8 |
| `_assemble_messages(state, system_content, model)` | 1 | `nodes.py` | ~90 |
| `_build_tools_schema(state, triggered_skills, skill_dir_map)` | 1 | `nodes.py` | ~60 |
| `_persist_loop_trace(run_id, loop_trace)` | 1 | `nodes.py` | 10 |
| `_handle_llm_response(state, response, ts, llm_ms)` | 2 | `nodes.py` | ~100 |
| `_persist_first_round_context(state, history_stats)` | 2 | `nodes.py` | ~30 |
| `_get_run_obj(state)` | 2 | `nodes.py` | 8 |

---

## AgentState Changes (Phase 2 only)

```python
# agent/graph/state.py — add to AgentState TypedDict
_system_content: str
_triggered_skills: list[str]
_skill_dir_map: dict
_rag_matches: list[dict]
_context_trace: dict
_tools_schema: list[dict]
_model: str
```

All new fields must have defaults (`""`, `[]`, `{}`) so existing runs that resume
from saved `graph_state` (tool approval flow) are not broken. LangGraph merges
returned dicts into state with `operator.add` for list fields and direct assignment
for others; the new fields use direct assignment.

**No database schema changes.** `graph_state` is a JSON field that already stores
arbitrary dicts — LangGraph serialises `AgentState` into it for suspension.

---

## Out of Scope

- Splitting `nodes.py` into `context.py` + `nodes.py` (spec 016 H1 — Phase 3 here is a pointer only)
- Splitting `agent/views.py` (spec 016 H2)
- Any changes to `execute_tools`, `check_approval`, or `save_result`
- Changes to `graph.py` edge routing (the `assemble_context → call_llm` edge already exists)
- New features or behaviour changes

---

## Acceptance Criteria

- [ ] `_is_cancelled` replaces both inline cancellation blocks (in `call_llm` and `execute_tools`)
- [ ] `_assemble_messages` is used by both `call_llm` and `force_conclude`; no message-assembly code remains duplicated
- [ ] `_build_tools_schema` is called from `call_llm` (Phase 1) then moved to `assemble_context` (Phase 2)
- [ ] `_persist_loop_trace` replaces all inline loop_trace DB write blocks
- [ ] `assemble_context` returns a non-empty dict populating the 7 new state fields
- [ ] `call_llm` is ≤ 100 lines after Phase 2
- [ ] `force_conclude` is ≤ 40 lines after Phase 1
- [ ] All 31 unit tests in `tests/agent/` pass
- [ ] Manual smoke test: send a chat message that triggers a tool call and verify the run completes correctly
- [ ] Existing tool-approval resumption flow (graph_state saved/loaded) still works

---

## Risks

| Risk | Mitigation |
|------|-----------|
| `assemble_context` runs once but `call_llm` loops — context won't refresh mid-run | Correct by design: query is fixed per run; skills/RAG results don't change between rounds |
| Tool-approval resumption restores state from DB — new `_*` fields may be empty | Add guards in `call_llm`: `state.get("_system_content") or _build_system_context(...)` fallback |
| `_assemble_messages` signature change breaks `force_conclude` call | Implement both changes in the same commit |
| LangGraph state serialisation of `dict` fields | `skill_dir_map` contains `Path` objects — must serialise to `str` before storing in state; deserialise on read |

---

## Open Questions

1. Should `_build_tools_schema` be called in `assemble_context` (once, cached) or
   in `call_llm` (every round)? The tool list can change if a new MCP server is
   added mid-run, but this is rare. **Recommended**: call in `assemble_context` for
   performance; accept that tool list is fixed for the run duration. If a hot-reload
   use case arises, `_build_tools_schema` can be called again in `call_llm` when
   `_tools_schema` is empty.

2. Should `_handle_llm_response` live in `nodes.py` or a new `agent/graph/llm.py`?
   **Recommended**: `nodes.py` for Phase 2; move to `llm.py` in Phase 3 together
   with the context module split.
