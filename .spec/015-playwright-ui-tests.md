# 015 — Playwright UI Tests

## Goal

Add end-to-end UI tests using Playwright to verify that the Chat and Agent
interfaces render correctly, HTMX interactions work, and critical user flows
complete without errors.

## Background

Spec 014 covers pytest-based unit/integration tests for backend logic. However,
the UI relies heavily on HTMX partials, Alpine.js state, and dynamic polling
that cannot be tested via Django's test client alone. Playwright tests fill
this gap by driving a real browser.

## Proposed Solution

### Test Framework

- **playwright** — browser automation
- **pytest-playwright** — pytest integration
- Package management via `uv add --dev`

### Test Directory Structure

```
tests/
├── e2e/
│   ├── __init__.py
│   ├── conftest.py           # Playwright fixtures, test server, seed data
│   ├── test_chat.py          # Chat UI tests (10 tests)
│   ├── test_chat_settings.py # Chat settings panel (5 tests)
│   ├── test_agent_nav.py     # Agent navigation + page load tests (5 tests)
│   ├── test_dashboard.py     # Dashboard cards + run lists (5 tests)
│   ├── test_agent_runs.py    # Run detail, status polling, cancel (8 tests)
│   ├── test_agent_crud.py    # Agent create/edit/delete (6 tests)
│   ├── test_tools.py         # Tools page, toggle (3 tests)
│   ├── test_skills.py        # Skills page, toggle (3 tests)
│   ├── test_knowledge.py     # Knowledge upload, status (6 tests)
│   ├── test_memory.py        # Memory search, edit (3 tests)
│   ├── test_workspace.py     # Workspace file edit (2 tests)
│   ├── test_workflows.py     # Workflow create, toggle, run now (8 tests)
│   ├── test_mcp.py           # MCP server add, toggle, refresh, delete (8 tests)
│   └── test_monitoring.py    # Activity feed, approvals, health, cost (7 tests)
```

### Test Categories

#### Chat UI (`test_chat.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 1 | `test_chat_page_loads` | `/chat/` renders conversation list |
| 2 | `test_create_conversation` | Click new conversation → page loads |
| 3 | `test_send_message` | Type message → submit → message appears in list |
| 4 | `test_message_appears_in_list` | Sent message visible with correct role badge |
| 5 | `test_model_selector` | Model dropdown renders with available models |
| 6 | `test_agent_toggle` | Agent toggle button renders and responds to click |
| 7 | `test_conversation_title_updates` | Title updates after first message |
| 8 | `test_empty_message_blocked` | Empty submit doesn't create a message |
| 9 | `test_chat_to_agent_nav` | "→ Agent" link navigates to agent dashboard |
| 10 | `test_settings_panel_toggle` | Settings gear opens/closes the panel (Alpine) |

#### Agent Navigation (`test_agent_nav.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 11 | `test_dashboard_loads` | `/agent/` renders dashboard with stats |
| 12 | `test_all_nav_links_work` | Every nav link (Dashboard, Agents, Runs, Monitoring, Logs, Memory, Knowledge, Tools, Skills, Workspace, MCP, Workflows) loads without error |
| 13 | `test_chat_link_navigates` | "← Chat" link works |
| 14 | `test_runs_page_loads` | `/agent/runs/` shows run list table |
| 15 | `test_logs_page_loads` | `/agent/logs/` renders log entries |

#### Agent Runs (`test_agent_runs.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 16 | `test_run_detail_loads` | Run detail page shows input, status, tool executions |
| 17 | `test_run_status_badge` | Status badge shows correct color (completed/failed/running) |
| 18 | `test_run_skills_badges` | Triggered skills shown as purple badges |
| 19 | `test_run_rag_badges` | Knowledge matches shown as teal badges with similarity |
| 20 | `test_run_loop_trace_expand` | Loop Trace section expands on click |
| 21 | `test_run_tool_output_toggle` | "Show output" / "Hide output" toggle works |
| 22 | `test_run_cancel_button` | Cancel Run button changes status to failed |
| 23 | `test_run_status_polling` | Running status triggers HTMX polling (hx-trigger="every 2s") |

