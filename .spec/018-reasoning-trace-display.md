# 018 — Reasoning Trace Display

## Goal

Give the user and developer a complete, human-readable record of every decision
the agent makes: what context was injected before it started, why it chose each
set of tools, what it concluded from the results, and why it went around the
loop again. This record must be inspectable live (during execution) and
retrospectively (in the run detail view) to support debugging and improvement.

---

## Background

### What is already captured

| Signal | Stored in | Rendered in |
|--------|-----------|-------------|
| Triggered skills | `AgentRun.triggered_skills` (JSONField) | Skill pills — chat + dashboard |
| RAG knowledge matches | `graph_state["rag_matches"]` `[{title, similarity}]` | Document chips — dashboard only |
| Active MCP servers | `graph_state["mcp_servers_active"]` `[server_name]` | Server pills — chat only |
| Round decision type | `loop_trace[n].decision` (`"tool_call"` / `"answer"`) | Loop trace badge — dashboard (collapsed) |
| Tools called per round | `loop_trace[n].tools` `[fn_name]` | Loop trace list |
| Parallel count | `loop_trace[n].parallel_count` | `⇉ N in parallel` badge |
| LLM free-text preamble | `loop_trace[n].reasoning` | Italic text — chat + dashboard |
| ToolExecution status + timing | `ToolExecution` DB rows | Progress list — chat + dashboard |
| Parallel batch grouping | `ToolExecution.parallel_group` (8-char hex) | `⇉ parallel ×N` divider + violet border |
| Parallel batch wall time | (not yet stored) | — |

### What is NOT captured (gaps)

1. **Reasoning is almost always `None`.**  
   `message.content` before tool calls is nearly always empty — Azure OpenAI
   by default suppresses the assistant preamble when returning tool calls.
   There is no structured prompt instruction that reliably elicits it.

2. **No explanation of why the loop continues.**  
   After `execute_tools` returns, the LLM sees the tool results and decides
   whether to call more tools or produce a final answer. This transition —
   the most important decision in the loop — is invisible. There is no
   `continue_reason` captured anywhere.

3. **Context loading is completely invisible.**  
   `_build_system_context` assembles: temporal context, `AGENTS.md`,
   `SOUL.md`, triggered skills (with body text), long-term memory excerpts,
   MCP always-include resources, RAG paragraphs, and the full tool catalogue.
   None of this assembly is persisted. The user cannot see what shaped the
   LLM's behaviour, making it impossible to debug wrong or surprising answers.

4. **Skill routing decision is opaque.**  
   `_build_skills_section` uses embedding similarity + keyword fallback to
   decide which skills are "Active" vs "Available". The score that caused a
   skill to be triggered (or not) is never recorded.

5. **History truncation is invisible.**  
   `_truncate_history` silently drops old messages. When an agent "forgets"
   something from earlier in a conversation, there is no indication.

6. **`force_conclude` is untagged.**  
   When `tool_call_rounds` hits the limit or all tool calls were duplicates,
   `force_conclude` fires a special LLM call. This is invisible — the run
   trace shows a normal final answer with no indication that the agent was
   forced to stop.

7. **`consecutive_failed_rounds` guard is invisible.**  
   The early-exit guard that fires after 2 consecutive all-failure rounds
   leaves no trace in the UI.

8. **Tool approval decisions are not explained.**  
   `check_approval` decides (per tool, per agent policy, per MCP
   `auto_approve_tools`) whether each tool needs human approval. The policy
   reasoning is not recorded.

9. **Parallel batch wall time is not stored.**  
   `tool_wall_ms` (the actual time the batch took end-to-end, equal to the
   slowest concurrent tool) is not persisted in the loop trace, so the UI
   cannot show the true critical path.

10. **The dashboard loop trace is collapsed and disconnected from tool rows.**  
    The loop trace and the tool execution list are two separate UI sections.
    There is no unified timeline connecting "Round 2 decided to call X, Y, Z"
    with the execution rows for X, Y, Z.

---

## Proposed Solution

### Layer 1 — Prompt instruction for structured reasoning

