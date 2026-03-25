# 016 — Code Refactoring Analysis & Plan

## Goal

Reduce technical debt in the GavinAgent codebase by breaking apart god files,
eliminating duplicated logic, and establishing clearer module boundaries. This
spec documents the current state and prioritises refactoring targets.

## Background

The project has grown rapidly through feature additions (RAG, MCP, workflows,
skills, knowledge base, loop trace). Most new logic was added to two files:

- `agent/graph/nodes.py` — **955 lines**, handles 9+ responsibilities
- `agent/views.py` — **1,672 lines**, contains 52 view/form classes across 12 UI domains

These god files are the primary maintenance risk. Other issues include duplicated
patterns, tight coupling, and dead code.

Since the original analysis, a comprehensive test suite has been added:
- **50 P0 unit tests** (`tests/agent/`, `tests/chat/`, `tests/core/`)
- **79 Playwright e2e tests** (`tests/e2e/`) covering all UI domains

This makes refactoring significantly safer — all changes can be validated
against the existing test suite.

---

## Current State Analysis

### File Sizes

| File | Lines | Status |
|------|------:|--------|
| `agent/views.py` | 1,672 | 🔴 God file — 52 classes |
| `agent/graph/nodes.py` | 955 | 🔴 God file — 6 node fns + 8 helpers |
| `agent/models.py` | 480 | 🟡 Acceptable — 14 model classes |
| `agent/runner.py` | 176 | 🟢 OK |
| `chat/views.py` | 376 | 🟡 Growing — 10 classes, workflow output views added |
| `agent/workflows/loader.py` | 165 | 🟢 OK |
| `agent/mcp/pool.py` | 233 | 🟡 Acceptable |
| `agent/mcp/client.py` | 113 | 🟢 OK |
| `agent/graph/graph.py` | 106 | 🟢 OK |
| `agent/skills/embeddings.py` | 98 | 🟢 OK |
| `agent/rag/ingest.py` | 96 | 🟢 OK |
| `agent/rag/chunker.py` | 90 | 🟢 OK |
| `agent/tools/__init__.py` | 42 | 🟢 OK |
| `core/llm.py` | 51 | 🟢 OK |
| `agent/services.py` | 43 | 🔴 Dead code — never imported |
| `agent/graph/state.py` | 27 | 🟢 OK |
| `agent/tools.py` | 27 | 🟢 OK (legacy registry, separate from tools/) |
| `chat/services.py` | 34 | 🟢 OK |

### God Functions in `agent/graph/nodes.py`

| Function | Lines | Responsibilities |
|----------|------:|-----------------|
| `call_llm` | 281–555 (~275) | Context assembly, conversation history, error filtering, tool schema building, skill auto-inject, MCP schema, LLM call, loop trace, graph_state persistence |
| `execute_tools` | 677–836 (~160) | URL dedup, signature dedup, built-in dispatch, MCP dispatch, ToolExecution CRUD, markdown collection |
| `check_approval` | 556–676 (~120) | Dedup filtering, approval policies, MCP approval, ToolExecution creation, workflow auto-approve, graph_state save |
| `_build_skills_section` | 40–134 (~95) | Skill loading, YAML parsing, embedding match, keyword fallback, disabled filter |
| `force_conclude` | 837–916 (~80) | Duplicate message assembly from `call_llm`, concluding prompt |
| `_build_system_context` | 169–233 (~65) | Assembles 8 sources into system prompt (workspace, soul, skills, memory, MCP resources, knowledge/RAG, temporal, tool formatting rule) |
| `_build_knowledge_section` | 135–167 (~33) | RAG retrieval wrapper |
| `save_result` | 917–955 (~38) | Final output persistence, workflow delivery |

### Mixed Responsibilities in `agent/views.py`

One file serves 12 UI domains with **52 classes** (51 views + 1 form):

