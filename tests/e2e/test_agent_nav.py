"""Agent navigation e2e tests — tests 11-15."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


NAV_LINKS = [
    ("Dashboard", "/agent/"),
    ("Agents", "/agent/agents/"),
    ("Runs", "/agent/runs/"),
    ("Monitoring", "/agent/monitoring/"),
    ("Logs", "/agent/logs/"),
    ("Memory", "/agent/memory/"),
    ("Knowledge", "/agent/knowledge/"),
    ("Tools", "/agent/tools/"),
    ("Skills", "/agent/skills/"),
    ("Workspace", "/agent/workspace/"),
    ("MCP", "/agent/mcp/"),
    ("Workflows", "/agent/workflows/"),
]


def test_dashboard_loads(page: Page, url, default_agent):
    """#11 — /agent/ renders the dashboard with stats cards."""
    page.goto(url("/agent/"))
    expect(page.locator("h1:text('Dashboard')")).to_be_visible()
    # Should have the 3 stat card labels
    expect(page.get_by_text("Default Agent", exact=True)).to_be_visible()
    expect(page.get_by_text("Last Heartbeat", exact=True)).to_be_visible()
    expect(page.get_by_text("Active Runs", exact=True)).to_be_visible()


def test_all_nav_links_work(page: Page, url, default_agent):
    """#12 — Every nav link loads without a server error."""
    for label, path in NAV_LINKS:
        resp = page.goto(url(path))
        assert resp is not None and resp.ok, f"{label} ({path}) returned {resp.status if resp else 'None'}"
        # Verify at least the nav bar is present (base_agent.html loaded)
        expect(page.locator("nav")).to_be_visible()


def test_chat_link_navigates(page: Page, url, default_agent):
    """#13 — '← Chat' link in the agent nav navigates to /chat/."""
    page.goto(url("/agent/"))
    page.locator("a:text('← Chat')").click()
    page.wait_for_url("**/chat/**")
    expect(page.get_by_role("main").get_by_role("button", name="New Chat")).to_be_visible()


def test_runs_page_loads(page: Page, url, completed_run):
    """#14 — /agent/runs/ shows the run list with the seeded run."""
    page.goto(url("/agent/runs/"))
    expect(page.locator("text=Search for Python tutorials")).to_be_visible()


def test_logs_page_loads(page: Page, url, default_agent):
    """#15 — /agent/logs/ renders without error."""
    page.goto(url("/agent/logs/"))
    # The logs page should have Heartbeat Logs and Tool Executions sections
    expect(page.locator("body")).to_contain_text("Heartbeat")