Add two explicit instructions to the system prompt in `_build_system_context`:

**A. Pre-tool reasoning (`Reason:` prefix):**

```
When you decide to call one or more tools, write one sentence on its own line
starting with "Reason:" before the tool calls. State specifically what
information you are missing and what you expect the tools to return.
Example: "Reason: I need the Trino table name for wafer starts before I can
write the SQL query."
```

This targets the gap where `message.content` is empty before tool calls.
The parser in `call_llm` strips the `Reason:` prefix and stores the rest as
`loop_trace[n].reasoning`. If the model omits it, `reasoning` stays `None`
and no harm is done.

**B. Post-result continuation annotation (`Continue:` / `Answer:` prefix):**

```
After receiving tool results, begin your response with one of:
  "Continue: <one sentence — what is still missing and why you need more tools>"
  "Answer: <one sentence — what the tools confirmed and what you will now say>"
Do not omit this prefix. It helps the user understand your process.
```

The parser in `call_llm` extracts this prefix from the beginning of
`message.content` in every subsequent round (i.e., when `tool_results`
are present). It stores the text as `loop_trace[N-1].continue_reason`,
retrospectively annotating the _previous_ round with why the loop continued.

> **Implementation note:** The `Continue:` / `Answer:` prefix must be parsed
> from `message.content` at the start of each `call_llm` invocation when
> `state["tool_results"]` is non-empty (i.e., this is not the first round).
> It is written back to the previous `loop_trace` entry before the new entry
> is appended.

### Layer 2 — Context loading trace (`context_trace`)

In `_build_system_context`, build and return a `context_trace` dict alongside
the existing return values. `call_llm` persists it to `graph_state["context_trace"]`
on the **first round only** (when `state.get("tool_call_rounds", 0) == 0`).

```python
# Return signature change:
# _build_system_context(query) -> (system_prompt, triggered_skills, rag_matches, context_trace)

context_trace = {
    "agents_md_chars": 4200,           # 0 if not present
    "soul_md_chars": 800,              # 0 if not present
    "skills": [
        {"name": "stock-chart", "status": "active", "match": "embedding"},
        {"name": "weekly-report", "status": "available", "match": None},
    ],
    "memory_excerpts": 3,              # count only (content is sensitive)
    "mcp_resources": [
        {"server": "EDWM MCP", "uri": "file:///context.md", "chars": 4200},
    ],
    "rag": [                           # already in rag_matches, duplicated here for co-location
        {"title": "Wafer Start Definition", "similarity": "92%"},
    ],
    "tools_count": 14,                 # total tools in the schema sent to the LLM
    "mcp_servers": ["EDWM MCP", "Research MCP"],
    "history_messages": 8,             # messages included after truncation
    "history_dropped": 2,              # messages dropped by _truncate_history
    "total_prompt_chars": 18400,       # len(system_content) + injected context
}
```

Skill `status` is `"active"` if it was injected (body included), `"available"`
if it appeared in the index table only. `match` records whether the trigger
was `"embedding"`, `"keyword"`, `"regex"`, or `None` (always-available fallback).
This requires a small change to `_build_skills_section` to return match reason
alongside the skill name.

### Layer 3 — `loop_trace` schema extension

Extend each entry to the full schema:

```json
{
  "round": 2,
  "decision": "tool_call",
  "tools": ["EDWM_MCP__execute_trino_query"],
  "parallel_count": 3,
  "reasoning": "I need to run three independent Trino queries to get wafer, lot and event counts.",
  "continue_reason": "Continue: All three queries succeeded. I now have enough data to compute yield.",
  "tool_wall_ms": 577,
  "tool_count": 3,
  "forced": false
}
```

New fields:

| Field | Type | Set by | Description |
|-------|------|--------|-------------|
| `continue_reason` | `str \| null` | `call_llm` (next round) | LLM's explanation of why it continued or concluded |
| `tool_wall_ms` | `int \| null` | `execute_tools` | Wall-clock ms of the batch (max duration in parallel group) |
| `tool_count` | `int` | `execute_tools` | Actual tools dispatched (after dedup/block filtering) |
| `forced` | `bool` | `force_conclude` | True if this was a forced conclusion, not a voluntary answer |

