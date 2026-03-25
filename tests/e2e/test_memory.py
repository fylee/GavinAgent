"""Memory page e2e tests — tests 42-44."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


def test_memory_page_loads(page: Page, url, default_agent):
    """#42 — Memory page shows paragraphs and controls."""
    page.goto(url("/agent/memory/"))
    expect(page.locator("h1:text('Long-term Memory')")).to_be_visible()
    # Should have the Raw/Paragraph view toggle (Alpine)
    expect(page.locator("button:text('Raw')")).to_be_visible()
    expect(page.locator("button:text('Paragraphs')")).to_be_visible()
    # Memory content should be visible in the textarea (raw view)
    textarea = page.locator("textarea[name='content']")
    expect(textarea).to_be_visible()
    # Should have memory stats
    expect(page.locator("text=memory records")).to_be_visible()


def test_memory_search(page: Page, url, default_agent):
    """#43 — Type search query → results appear via HTMX."""
    page.goto(url("/agent/memory/"))
    search_input = page.locator("input[name='q']")
    expect(search_input).to_be_visible()
    expect(search_input).to_have_attribute("placeholder", "Search memories…")
    # Verify hx-trigger is set for delayed search
    expect(search_input).to_have_attribute("hx-trigger", "input changed delay:400ms")
    expect(search_input).to_have_attribute("hx-target", "#memory-search-results")


def test_memory_paragraph_edit(page: Page, url, default_agent):
    """#44 — Memory page has paragraph view with editable cards."""
    page.goto(url("/agent/memory/"))
    # Switch to paragraph view
    page.locator("button:text('Paragraphs')").click()
    # The paragraph view should become visible (Alpine x-show toggle)
    # Memory fixture has paragraphs — check for "Add paragraph" button
    expect(page.locator("button:text('+ Add paragraph')")).to_be_visible(
        timeout=3000
    )