| Domain | Classes | Lines (approx) |
|--------|--------:|------:|
| Run management | RunListView, RunCreateView, RunDetailView, RunStatusView, RunRespondView, RunCancelView | 79–228 |
| Dashboard & Logs | DashboardView, LogsView | 229–258 |
| Memory | MemoryView, MemoryReembedView, MemorySearchView, MemoryParagraphDeleteView, MemoryParagraphEditView | 259–390 |
| Tools | ToolsView, ToolToggleView, ToolPolicyView | 391–494 |
| Skills | SkillsView, SkillInstallView, SkillToggleView, SkillDeleteView | 495–584 |
| Tool Approval | ToolApproveView | 585–634 |
| Agent CRUD | AgentForm, AgentListView, AgentCreateView, AgentEditView, AgentDeleteView, AgentSetDefaultView | 635–804 |
| Monitoring & Health | MonitoringView, HealthCheckView | 805–985 |
| Workspace | WorkspaceFileListView, WorkspaceFileEditView, WorkspaceFileServeView | 986–1089 |
| MCP Servers | MCPServerListView, MCPServerAddView, MCPServerDetailView, MCPServerToggleView, MCPServerRefreshView, MCPServerDeleteView | 1090–1300 |
| Workflows | WorkflowListView, WorkflowDetailView, WorkflowToggleView, WorkflowRunNowView, WorkflowReloadView, WorkflowSaveView, WorkflowCreateView, WorkflowDeleteView | 1301–1539 |
| Knowledge Base | KnowledgeListView, KnowledgeCreateView, KnowledgeToggleView, KnowledgeStatusView, KnowledgeReingestView, KnowledgeDeleteView | 1540–1672 |

### Duplicated Patterns

| Pattern | Locations | Impact |
|---------|-----------|--------|
| Message assembly + conversation history + tool injection | `call_llm()` and `force_conclude()` | 🔴 ~60 lines duplicated (history fetch, error filtering, truncation, tool message injection) |
| YAML frontmatter parsing (`---` split) | `_build_skills_section`, `call_llm` (auto-inject), `views.py` (`_load_skill_bodies`) | 🟡 3 locations |
| `graph_state` read-modify-write | `call_llm` (2× for loop_trace, 1× for rag_matches), `check_approval` (1×) | 🟡 4 locations |
| Cancellation check (`AgentRun.Status.FAILED`) | `call_llm`, `execute_tools` | 🟡 2 locations — identical 6-line pattern |
| ToolExecution create/update pattern | `check_approval` (2×), `execute_tools` (built-in + MCP), `runner.py` | 🟡 5 locations |
| `AgentRun` imported under 3 aliases | `_AgentRun`, `_AR`, `AgentRun` in nodes.py | 🟡 3 different names for the same model |
| Lazy `import yaml` | `_build_skills_section` and `call_llm` auto-inject block | 🟡 2 locations |

### Tight Coupling

| Source | Imports from | Issue |
|--------|-------------|-------|
| `nodes.py` | 15+ modules (lazy) | Coupling nexus of the entire project — imports from `agent.models`, `agent.tools`, `agent.skills`, `agent.mcp.*`, `agent.rag.*`, `agent.memory.*`, `chat.models`, `core.llm`, plus stdlib |
| `views.py` | 12+ modules | Monolithic controller |
| `chat/views.py` | `agent.models` (7 imports) | Chat queries Agent, AgentRun, ToolExecution, Workflow directly |
| `nodes.py` ↔ `runner.py` | Bidirectional knowledge | Both know graph_state schema details (pending_tool_calls, assistant_tool_call_message, etc.) |

### Dead / Stale Code

| File | Issue |
|------|-------|
| `agent/services.py` | Uses stale field names (`waiting_for_human`, `tool_calls`), builds wrong state dict, **never imported** — confirmed dead |
| `_build_system_context` return type | Declares `tuple[str, list[str]]` but returns 3-tuple `(content, triggered, rag_matches)` |
| `agent/tools.py` (root) | Legacy tool registry with `echo` example — separate from `agent/tools/` package |
| `assemble_context` node | No-op pass-through function (`return {}`) — adds a graph node that does nothing |

---

## Proposed Refactoring Plan

### Phase 1 — High Impact (break apart god files)

#### H1: Split `agent/graph/nodes.py` into focused modules