`tool_wall_ms` is computed in `execute_tools` as `max(te.duration_ms for te in batch)` and written back to the current round's trace entry before returning.

`forced` is set by `force_conclude` when it appends its own final trace entry.

### Layer 4 — `check_approval` decision trace

When `check_approval` processes a tool call, record the approval decision and
its reason in the `ToolExecution` record:

Add `approval_reason` (CharField, blank=True) to `ToolExecution`:

| Value | Meaning |
|-------|---------|
| `"auto_approve_list"` | Tool name in `MCPServer.auto_approve_tools` |
| `"auto_workflow"` | Run is a workflow — all tools auto-approved |
| `"policy_allow"` | Agent's `ApprovalPolicy` is NEVER or tool type allows auto |
| `"requires_human"` | Queued for human approval |
| `"rejected"` | Human rejected |

This makes approval decisions auditable independently of the loop trace.

### Layer 5 — UI rendering

#### 5a. Chat: `_tool_progress.html` — unified timeline

Replace the two separate sections (loop trace + tool rows) with a single
chronological timeline, one section per round:

```
┌─────────────────────────────────────────────────────────────────┐
│ 📚 Context  stock-chart ·  3 memory  · 📄 Wafer Start (92%)    │  ← collapsed by default
│             EDWM MCP / context.md (4.2 KB) · 🔧 14 tools       │
└─────────────────────────────────────────────────────────────────┘

① Round 1  ⇉ 4 in parallel                                        ← round badge
  Reason: I need to search for relevant table names before querying.
  ─────────────────────────────────── parallel ×4 ────────────────
  │ ✓ search_elasticsearch_datadictionary  "wafer start"  63ms ⚑
  │ ✓ search_elasticsearch_datadictionary  "lot start"    94ms ⚑
  │ ✓ search_elasticsearch_datadictionary  "wafer event"  62ms ⚑
  │ ✓ search_elasticsearch_datadictionary  "first step"  140ms ⚑
  → Continue: Found the table. Now I'll run the Trino queries.

② Round 2  ⇉ 3 in parallel
  Reason: Running three independent Trino queries simultaneously.
  ─────────────────────────────────── parallel ×3 ────────────────
  │ ✓ execute_trino_query   563ms ⚑   (critical path)
  │ ✓ execute_trino_query   577ms ⚑
  │ ✓ execute_trino_query   563ms ⚑
  → Answer: All counts retrieved. Composing final response.

● ● ●  (thinking dots while still running)
```

Specific changes:
- "Context" row: single collapsed strip at top showing pills for what was
  loaded. Expands to show `context_trace` details. Shown only once
  `graph_state["context_trace"]` is available.
- Round header: includes `continue_reason` from the **previous** round at the
  bottom of that round's section (the `→ Continue:` line).
- Parallel batch divider: already implemented — keep as-is.
- Duration: `⚑` amber flag for parallel group members (already implemented).
  Add `(critical path)` label on the single slowest member.
- `forced=true`: show a red `⚠ Forced conclusion` badge on the round badge.

#### 5b. Agent dashboard: `_run_status.html` — timeline + context card

The loop trace section becomes the primary UI rather than a collapsed
afterthought:

- Default to **expanded** when `run.status` is running or waiting.
- Default to collapsed when completed/failed (but remember expansion state
  in `localStorage`).
- Each round shows: badge → `reasoning` → tool execution cards (inline, not
  a separate section) → `continue_reason`.
- The existing separate "Tool Executions" section is **removed** — tool cards
  are now embedded inside each round.
- Add a **"Prompt context"** card above the timeline (collapsed by default)
  that renders `context_trace`.

#### 5c. Dashboard: `run_detail.html` — Context panel

Add a **Context** panel to the run detail page between the run summary and
the loop trace. It shows `context_trace` in a readable format:

