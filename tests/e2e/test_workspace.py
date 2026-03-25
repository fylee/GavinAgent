"""Workspace page e2e tests — tests 45-46."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


def test_workspace_lists_files(page: Page, url, default_agent):
    """#45 — Workspace page shows AGENTS.md and SOUL.md files."""
    page.goto(url("/agent/workspace/"))
    expect(page.locator("text=AGENTS.md")).to_be_visible()
    expect(page.locator("text=SOUL.md")).to_be_visible()


def test_edit_workspace_file(page: Page, url, default_agent):
    """#46 — Edit file content → save → 'Saved' message appears."""
    page.goto(url("/agent/workspace/AGENTS.md/"))
    page.wait_for_load_state("domcontentloaded")
    # Page should show the filename heading
    expect(page.locator("h1")).to_contain_text("AGENTS.md")
    # Textarea for editing the file
    textarea = page.locator("textarea[name='content']")
    expect(textarea).to_be_visible(timeout=5000)

    # Modify the content
    textarea.fill("# Updated by Playwright\nTest content.")
    page.locator("button:text('Save')").click()

    # Should see the 'Saved' confirmation via HTMX
    expect(page.locator("text=Saved")).to_be_visible(timeout=5000)
    expect(page.locator("h1")).to_contain_text("AGENTS.md")
    # Textarea for editing the file
    textarea = page.locator("textarea[name='content']")
    expect(textarea).to_be_visible(timeout=5000)

    # Modify the content
    textarea.fill("# Updated by Playwright\nTest content.")
    page.locator("button:text('Save')").click()

    # Should see the 'Saved' confirmation via HTMX
    expect(page.locator("text=Saved")).to_be_visible(timeout=5000)
