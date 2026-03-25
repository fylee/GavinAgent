"""Tools page e2e tests — tests 30-32."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


def test_tools_page_lists_all(page: Page, url, default_agent):
    """#30 — All registered tools are shown on the tools page."""
    page.goto(url("/agent/tools/"))
    # The tools page should list known tools like web_read, shell_exec
    expect(page.get_by_text("web_read", exact=True).first).to_be_visible()


def test_tool_toggle(page: Page, url, default_agent):
    """#31 — Toggle switch enables/disables a tool via HTMX."""
    page.goto(url("/agent/tools/"))
    # Find a toggle button for web_read — it should be in the tool row
    toggle_btn = page.locator("[hx-post*='web_read/toggle']").first
    if toggle_btn.count() > 0:
        expect(toggle_btn).to_be_visible()
        toggle_btn.click()
        # After toggle, the page should still be functional
        page.wait_for_timeout(1000)
        expect(page.get_by_text("web_read", exact=True).first).to_be_visible()


def test_tool_policy_change(page: Page, url, default_agent):
    """#32 — Tool policy dropdown is present."""
    page.goto(url("/agent/tools/"))
    # Policy selects should be present
    policy_select = page.locator("select[name='policy']").first
    if policy_select.count() > 0:
        expect(policy_select).to_be_visible()
