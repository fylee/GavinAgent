"""Dashboard detail e2e tests — tests 75-79."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.factories import HeartbeatLogFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_dashboard_default_agent_card(page: Page, url, default_agent):
    """#75 — Default Agent card shows agent name + model."""
    page.goto(url("/agent/"))
    card = page.locator("div:has(> p:text('Default Agent'))").first
    expect(card).to_contain_text("E2E Default Agent")
    expect(card).to_contain_text(default_agent.model)


def test_dashboard_heartbeat_card(page: Page, url, default_agent):
    """#76 — Last Heartbeat card shows timestamp + status badge."""
    hb = HeartbeatLogFactory(status="ok")
    page.goto(url("/agent/"))
    card = page.locator("div:has(> p:text('Last Heartbeat'))").first
    expect(card).to_contain_text("OK")


def test_dashboard_active_runs_count(page: Page, url, running_run):
    """#77 — Active Runs card shows the count."""
    page.goto(url("/agent/"))
    card = page.locator("div:has(> p:text('Active Runs'))").first
    expect(card).to_contain_text("1")


def test_dashboard_active_runs_list(page: Page, url, running_run):
    """#78 — Active runs section lists running runs with View link."""
    page.goto(url("/agent/"))
    expect(page.get_by_text("Active Runs").last).to_be_visible()
    expect(page.get_by_text("Running task").first).to_be_visible()
    expect(page.locator("a:text('View')").first).to_be_visible()


def test_dashboard_recent_runs_list(page: Page, url, completed_run):
    """#79 — Recent runs section shows completed runs."""
    page.goto(url("/agent/"))
    expect(page.locator("text=Recent Runs")).to_be_visible()
    expect(page.locator("text=Search for Python tutorials")).to_be_visible()
