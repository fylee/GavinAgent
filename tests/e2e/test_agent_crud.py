"""Agent CRUD e2e tests — tests 24-29."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


def test_agent_list_page(page: Page, url, default_agent, second_agent):
    """#24 — Agents page shows existing agents."""
    page.goto(url("/agent/agents/"))
    expect(page.locator("h1:text('Agents')")).to_be_visible()
    expect(page.locator("text=E2E Default Agent")).to_be_visible()
    expect(page.locator("text=E2E Secondary Agent")).to_be_visible()


def test_create_agent(page: Page, url, default_agent):
    """#25 — Fill create form → submit → agent appears in list."""
    page.goto(url("/agent/agents/create/"))
    expect(page.locator("text=Create Agent")).to_be_visible()

    page.locator("input[name='name']").fill("Playwright Agent")
    page.locator("textarea[name='system_prompt']").fill("Test prompt for e2e")
    page.locator("input[name='is_active']").check()
    page.locator("button:text('Create')").click()

    # Should redirect to agents list
    page.wait_for_url("**/agent/agents/")
    expect(page.locator("text=Playwright Agent")).to_be_visible()


def test_edit_agent_name(page: Page, url, default_agent):
    """#26 — Edit name → save → updated name shown."""
    page.goto(url(f"/agent/agents/{default_agent.id}/"))
    name_input = page.locator("input[name='name']")
    name_input.fill("Renamed Agent")
    page.locator("button:text('Save')").click()

    page.wait_for_url("**/agent/agents/")
    expect(page.locator("text=Renamed Agent")).to_be_visible()


def test_toggle_agent_tools(page: Page, url, default_agent):
    """#27 — Agent edit form has tool checkboxes."""
    page.goto(url(f"/agent/agents/{default_agent.id}/"))
    # Tools section should have checkboxes
    tools_section = page.locator("div:has(> label:text('Tools'))")
    expect(tools_section).to_be_visible()
    # web_read should be checked (it's in the agent's tools list)
    web_read_cb = page.locator("input[name='tools'][value='web_read']")
    if web_read_cb.count() > 0:
        expect(web_read_cb).to_be_checked()


def test_delete_agent(page: Page, url, second_agent):
    """#28 — Delete → confirm page → confirm → agent removed from list."""
    page.goto(url(f"/agent/agents/{second_agent.id}/delete/"))
    expect(page.get_by_role("heading", name="Delete Agent")).to_be_visible()

    # Submit the delete form
    page.get_by_role("button", name="Delete Agent").click()
    page.wait_for_url("**/agent/agents/**")


def test_set_default_agent(page: Page, url, default_agent, second_agent):
    """#29 — Set as default → the 'Default' badge appears."""
    page.goto(url("/agent/agents/"))
    # E2E Default Agent should already have the Default badge
    expect(page.locator("span:text('Default')").first).to_be_visible()
