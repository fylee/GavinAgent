# 014 — Automated Testing

## Goal

Establish an automated test suite for GavinAgent to catch regressions before
they reach production, and to provide a safety net for the upcoming code
refactoring (spec 015).

## Background

The project currently has **zero tests**. All validation is manual — run the
agent, observe the UI, check Celery logs. This makes refactoring risky and
bug detection slow (e.g. the `force_conclude` 3-tuple unpack crash would have
been caught instantly by a unit test).

## Proposed Solution

### Test Framework

- **pytest** + **pytest-django** — standard for Django projects
- **factory_boy** — model factories for test data
- **responses** or **respx** — HTTP mock library for httpx calls
- Package management via `uv add --dev`

### Test Directory Structure

```
tests/
├── conftest.py               # pytest-django config, shared fixtures
├── factories.py              # factory_boy model factories
├── fixtures/
│   └── workspace/            # mock AGENT_WORKSPACE_DIR
│       ├── AGENTS.md
│       ├── SOUL.md
│       ├── memory/
│       │   └── MEMORY.md
│       └── skills/
│           └── test_skill/
│               └── SKILL.md
├── agent/
│   ├── __init__.py
│   ├── test_chunker.py       # RAG chunker (pure logic, no DB)
│   ├── test_tools.py         # Tool execute() + base classes
│   ├── test_search.py        # WebSearchTool (mock SearXNG)
│   ├── test_web_read.py      # WebReadTool (mock Jina + trafilatura)
│   ├── test_models.py        # Model methods, constraints, status transitions
│   ├── test_nodes.py         # Graph node functions (mock LLM)
│   ├── test_runner.py        # AgentRunner, resume logic, approval resolution
│   ├── test_retriever.py     # RAG retriever (needs pgvector)
│   ├── test_ingest.py        # Document ingestion (mock embeddings)
│   ├── test_skills.py        # Skill loader, YAML parsing, registry
│   ├── test_memory.py        # Long-term memory reembed
│   ├── test_signals.py       # on_message_created signal handler
│   ├── test_tasks.py         # Celery tasks (eager mode)
│   ├── test_workflows.py     # Workflow runner, delivery logic
│   └── test_views.py         # View smoke tests (HTTP status codes)
├── chat/
│   ├── __init__.py
│   └── test_views.py         # Chat views
└── core/
    ├── __init__.py
    └── test_llm.py            # LLM client, usage recording
```

### Test Categories & Priority

#### P0 — Pure logic (no DB, no external services)

These are the easiest to write and fastest to run. Start here.

| Test file | Target | Example tests |
|-----------|--------|---------------|
| `test_chunker.py` | `agent.rag.chunker` | `chunk_text` splits correctly, overlap works, hash is stable, empty input handled |
| `test_tools.py` | `agent.tools.base` | `ToolResult.as_dict()`, `to_llm_schema()` format, approval policy values |
| `test_skills.py` | `agent.skills.loader` | `_parse_skill_md` YAML frontmatter parsing, missing `---` handled, tools list extracted |
| `test_nodes.py` (helpers) | `agent.graph.nodes` | `_truncate_history` drops oldest first, `_tool_sig` dedup logic, `_count_tokens` fallback, `_read_workspace_file` missing file |

#### P1 — Database-dependent (Django ORM, needs test DB)

| Test file | Target | Example tests |
|-----------|--------|---------------|
| `test_models.py` | `agent.models` | `KnowledgeDocument` status transitions, `Agent.tools` field default, `AgentRun` lifecycle, `ToolExecution` status choices, `Skill.enabled` default |
| `test_views.py` | `agent.views` | Smoke tests — GET returns 200, POST creates objects, HTMX partial responses, 404 for bad PKs |
| `test_memory.py` | `agent.memory` | `_split_paragraphs`, `reembed` creates/deletes Memory records, skips unchanged hashes |
| `test_signals.py` | `agent.signals` | `on_message_created` triggers agent run, resumes waiting run, skips inactive agent |
| `test_tasks.py` | `agent.tasks` | `execute_agent_run` calls `AgentRunner.run`, handles missing run, retries on error |
| `test_runner.py` | `agent.runner` | `_resolve_approved_tools` handles approved/rejected/missing, `run()` marks status transitions, `AgentState` initial_state built correctly |
| `test_workflows.py` | `agent.workflows` | `_deliver` routes to telegram/announce/silent, creates inbox conversation on first use |