#### Agent CRUD (`test_agent_crud.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 24 | `test_agent_list_page` | Agents page shows existing agents |
| 25 | `test_create_agent` | Fill form → submit → agent appears in list |
| 26 | `test_edit_agent_name` | Edit name → save → updated name shown |
| 27 | `test_toggle_agent_tools` | Check/uncheck tool checkboxes → saved |
| 28 | `test_delete_agent` | Delete → confirm → agent removed from list |
| 29 | `test_set_default_agent` | Set as default → badge appears |

#### Tools (`test_tools.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 30 | `test_tools_page_lists_all` | All registered tools shown |
| 31 | `test_tool_toggle` | Toggle switch enables/disables tool |
| 32 | `test_tool_policy_change` | Change approval policy dropdown |

#### Skills (`test_skills.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 33 | `test_skills_page_loads` | Skills listed with enable/disable toggles |
| 34 | `test_skill_toggle` | Toggle enables/disables skill via HTMX |
| 35 | `test_skill_delete` | Delete → confirm → skill removed |

#### Knowledge Base (`test_knowledge.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 36 | `test_knowledge_page_loads` | Knowledge page shows documents list |
| 37 | `test_add_text_document` | Fill text form → submit → document appears |
| 38 | `test_add_url_document` | Enter URL → submit → document appears with pending status |
| 39 | `test_toggle_document_active` | Toggle active/inactive |
| 40 | `test_delete_document` | Delete → confirm → document removed |
| 41 | `test_document_status_badge` | Status badge shows correct color per status |

#### Memory (`test_memory.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 42 | `test_memory_page_loads` | Memory page shows paragraphs |
| 43 | `test_memory_search` | Type search query → results appear via HTMX |
| 44 | `test_memory_paragraph_edit` | Edit paragraph → save → updated text shown |

#### Workspace (`test_workspace.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 45 | `test_workspace_lists_files` | Workspace page shows files |
| 46 | `test_edit_workspace_file` | Edit file content → save → changes persisted |

#### Workflows (`test_workflows.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 47 | `test_workflow_list_page` | Workflows page loads with table of workflows |
| 48 | `test_create_workflow` | Fill YAML form → submit → workflow appears in list |
| 49 | `test_toggle_workflow` | Toggle active/inactive via HTMX button |
| 50 | `test_delete_workflow` | Delete → hx-confirm dialog → removed from list |
| 51 | `test_workflow_detail_page` | Detail page shows metadata, steps, output, YAML editor |
| 52 | `test_workflow_run_now` | "Run now" button triggers workflow execution |
| 53 | `test_workflow_reload` | Reload button scans workspace for YAML changes |
| 54 | `test_workflow_step_expand` | Step accordion expands to show prompt (Alpine) |

#### MCP Servers (`test_mcp.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 55 | `test_mcp_page_loads` | `/agent/mcp/` renders server list + warning banner |
| 56 | `test_mcp_add_form_opens` | "+ Add server" button loads HTMX form into `#add-form-slot` |
| 57 | `test_mcp_toggle_server` | Enable/Disable button swaps server card via HTMX |
| 58 | `test_mcp_refresh_server` | Refresh button re-renders server card |
| 59 | `test_mcp_delete_server` | Delete → hx-confirm → server removed from list |
| 60 | `test_mcp_edit_server` | Edit button loads detail form into `#add-form-slot` |
| 61 | `test_mcp_connection_status_dot` | Status dot color reflects connected/error/disconnected |
| 62 | `test_mcp_error_display` | Error state shows `last_error` message |