```
agent/graph/
├── __init__.py
├── graph.py              (existing — graph builder + routing, 106 lines)
├── state.py              (existing — AgentState TypedDict, 27 lines)
├── context.py            (NEW — _build_system_context, _build_skills_section,
│                                _build_knowledge_section, _read_workspace_file)
├── llm.py                (NEW — call_llm, force_conclude, _assemble_messages helper)
├── tools.py              (NEW — check_approval, execute_tools, _tool_sig,
│                                _NAME_ONLY_DEDUP_TOOLS)
├── persistence.py        (NEW — save_result, update_graph_state helper)
└── helpers.py            (NEW — _count_tokens, _truncate_history,
                                  _get_agent_model, _is_cancelled)
```

**Target**: each file < 250 lines, single responsibility.

Note: the `assemble_context` no-op node should be removed — it adds a graph
step that does nothing. Context assembly happens inside `call_llm`. Remove the
node from `graph.py` and make `call_llm` the entry point.

#### H2: Split `agent/views.py` into per-domain view modules

```
agent/views/
├── __init__.py            (re-exports ALL view classes for URL compatibility)
├── _helpers.py            (_load_skill_bodies, _memory_path, _split_paragraphs,
│                           _hash, _status_badge_class)
├── runs.py                (RunListView, RunCreateView, RunDetailView, RunStatusView,
│                           RunRespondView, RunCancelView — 6 classes)
├── dashboard.py           (DashboardView, LogsView — 2 classes)
├── agents.py              (AgentListView, AgentCreateView, AgentEditView,
│                           AgentDeleteView, AgentSetDefaultView — 5 classes)
├── memory.py              (MemoryView, MemoryReembedView, MemorySearchView,
│                           MemoryParagraphDeleteView, MemoryParagraphEditView — 5)
├── tools.py               (ToolsView, ToolToggleView, ToolPolicyView,
│                           ToolApproveView — 4 classes)
├── skills.py              (SkillsView, SkillInstallView, SkillToggleView,
│                           SkillDeleteView — 4 classes)
├── mcp.py                 (MCPServerListView, MCPServerAddView, MCPServerDetailView,
│                           MCPServerToggleView, MCPServerRefreshView,
│                           MCPServerDeleteView — 6 classes)
├── workflows.py           (WorkflowListView, WorkflowDetailView, WorkflowToggleView,
│                           WorkflowRunNowView, WorkflowReloadView, WorkflowSaveView,
│                           WorkflowCreateView, WorkflowDeleteView — 8 classes)
├── knowledge.py           (KnowledgeListView, KnowledgeCreateView, KnowledgeToggleView,
│                           KnowledgeStatusView, KnowledgeReingestView,
│                           KnowledgeDeleteView — 6 classes)
├── workspace.py           (WorkspaceFileListView, WorkspaceFileEditView,
│                           WorkspaceFileServeView — 3 classes)
└── monitoring.py          (MonitoringView, HealthCheckView — 2 classes)
```

**Target**: each file 50–200 lines. Total: 51 view classes + helpers.

#### H3: Extract shared message assembly (DRY `call_llm` / `force_conclude`)

Create `_assemble_messages(state, system_content, *, include_markdown_reminder=True)` helper that:
- Builds conversation history from `ChatMessage`
- Filters error-prefix messages (the `_error_prefixes` tuple)
- Applies history window (`AGENT_HISTORY_WINDOW`) + token truncation
- Injects `assistant_tool_call_message` + tool results (with `required_ids` validation)
- Injects collected markdown reminders when no new tool results are present

Both `call_llm` and `force_conclude` call this instead of duplicating the logic.
Currently ~60 lines are duplicated between these two functions.

#### H4: Delete `agent/services.py`

**Confirmed dead code**: `AgentService` is never imported anywhere in the
codebase. It uses stale field names (`waiting_for_human`, `tool_calls`) that
don't exist in the current `AgentState` TypedDict. All run execution goes
through `agent/runner.py` → `AgentRunner.run()`.

#### H5: Delete `agent/tools.py` (root)

The root-level `agent/tools.py` is a legacy tool registry with a toy `echo`
function. The real tool system is in `agent/tools/` (package). This file is
likely vestigial — verify no imports reference it before deleting.