#### P2 — External service mocks (LLM, SearXNG, Jina)

| Test file | Target | Mock | Example tests |
|-----------|--------|------|---------------|
| `test_search.py` | `WebSearchTool` | httpx → SearXNG JSON | Returns results, handles connection error, respects num_results limit, caps at 20, passes language param |
| `test_web_read.py` | `WebReadTool` | httpx → Jina + trafilatura | Jina success, Jina fail → trafilatura fallback, both fail → error, content truncation |
| `test_nodes.py` (LLM) | `call_llm`, `force_conclude` | `core.llm.get_completion` | Tool call → pending_tool_calls, final answer → output, loop_trace recorded, reasoning captured, cancelled run aborts, rag_matches saved, 3-tuple unpack (regression) |
| `test_ingest.py` | `agent.rag.ingest` | `embed_text`, httpx | Chunks created, status transitions, PDF extraction, re-ingest replaces old chunks, error sets status |
| `test_retriever.py` | `agent.rag.retriever` | `embed_text` | Cosine search ordered, threshold filtering, limit respected, inactive docs excluded, non-ready docs excluded |
| `test_llm.py` | `core.llm` | `litellm.completion` | `get_completion` returns response, records `LLMUsage`, handles missing usage gracefully, cost calculation failure doesn't crash |

### Configuration

**`pyproject.toml` additions:**

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings.test"
python_files = "test_*.py"
python_classes = "Test*"
python_functions = "test_*"
addopts = "-v --tb=short"
```

**`config/settings/test.py`:**

- Inherits from `base.py`
- Uses a separate test database (`agent_test_db`)
- `CELERY_TASK_ALWAYS_EAGER = True` — tasks run synchronously in tests
- `AGENT_WORKSPACE_DIR` → `tests/fixtures/workspace/`
- Disables LangSmith tracing
- Sets `SEARXNG_URL` to a dummy value (tests mock httpx anyway)

### Fixtures

**`tests/conftest.py`** — shared fixtures:

- `agent` — creates an `Agent` with default tools enabled
- `agent_run` — creates an `AgentRun` linked to the agent
- `conversation` — creates a `Conversation` with a few messages
- `knowledge_doc` — creates a `KnowledgeDocument` with status `ready`
- `skill_dir` — temp directory with a sample SKILL.md
- `mock_llm` — patches `core.llm.get_completion` to return canned responses
- `mock_embed` — patches `core.memory.embed_text` to return a fixed 1536-dim vector
- `mock_httpx` — patches httpx.get/post for tool tests
- `workspace_dir` — temp copy of `tests/fixtures/workspace/`

**`tests/factories.py`** — factory_boy factories:

- `AgentFactory`
- `AgentRunFactory` (with status sequence helpers)
- `ConversationFactory`
- `MessageFactory`
- `KnowledgeDocumentFactory`
- `DocumentChunkFactory` (with fake embedding vector)
- `ToolExecutionFactory`
- `SkillFactory`
- `WorkflowFactory`
- `LLMUsageFactory`

### Test Markers

```python
# Run only fast tests (P0):
# uv run pytest -m "not db and not external"

# Run all tests:
# uv run pytest

pytest.mark.db         # needs PostgreSQL test database
pytest.mark.external   # mocks external services (LLM, SearXNG, Jina)
```

### CI Integration

For now: run locally with `uv run pytest`. CI pipeline can be added later
when a GitHub Actions workflow is set up.

## Implementation Order

```
Step 1: Add dev dependencies (pytest, pytest-django, factory-boy, respx)
Step 2: Create config/settings/test.py
Step 3: Create tests/conftest.py + factories.py + fixtures/workspace/
Step 4: Write P0 tests (pure logic — chunker, tool helpers, skill parser, node helpers)
Step 5: Write P1 tests (models, views, signals, runner, tasks, workflows)
Step 6: Write P2 tests (mocked LLM, search, web_read, ingest, retriever)
```

Start with Step 1–4. This gives immediate coverage of the most testable code
and establishes the pattern for the rest.

## Out of Scope

- End-to-end tests (full agent run with real LLM)
- Performance / load testing
- Frontend / browser tests (Playwright, Selenium) — revisit if multi-user
- CI/CD pipeline setup (separate spec if needed)

## Open Questions

None — ready to implement.
