# 016 — Code Refactoring Analysis & Plan

## Goal

Reduce technical debt in the GavinAgent codebase by breaking apart god files,
eliminating duplicated logic, and establishing clearer module boundaries. This
spec documents the current state and prioritises refactoring targets.

## Background

The project has grown rapidly through feature additions (RAG, MCP, workflows,
skills, knowledge base, loop trace). Most new logic was added to two files:

- `agent/graph/nodes.py` — **955 lines**, handles 9+ responsibilities
- `agent/views.py` — **1,672 lines**, contains 27 view classes across 10 UI domains

These god files are the primary maintenance risk. Other issues include duplicated
patterns, tight coupling, and dead code.

---

## Current State Analysis

### File Sizes

| File | Lines | Status |
|------|------:|--------|
| `agent/views.py` | 1,672 | 🔴 God file |
| `agent/graph/nodes.py` | 955 | 🔴 God file |
| `agent/models.py` | 480 | 🟡 Acceptable |
| `agent/runner.py` | 377 | 🟢 OK |
| `agent/tools/__init__.py` | 43 | 🟢 OK |
| `agent/graph/graph.py` | 88 | 🟢 OK |
| `agent/graph/state.py` | 25 | 🟢 OK |
| `agent/services.py` | 13 | 🟡 Possibly dead code |
| `core/llm.py` | 152 | 🟢 OK |
| `chat/views.py` | 50 | 🟢 OK |
| `chat/services.py` | 40 | 🟢 OK |

### God Functions in `agent/graph/nodes.py`

| Function | ~Lines | Responsibilities |
|----------|-------:|-----------------|
| `call_llm` | 260 | Context assembly, conversation history, error filtering, tool schema building, skill auto-inject, MCP schema, LLM call, loop trace, graph_state persistence |
| `execute_tools` | 170 | URL dedup, signature dedup, built-in dispatch, MCP dispatch, ToolExecution CRUD, markdown collection |
| `check_approval` | 130 | Dedup filtering, approval policies, MCP approval, ToolExecution creation, workflow auto-approve, graph_state save |
| `_build_skills_section` | 100 | Skill loading, YAML parsing, embedding match, keyword fallback, disabled filter |
| `_build_system_context` | 75 | Assembles 6+ sources into system prompt |

### Mixed Responsibilities in `agent/views.py`

One file serves 10+ UI domains:

1. Run management (RunDetailView, RunStatusView, RunRespondView, RunCancelView)
2. Dashboard & Logs
3. Memory management (5 views)
4. Tool management (3 views)
5. Skill management (4 views)
6. Agent CRUD (5 views + AgentForm)
7. MCP server management (6 views)
8. Workflow management (7 views)
9. Knowledge base (5 views)
10. Workspace file editing (3 views)
11. Monitoring & Health (2 views)

### Duplicated Patterns

| Pattern | Locations | Impact |
|---------|-----------|--------|
| Message assembly + conversation history + tool injection | `call_llm()` and `force_conclude()` | 🔴 ~50 lines duplicated |
| YAML frontmatter parsing (`---` split) | `_build_skills_section`, `call_llm` (auto-inject), `views.py` (`_load_skill_bodies`) | 🟡 3 locations |
| Run status template context dict | `RunDetailView`, `RunStatusView`, `RunRespondView`, `RunCancelView` | 🟡 4× identical |
| `graph_state` read-modify-write | `call_llm` (3 times), `check_approval` (1 time) | 🟡 4 locations |
| Cancellation check (`AgentRun.Status.FAILED`) | `call_llm`, `execute_tools` | 🟡 2 locations |
| ToolExecution create/update pattern | `check_approval`, `execute_tools`, `runner.py` | 🟡 3 locations |

### Tight Coupling

| Source | Imports from | Issue |
|--------|-------------|-------|
| `nodes.py` | 14+ modules | Coupling nexus of the entire project |
| `views.py` | 12+ modules | Monolithic controller |
| `chat/` | `agent.models` | Chat queries AgentRun and ToolExecution directly |
| `nodes.py` ↔ `runner.py` | Bidirectional | Both know graph_state schema details |

### Dead / Stale Code

| File | Issue |
|------|-------|
| `agent/services.py` | Uses stale field names (`waiting_for_human`), likely unused |
| `_build_system_context` return type | Declares `tuple[str, list[str]]` but returns 3-tuple |

---

## Proposed Refactoring Plan

### Phase 1 — High Impact (break apart god files)

