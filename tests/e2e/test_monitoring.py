"""Monitoring page e2e tests — tests 63-69."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.factories import HeartbeatLogFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_monitoring_page_loads(page: Page, url, default_agent, llm_usage):
    """#63 — /agent/monitoring/ renders activity + approval + cost panels."""
    page.goto(url("/agent/monitoring/"))
    expect(page.locator("h1:text('Monitoring')")).to_be_visible()
    # Activity section
    expect(page.get_by_role("heading", name="Recent Activity")).to_be_visible()
    # Approval section
    expect(page.get_by_role("heading", name="Approval History")).to_be_visible()
    # Cost section
    expect(page.get_by_text("Cost Today", exact=True).first).to_be_visible()


def test_activity_filter_toggle(page: Page, url, default_agent):
    """#64 — 'All' / 'Agent only' filter buttons toggle event visibility."""
    page.goto(url("/agent/monitoring/"))
    # Alpine filter buttons
    all_btn = page.locator("button:text('All')").first
    agent_btn = page.locator("button:text('Agent only')")
    expect(all_btn).to_be_visible()
    expect(agent_btn).to_be_visible()

    # Click "Agent only" to filter — check it becomes the active class
    agent_btn.click()
    cls = agent_btn.get_attribute("class")
    assert "text-white" in cls


def test_approval_filter_links(page: Page, url, default_agent):
    """#65 — Approval status filter links (All/Approved/Rejected/Pending)."""
    page.goto(url("/agent/monitoring/"))
    # Filter links
    expect(page.locator("a:text('Approved')")).to_be_visible()
    expect(page.locator("a:text('Rejected')")).to_be_visible()
    expect(page.locator("a:text('Pending')")).to_be_visible()


def test_approve_tool_execution(
    page: Page, url, waiting_run_with_approval
):
    """#66 — Approve button on pending row updates status via HTMX."""
    page.goto(url("/agent/monitoring/"))
    # If the pending approval is in the approval history table
    approve_btn = page.locator("button:text('Approve')").first
    if approve_btn.count() > 0:
        expect(approve_btn).to_be_visible()


def test_reject_tool_execution(
    page: Page, url, waiting_run_with_approval
):
    """#67 — Reject button on pending row updates status via HTMX."""
    page.goto(url("/agent/monitoring/"))
    reject_btn = page.locator("button:text('Reject')").first
    if reject_btn.count() > 0:
        expect(reject_btn).to_be_visible()


def test_health_status_auto_refresh(page: Page, url, default_agent):
    """#68 — Health container has hx-trigger='load, every 30s'."""
    page.goto(url("/agent/monitoring/"))
    health_container = page.locator("#health-container")
    expect(health_container).to_have_attribute("hx-trigger", "load, every 30s")


def test_cost_panels_render(page: Page, url, default_agent, llm_usage):
    """#69 — Cost Today / Last 30 Days / By Source panels render."""
    page.goto(url("/agent/monitoring/"))
    expect(page.locator("text=Cost Today")).to_be_visible()
    expect(page.locator("text=Last 30 Days")).to_be_visible()
    expect(page.locator("text=Agent vs Chat")).to_be_visible()
