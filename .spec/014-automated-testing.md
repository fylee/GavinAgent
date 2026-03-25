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
- **pytest-asyncio** — for any async MCP/tool tests (if needed later)
- Package management via `uv add --dev`

### Test Directory Structure

```
tests/
├── conftest.py               # pytest-django config, shared fixtures
├── factories.py              # factory_boy model factories
├── agent/
│   ├── __init__.py
│   ├── test_chunker.py       # RAG chunker (pure logic, no DB)
│   ├── test_tools.py         # Tool execute() methods
│   ├── test_search.py        # WebSearchTool (mock SearXNG)
│   ├── test_models.py        # Model methods, constraints, status transitions
│   ├── test_nodes.py         # Graph node functions (mock LLM)
│   ├── test_retriever.py     # RAG retriever (needs pgvector)
│   ├── test_ingest.py        # Document ingestion (mock embeddings)
│   ├── test_skills.py        # Skill loader, YAML parsing
│   ├── test_memory.py        # Long-term memory reembed
│   └── test_views.py         # View smoke tests (HTTP status codes)
├── chat/
│   ├── __init__.py
│   └── test_views.py         # Chat views
└── core/
    ├── __init__.py
    └── test_llm.py            # LLM client helpers
```

### Test Categories & Priority

#### P0 — Pure logic (no DB, no external services)

These are the easiest to write and fastest to run. Start here.

| Test file | Target | Example tests |
|-----------|--------|---------------|
| `test_chunker.py` | `agent.rag.chunker` | `chunk_text` splits correctly, overlap works, hash is stable, empty input handled |
| `test_tools.py` | `agent.tools.base` | `ToolResult.as_dict()`, `to_llm_schema()` format |
| `test_skills.py` | `agent.skills.loader` | `_parse_skill_md` YAML frontmatter parsing, missing `---` handled |
| `test_nodes.py` (helpers) | `agent.graph.nodes` | `_truncate_history` drops oldest first, `_tool_sig` dedup logic, `_count_tokens` fallback |

#### P1 — Database-dependent (Django ORM, needs test DB)

| Test file | Target | Example tests |
|-----------|--------|---------------|
| `test_models.py` | `agent.models` | `KnowledgeDocument` status transitions, `Agent.tools` field default, `AgentRun` lifecycle |
| `test_views.py` | `agent.views` | Smoke tests — GET returns 200, POST creates objects, HTMX partial responses |
| `test_memory.py` | `agent.memory` | `_split_paragraphs`, `reembed` creates/deletes Memory records |

#### P2 — External service mocks (LLM, SearXNG, Jina)

| Test file | Target | Mock | Example tests |
|-----------|--------|------|---------------|
| `test_search.py` | `WebSearchTool` | httpx → SearXNG JSON | Returns results, handles connection error, respects num_results limit |
| `test_nodes.py` (LLM) | `call_llm`, `force_conclude` | `core.llm.get_completion` | Tool call response → returns pending_tool_calls, final answer → returns output, loop_trace recorded |
| `test_ingest.py` | `agent.rag.ingest` | `embed_text`, httpx | Chunks created, status transitions, PDF extraction |
| `test_retriever.py` | `agent.rag.retriever` | `embed_text` | Cosine search returns ordered results, threshold filtering, inactive docs excluded |

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

```python
from .base import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "agent_test_db",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "localhost",
        "PORT": "5432",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
AGENT_WORKSPACE_DIR = str(Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "workspace")
```

### Fixtures

**`tests/conftest.py`** — shared fixtures:

- `agent` — creates an `Agent` with default tools enabled
- `agent_run` — creates an `AgentRun` linked to the agent
- `conversation` — creates a `Conversation` with a few messages
- `knowledge_doc` — creates a `KnowledgeDocument` with status `ready`
- `skill_dir` — temp directory with a sample SKILL.md
- `mock_llm` — patches `core.llm.get_completion` to return canned responses
- `mock_embed` — patches `core.memory.embed_text` to return a fixed vector

**`tests/factories.py`** — factory_boy factories:

- `AgentFactory`
- `AgentRunFactory`
- `ConversationFactory`
- `MessageFactory`
- `KnowledgeDocumentFactory`
- `DocumentChunkFactory`
- `ToolExecutionFactory`
- `SkillFactory`

### CI Integration

For now: run locally with `uv run pytest`. CI pipeline can be added later
when a GitHub Actions workflow is set up.

## Implementation Order

```
Step 1: Add dev dependencies (pytest, pytest-django, factory-boy)
Step 2: Create config/settings/test.py
Step 3: Create tests/conftest.py + factories.py
Step 4: Write P0 tests (pure logic — chunker, tool helpers, skill parser, node helpers)
Step 5: Write P1 tests (models, views smoke tests)
Step 6: Write P2 tests (mocked LLM, search, ingest)
```

Start with Step 1–4. This gives immediate coverage of the most testable code
and establishes the pattern for the rest.

## Out of Scope

- End-to-end tests (full agent run with real LLM)
- Performance / load testing
- Frontend / browser tests (Playwright, Selenium)
- CI/CD pipeline setup (separate spec if needed)

## Open Questions

None — ready to implement.
