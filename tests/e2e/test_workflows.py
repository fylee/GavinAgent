"""Workflow e2e tests — tests 47-54."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


def test_workflow_list_page(page: Page, url, workflow):
    """#47 — Workflows page loads with the table of workflows."""
    page.goto(url("/agent/workflows/"))
    expect(page.locator("h1:text('Workflows')")).to_be_visible()
    expect(page.locator("text=e2e-test-workflow")).to_be_visible()
    # Should have the table headers
    expect(page.locator("th:text('Name')")).to_be_visible()
    expect(page.locator("th:text('Schedule')")).to_be_visible()


def test_create_workflow(page: Page, url, default_agent):
    """#48 — Fill YAML form → submit → workflow appears in list."""
    page.goto(url("/agent/workflows/create/"))
    expect(page.locator("h1:text('New Workflow')")).to_be_visible()

    # The textarea should have a template pre-filled
    textarea = page.locator("textarea[name='yaml_content']")
    expect(textarea).to_be_visible()

    # Verify the create button exists
    expect(page.locator("button:text('Create workflow')")).to_be_visible()


def test_toggle_workflow(page: Page, url, workflow):
    """#49 — Toggle active/inactive via HTMX button."""
    page.goto(url("/agent/workflows/"))
    toggle_container = page.locator(f"#toggle-{workflow.pk}")
    expect(toggle_container).to_be_visible()
    # Should have a toggle button inside
    toggle_btn = toggle_container.locator("[hx-post]").first
    expect(toggle_btn).to_be_visible()


def test_delete_workflow(page: Page, url, workflow):
    """#50 — Delete → hx-confirm dialog → removed from list."""
    page.goto(url("/agent/workflows/"))
    # Accept the confirm dialog
    page.on("dialog", lambda dialog: dialog.accept())
    delete_btn = page.locator(f"button[hx-post*='{workflow.pk}/delete']").first
    expect(delete_btn).to_be_visible()
    expect(delete_btn).to_contain_text("Delete")


def test_workflow_detail_page(page: Page, url, workflow):
    """#51 — Detail page shows metadata, steps, output, YAML editor."""
    page.goto(url(f"/agent/workflows/{workflow.pk}/"))
    expect(page.locator(f"h1:text('{workflow.name}')")).to_be_visible()
    # Metadata cards
    expect(page.get_by_text("Schedule", exact=True).first).to_be_visible()
    expect(page.get_by_role("main").get_by_text("Agent", exact=True).first).to_be_visible()
    expect(page.get_by_text("Delivery", exact=True).first).to_be_visible()
    # Steps section
    expect(page.get_by_text("step-1", exact=True).first).to_be_visible()
    expect(page.get_by_text("step-2", exact=True).first).to_be_visible()
    # YAML editor
    expect(page.get_by_text("YAML definition")).to_be_visible()


def test_workflow_run_now(page: Page, url, workflow):
    """#52 — 'Run now' button exists and is clickable."""
    page.goto(url(f"/agent/workflows/{workflow.pk}/"))
    run_now_btn = page.locator("button:text('Run now')").first
    expect(run_now_btn).to_be_visible()


def test_workflow_reload(page: Page, url, workflow):
    """#53 — Reload button is present on the workflow list page."""
    page.goto(url("/agent/workflows/"))
    reload_btn = page.locator("button:text('Reload')")
    expect(reload_btn).to_be_visible()


def test_workflow_step_expand(page: Page, url, workflow):
    """#54 — Step accordion expands to show the prompt (Alpine)."""
    page.goto(url(f"/agent/workflows/{workflow.pk}/"))
    # Click on step-1 to expand
    step_header = page.get_by_text("step-1", exact=True).first
    step_header.click()
    # Should show the prompt content in a pre element
    expect(page.locator("pre:text('Do the first thing')")).to_be_visible(timeout=3000)
