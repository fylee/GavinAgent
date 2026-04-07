# 018 — Reasoning Trace Display

## Goal

Give the user real-time, human-readable visibility into _why_ the agent is
doing what it is doing at every step: which context sources were loaded, what
the LLM decided to do in each round, and why it decided to go around the loop
again rather than respond directly.

## Background

The agent loop currently records and displays:

| Signal | Where stored | Where rendered |
|--------|-------------|----------------|
| Triggered skills | `AgentRun.triggered_skills` | skill pills (chat + dashboard) |
| RAG knowledge matches | `graph_state["rag_matches"]` | document chips (dashboard) |
| Active MCP servers | `graph_state["mcp_servers_active"]` | server pills (chat) |
| Round decisions | `graph_state["loop_trace"][n].decision` | loop trace (dashboard, collapsed) |
| Tool calls per round | `loop_trace[n].tools` | loop trace list |
| Parallel count | `loop_trace[n].parallel_count` | `⇉ N in parallel` badge |
| LLM free-text reasoning | `loop_trace[n].reasoning` | italic text under round badge |
| Tool execution status + timing | `ToolExecution` rows | progress list (chat + dashboard) |

### Gaps

1. **Reasoning is almost always `None`.**  
   The LLM emits a free-text preamble before tool calls only occasionally.
   There is no structured channel for it to explain _why_ it chose specific
   tools, what it expects to learn, or why it decided to continue after
   seeing the results.

2. **Context loading is invisible.**  
   Before the first LLM call, `_build_system_context` loads skills, long-term
   memory, RAG paragraphs, and MCP always-include resources. The user never
   sees _what was injected_ or _why_. When the agent's answer is wrong or
   surprising, there is no way to tell which context shaped it.

3. **The "why continue" decision is unexplained.**  
   After `execute_tools` returns, the LLM decides whether to call more tools or
   produce a final answer. This is the most important decision in the loop, yet
   it is completely invisible — the user just sees another batch of tool calls
   start without understanding what gap the agent is trying to fill.

4. **The dashboard loop trace is collapsed by default and hard to read.**  
   Power users who open it see a compressed list. There is no narrative thread
   connecting rounds — each entry is a standalone fact rather than part of a
   visible reasoning chain.

5. **No "context summary" panel.**  
   There is no single place that shows everything the LLM was given before it
   started: system instructions, injected skills, memories, RAG hits, MCP
   resources, and tool catalogue size.

## Proposed Solution

### Layer 1 — Structured reasoning hooks in the prompt

Add two explicit reasoning prompts to the agent loop:

**A. Pre-tool-call annotation (already partly done via `loop_trace[n].reasoning`):**  
Strengthen the system prompt so the LLM _always_ emits a brief reasoning line
before its tool calls, in a structured format the parser can extract:

```
Before calling any tools in a round, write one line starting with
"Reason:" that summarises what you need to find out and why.
Example: "Reason: I need the wafer start count for yesterday from Trino
before I can compute the yield."
```

This line goes into `loop_trace[n].reasoning` and is displayed verbatim.

**B. Post-tool continuation annotation:**  
After each `execute_tools` call and before the next LLM invocation, the
prompt history already contains the tool results. Add an instruction so the
LLM always emits a `Continue:` or `Answer:` line as its first token:

```
After receiving tool results, begin your response with either:
  "Continue: <one sentence explaining what is still missing>"
  "Answer: <one sentence summarising what you found>"
Do not omit this prefix.
```

`Continue:` text goes into a new `loop_trace[n].continue_reason` field and
is shown as the "→ next round reason" banner between batches.  
`Answer:` text is stored and shown as the conclusion before the final message.

### Layer 2 — Context loading trace (`context_trace` in `graph_state`)

In `_build_system_context`, record a structured log of every context source
that was loaded and injected, storing it in `graph_state["context_trace"]`:

```python
context_trace = {
    "skills":   [{"name": "stock-chart", "match_score": 0.87}],
    "memory":   [{"excerpt": "User prefers Taichung FAB…", "chars": 120}],
    "rag":      [{"title": "Wafer Start Definition", "similarity": "92%"}],
    "mcp_resources": [{"server": "EDWM MCP", "uri": "file:///context.md", "chars": 4200}],
    "tools_count": 14,
    "total_prompt_chars": 18400,
}
```

This is persisted to `AgentRun.graph_state` after the first `call_llm` invocation.

### Layer 3 — UI rendering

#### 3a. Chat: `_tool_progress.html`

Add a collapsible **"Context loaded"** section above the round trace, shown once
at the top of the typing indicator:

```
📚 Context loaded
  ⚡ Skill: stock-chart (score 87%)
  🧠 Memory: 2 excerpts
  📄 Knowledge: Wafer Start Definition (92%)
  🔌 MCP resource: EDWM MCP / context.md (4.2 KB)
  🔧 14 tools available
```