### Phase 2 — Medium Impact (eliminate duplication)

| ID | Refactoring | Description |
|----|-------------|-------------|
| M1 | `_is_cancelled(run_id)` helper | Replace 2× identical 6-line cancellation check blocks in `call_llm` and `execute_tools` |
| M2 | `ToolExecutionRecorder` | Centralise ToolExecution create/update/status pattern (5 locations across `check_approval`, `execute_tools`, `runner.py`) |
| M3 | `parse_skill_frontmatter(path)` | Extract YAML `---` parsing used in 3 places |
| M4 | `update_graph_state(run_id, **updates)` | Replace 4 inline read-modify-write blocks (`AgentRun.objects.get` → modify `gs` dict → `.update()`) |
| M5 | `_persist_loop_trace(run_id, loop_trace)` | Replace 2× identical try/except blocks that save loop_trace to graph_state |
| M6 | Move `AgentForm` to `agent/forms.py` | Django convention — forms don't belong in views |

### Phase 3 — Low Impact (cleanup)

| ID | Refactoring | Description |
|----|-------------|-------------|
| L1 | Fix `_build_system_context` type hint | Change `tuple[str, list[str]]` → `tuple[str, list[str], list[dict]]` (returns 3-tuple) |
| L2 | Unify `AgentRun` import aliases | Standardise on one name — currently uses `_AgentRun`, `_AR`, `AgentRun` in the same file |
| L3 | Standardise lazy import policy | Document rule: top-level unless circular dep. Currently 40+ lazy imports in `nodes.py` alone |
| L4 | Consolidate duplicate `import yaml` | `yaml` is lazily imported in both `_build_skills_section` and the auto-inject block of `call_llm` |
| L5 | Remove `assemble_context` no-op | Delete the empty node and its graph edge — `call_llm` already assembles context internally |

---

## Execution Order

```
Phase 1 (god file breakup):
  H4 → H5 → H1 → H3 → H2

Phase 2 (DRY patterns):
  M1 → M4 → M5 → M3 → M2 → M6

Phase 3 (polish):
  L1 → L2 → L5 → L3 → L4
```

**H4/H5 first** — trivial deletions of dead code, no risk.

**H1 before H3** because splitting `nodes.py` first makes it easier to extract
the shared message assembly helper into the new `llm.py` module.

**H3 before H2** because views refactoring is lower risk (mostly moving code)
and doesn't affect runtime behaviour.

## Validation Strategy

All refactoring changes MUST pass:

1. **Unit tests**: `uv run pytest tests/ --ignore=tests/e2e -v` (50 P0 tests)
2. **E2E tests**: `uv run pytest tests/e2e/ -v "--browser-channel=chrome"` (79 tests)
3. **Import check**: `uv run python -c "from agent.views import *; from agent.graph.nodes import *"` — verify all re-exports work
4. **Manual smoke test**: Start the server and verify chat + agent run workflow

## Out of Scope

- Adding new features during refactoring
- Database schema changes
- Changing the LangGraph graph structure (except removing the no-op `assemble_context` node)
- Rewriting tools or RAG pipeline
- Refactoring `chat/views.py` (376 lines — growing but not yet a god file)

## Risks

- **Import paths change** — `agent/urls.py` references views by class name; must
  update imports or use re-exports in `views/__init__.py`. The e2e tests will
  catch any broken routes.
- **Circular imports** — splitting `nodes.py` may surface circular deps that
  were previously hidden by lazy imports; address case-by-case. The 40+ lazy
  imports in `nodes.py` suggest this is a real risk.
- **`views/__init__.py` re-exports** — must re-export every class to avoid
  breaking `urls.py`. Consider using `__all__` to be explicit.

## Resolved Questions

1. ~~Should `agent/services.py` be deleted or repurposed?~~ **Delete** — confirmed
   never imported, uses stale schema.
2. ~~Should we add tests before or during the refactoring?~~ **Tests exist** — 50
   unit + 79 e2e tests provide a solid safety net.
3. ~~What is the preferred lazy import policy?~~ **Top-level unless circular dep** —
   document in a code style section when implementing L3.