- System instructions: `AGENTS.md` chars, `SOUL.md` chars
- Skills: table of name / status (active/available) / match reason
- Memory: N excerpts injected
- Knowledge: RAG matches with titles and similarity
- MCP resources: server / URI / size
- Tools: N total (N built-in + N MCP)
- History: N messages included, N dropped by truncation
- Estimated prompt size: total chars

---

## Data Flow (revised)

```
_build_system_context(query)
  → system_prompt (str)
  → triggered_skills (list[str])
  → rag_matches (list[{title, similarity}])
  → context_trace (dict)          ← NEW

call_llm()  [round 1]
  → graph_state["context_trace"] = context_trace    ← persisted once
  → graph_state["loop_trace"][0].reasoning = parse("Reason: …")

call_llm()  [round N > 1, tool_results present]
  → parse "Continue:"|"Answer:" from message.content
  → loop_trace[N-2].continue_reason = parsed text   ← retrospective write
  → loop_trace[N-1].reasoning = parse("Reason: …")  ← new entry

execute_tools()  [round N]
  → ToolExecution.parallel_group = batch_uuid       ← already done
  → ToolExecution.is_serial                         ← already done
  → loop_trace[N-1].tool_wall_ms = max(duration_ms) ← NEW
  → loop_trace[N-1].tool_count = len(dispatched)    ← NEW

force_conclude()
  → loop_trace[-1].forced = True                    ← NEW

check_approval()
  → ToolExecution.approval_reason = reason_str      ← NEW (migration needed)
```

---

## Migration

Two DB changes required:

1. **`ToolExecution.approval_reason`** — `CharField(max_length=40, blank=True, default="")`  
   Migration: `agent/migrations/0012_toolexecution_approval_reason.py`

2. No changes to `AgentRun` — `context_trace` lives inside the existing
   `graph_state` JSONField.

---

## Out of Scope

- Storing the full LLM message history for replay (too large).
- Displaying per-round token counts (API returns totals only, already in
  `LLMUsage`).
- Streaming reasoning tokens in real time (requires API-level streaming changes).
- Editing or re-running individual rounds from the trace UI.
- Surfacing `AGENTS.md` / `SOUL.md` full content in the UI (sensitive; chars
  count is sufficient).

---

## Open Questions

1. **`Continue:` / `Answer:` prefix reliability.**  
   Azure GPT-4o-mini sometimes omits the prefix when the content is short.
   Should we treat absence as `continue_reason = None` (silent) or fall back
   to showing the raw `message.content`?  
   Proposal: show raw content truncated to 100 chars if no prefix found,
   rather than showing nothing.

2. **Skill match score from embeddings.**  
   `find_relevant_skills` currently returns a list of matched names without
   scores. To record `match_score` in `context_trace`, the function signature
   must return `list[tuple[str, float]]`. This is a breaking change in
   `agent/skills/embeddings.py`.  
   Proposal: return `list[tuple[str, float]]` and update all callers.

3. **History truncation count.**  
   `_truncate_history` returns the truncated list but not how many messages
   were dropped. A simple `dropped = len(original) - len(truncated)` can be
   computed in `call_llm` before calling the function.  
   Proposal: compute inline in `call_llm` — no change to `_truncate_history`.

4. **Context panel visibility in chat.**  
   Should "Context loaded" show immediately (before the first LLM call) by
   reading `triggered_skills` and `rag_matches` which are set early, or wait
   for `context_trace` to be persisted?  
   Proposal: show `triggered_skills` / `rag_matches` / `mcp_servers_active`
   immediately (already in state), show full `context_trace` once available.

5. **Unifying the two UI sections (loop trace + tool cards) in `_run_status.html`.**  
   Merging them requires the tool executions to be round-aware. Currently
   `ToolExecution` has no `round` field — only `parallel_group`. A `round`
   integer field would make this trivial, or the UI can infer round membership
   by matching `parallel_group` to `loop_trace[n]`'s tool list.  
   Proposal: add `round` (IntegerField, null=True) to `ToolExecution` in the
   same migration as `approval_reason`.