Between each round, show the `continue_reason` banner:

```
Round 1  ⇉ 4 in parallel
  Reason: I need to identify the correct Trino table for wafer starts.
  [tool rows…]
  → Continue: I found the table but need yesterday's date range to run the query.
Round 2  ⇉ 1 tool
  Reason: Running the Trino query with the correct date filter.
  [tool row…]
  → Answer: Query returned 1,204 wafer starts.
```

#### 3b. Agent dashboard: `_run_status.html`

Expand the existing loop trace to show the full narrative chain as a
vertical timeline. Each round node shows:

- Round number badge (blue = tool call, green = final)
- `parallel_count` badge if >1
- `reasoning` text (LLM pre-call explanation)
- Tool calls with status/timing (already shown in tool execution cards)
- `continue_reason` text beneath the tool list (LLM post-results decision)
- Connector line to next round

Add a **"Prompt context"** card (collapsed by default) with the full
`context_trace` contents, so developers can inspect exactly what was injected.

#### 3c. Dashboard: "Context" tab or panel in run detail

On the `run_detail.html` page, add a dedicated **Context** section alongside
the existing tool execution trace. It shows:

- System prompt (truncated, expandable)
- Skills injected (with match scores)
- Memory excerpts injected (with text preview)
- RAG paragraphs injected (title + similarity)
- MCP resources injected (server, URI, byte size)
- Total prompt token estimate

### Layer 4 — `loop_trace` schema extension

Extend each `loop_trace` entry from:
```json
{
  "round": 2,
  "decision": "tool_call",
  "tools": ["edwm_mcp__execute_trino_query"],
  "reasoning": "I need to run the query…",
  "parallel_count": 1
}
```
to:
```json
{
  "round": 2,
  "decision": "tool_call",
  "tools": ["edwm_mcp__execute_trino_query"],
  "parallel_count": 1,
  "reasoning": "Reason: I need to run the query with the date range I found.",
  "continue_reason": "Continue: The query succeeded. I can now compute yield.",
  "tool_wall_ms": 405,
  "tool_count": 1
}
```

`continue_reason` is extracted from the LLM's preamble in the _next_ round
(it looks back at the previous round) or from the final-answer prefix if
`decision == "final_answer"`.

`tool_wall_ms` is the wall-clock time of the parallel batch (max duration
among concurrent tools), stored when `execute_tools` returns.

## Data Flow

```
_build_system_context()
  → context_trace{}        → graph_state["context_trace"]
  → triggered_skills[]     → AgentRun.triggered_skills
  → rag_matches[]          → graph_state["rag_matches"]

call_llm()  [round N]
  → parse "Reason: …"      → loop_trace[N].reasoning
  → parse "Continue: …"    → loop_trace[N-1].continue_reason  (injected retrospectively)
  → tool_calls[]           → loop_trace[N].tools, .parallel_count

execute_tools()  [round N]
  → wall clock             → loop_trace[N].tool_wall_ms
  → parallel_group stamps  → ToolExecution.parallel_group / .is_serial

→ Persisted to graph_state after every round
→ Rendered live via 1s polling in chat / polling in run_detail
```

## Out of Scope

- Storing the full LLM message history for replay (too large; use `graph_state`
  as a summary only).
- Displaying token counts per round (API does not return per-message tokens,
  only totals — already stored in `LLMUsage`).
- Editing the system prompt from the trace UI (separate feature).
- Streaming the reasoning token-by-token (requires streaming API changes).

## Open Questions

1. Should `context_trace` be stored per-round (re-evaluated each round) or
   only once (first round)? Context is currently built once per run, so once
   is sufficient — but if the skill routing changes between rounds this would
   be missed. Initial proposal: **once, on first `call_llm` invocation**.

2. Should `continue_reason` be extracted by parsing the LLM prefix, or by
   asking the LLM to write it into a structured JSON field?  
   Prefix parsing is fragile but zero-cost; structured JSON requires an extra
   prompt field and response_format enforcement. Initial proposal: **prefix
   parsing** with a fallback of `None` if the LLM omits it.

3. Should the "Context loaded" panel be visible immediately (before the first
   LLM round) or only after context has been built?  
   It requires `graph_state["context_trace"]` to be populated, which happens
   after the first DB write in `call_llm`. Initial proposal: **show only once
   available**, leaving the typing dots visible until then.

4. Is the `continue_reason` / `reasoning` prefix instruction compatible with
   all model providers (Azure OpenAI, Anthropic via LiteLLM)?  
   Azure GPT-4o follows this reliably; Anthropic Claude tends to comply too.
   Should be tested per-provider before relying on it.