#### H1: Split `agent/graph/nodes.py` into focused modules

```
agent/graph/
├── __init__.py
├── graph.py              (existing — graph builder)
├── state.py              (existing — AgentState TypedDict)
├── context.py            (NEW — _build_system_context, _build_skills_section,
│                                _build_knowledge_section, _read_workspace_file)
├── llm.py                (NEW — call_llm, force_conclude, _assemble_messages helper)
├── tools.py              (NEW — check_approval, execute_tools, _tool_sig)
└── persistence.py         (NEW — save_result, graph_state helpers)
```

**Target**: each file < 250 lines, single responsibility.

#### H2: Split `agent/views.py` into per-domain view modules

```
agent/views/
├── __init__.py            (re-exports for URL compatibility)
├── runs.py                (RunDetailView, RunStatusView, RunRespondView, RunCancelView)
├── dashboard.py           (DashboardView, LogsView)
├── agents.py              (AgentListView, AgentCreateView, AgentUpdateView, etc.)
├── memory.py              (MemoryView, MemorySearchView, etc.)
├── tools.py               (ToolsView, ToolToggleView)
├── skills.py              (SkillsView, SkillToggleView, etc.)
├── mcp.py                 (MCPView, MCPAddView, etc.)
├── workflows.py           (WorkflowListView, WorkflowCreateView, etc.)
├── knowledge.py           (KnowledgeListView, KnowledgeCreateView, etc.)
├── workspace.py           (WorkspaceView, WorkspaceFileView)
└── monitoring.py          (MonitoringView, HealthCheckView)
```

**Target**: each file 50–200 lines.

#### H3: Extract shared message assembly (DRY `call_llm` / `force_conclude`)

Create `_assemble_messages(state, system_content)` helper that:
- Builds conversation history from `ChatMessage`
- Filters error-prefix messages
- Applies history window + token truncation
- Injects `assistant_tool_call_message` + tool results
- Injects collected markdown reminders

Both `call_llm` and `force_conclude` call this instead of duplicating the logic.

#### H4: Clean up `agent/services.py`

Either delete (if confirmed dead) or update to use current field names and
delegate to `runner.py`.

### Phase 2 — Medium Impact (eliminate duplication)

| ID | Refactoring | Description |
|----|-------------|-------------|
| M1 | `_run_status_context(run)` | Single helper for the 4× duplicated template context |
| M2 | `ToolExecutionRecorder` | Centralise ToolExecution create/update/status pattern |
| M3 | `parse_skill_frontmatter()` | Extract YAML `---` parsing used in 3 places |
| M4 | `update_graph_state(run_id, **updates)` | Replace 4 inline read-modify-write blocks |
| M5 | Agent query layer for `chat/` | Decouple chat from direct agent model queries |

### Phase 3 — Low Impact (cleanup)

| ID | Refactoring | Description |
|----|-------------|-------------|
| L1 | Fix `_build_system_context` type hint | Change to `tuple[str, list[str], list[dict]]` |
| L2 | Standardise lazy import policy | Document rule: top-level unless circular dep |
| L3 | Unify `AgentRun` import aliases | Pick one name, not `_AgentRun` / `_AR` / `AgentRun` |
| L4 | Move `AgentForm` to `agent/forms.py` | Django convention |

---

## Execution Order

```
Phase 1 (god file breakup):
  H1 → H3 → H2 → H4

Phase 2 (DRY patterns):
  M1 → M3 → M4 → M2 → M5

Phase 3 (polish):
  L1 → L2 → L3 → L4
```

**H1 before H3** because splitting `nodes.py` first makes it easier to extract
the shared message assembly helper into the new `llm.py` module.

**H3 before H2** because views refactoring is lower risk (mostly moving code)
and doesn't affect runtime behaviour.

## Out of Scope

- Adding new features during refactoring
- Database schema changes
- Changing the LangGraph graph structure
- Rewriting tools or RAG pipeline

## Risks

- **Import paths change** — `urls.py` references views by class name; must
  update imports or use re-exports in `views/__init__.py`
- **Circular imports** — splitting `nodes.py` may surface circular deps that
  were previously hidden by lazy imports; address case-by-case
- **Test coverage** — no automated tests exist; refactoring must be validated
  manually by running agent tasks

## Open Questions

1. Should `agent/services.py` be deleted or repurposed?
2. Should we add tests before or during the refactoring?
3. What is the preferred lazy import policy? (top-level vs inline)