#### Monitoring (`test_monitoring.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 63 | `test_monitoring_page_loads` | `/agent/monitoring/` renders activity + approval + cost panels |
| 64 | `test_activity_filter_toggle` | "All" / "Agent only" filter buttons toggle event visibility (Alpine) |
| 65 | `test_approval_filter_links` | Approval status filter links (All/Approved/Rejected/Pending) reload page |
| 66 | `test_approve_tool_execution` | Approve button on pending row updates status via HTMX |
| 67 | `test_reject_tool_execution` | Reject button on pending row updates status via HTMX |
| 68 | `test_health_status_auto_refresh` | Health container has `hx-trigger="load, every 30s"` |
| 69 | `test_cost_panels_render` | Cost Today / Last 30 Days / By Source / By Model panels render |

#### Chat Settings (`test_chat_settings.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 70 | `test_settings_system_prompt` | System prompt textarea renders with current value |
| 71 | `test_settings_temperature_slider` | Temperature slider changes value, "Reset to default" works |
| 72 | `test_settings_max_tokens` | Max tokens input accepts numeric value |
| 73 | `test_settings_auto_save` | Changing a setting triggers HTMX PATCH with `delay:400ms` |
| 74 | `test_settings_saved_indicator` | "Saved" text appears briefly after successful PATCH |

#### Dashboard Details (`test_dashboard.py`)

| # | Test Case | What it verifies |
|---|-----------|-----------------|
| 75 | `test_dashboard_default_agent_card` | Default Agent card shows agent name + model or "None configured" |
| 76 | `test_dashboard_heartbeat_card` | Last Heartbeat card shows timestamp + status badge |
| 77 | `test_dashboard_active_runs_count` | Active Runs card shows count |
| 78 | `test_dashboard_active_runs_list` | Active runs section lists running/waiting runs with View links |
| 79 | `test_dashboard_recent_runs_list` | Recent runs section shows completed/failed runs |

### Configuration

**`conftest.py` (e2e):**

```python
import pytest
from playwright.sync_api import Page

@pytest.fixture(scope="session")
def live_server():
    """Start Django dev server for Playwright tests."""
    # Use pytest-django's live_server or a custom subprocess

@pytest.fixture()
def seeded_db(db):
    """Create seed data: agent, conversation, runs, skills, etc."""
    ...

@pytest.fixture()
def page(browser, live_server) -> Page:
    """Open a fresh browser page pointing at the test server."""
    p = browser.new_page()
    yield p
    p.close()
```

**Browser:** Chromium (default, fastest). Can add Firefox/WebKit later.

### Running

```bash
# Install Playwright browsers (one-time)
uv run playwright install chromium

# Run all UI tests
uv run pytest tests/e2e/ -v

# Run only chat tests
uv run pytest tests/e2e/test_chat.py -v

# Run headed (visible browser)
uv run pytest tests/e2e/ -v --headed
```

## Implementation Order

```
Step 1: Add dev deps (playwright, pytest-playwright)
Step 2: Create tests/e2e/conftest.py with fixtures + seed data
Step 3: Write test_chat.py + test_chat_settings.py (15 tests)
Step 4: Write test_agent_nav.py + test_dashboard.py (10 tests)
Step 5: Write test_agent_runs.py + test_agent_crud.py (14 tests)
Step 6: Write test_tools.py + test_skills.py + test_knowledge.py (12 tests)
Step 7: Write test_memory.py + test_workspace.py (5 tests)
Step 8: Write test_workflows.py + test_mcp.py + test_monitoring.py (23 tests)
```

## Out of Scope

- Testing with real LLM responses (mock the agent run)
- Cross-browser testing (Chromium only for now)
- Mobile responsive testing
- Accessibility (a11y) testing
- Screenshot comparison / visual regression

## Dependencies on Spec 014

- Reuses `tests/factories.py` for seed data
- Reuses `config/settings/test.py` (with live server additions)
- Requires test DB to be running

## Open Questions

1. Should we use `pytest-django`'s `live_server` fixture or manage our own
   subprocess? (`live_server` is simpler but runs on a random port.)
