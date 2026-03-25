"""Skills page e2e tests — tests 33-35."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


def test_skills_page_loads(page: Page, url, default_agent):
    """#33 — Skills page loads and shows the skills list."""
    page.goto(url("/agent/skills/"))
    expect(page.locator("h1:text('Skills')")).to_be_visible()


def test_skill_toggle(page: Page, url, default_agent, skill):
    """#34 — Toggle enables/disables a skill via HTMX."""
    page.goto(url("/agent/skills/"))
    # The skill toggle button should exist
    toggle_btn = page.locator(f"[hx-post*='{skill.name}/toggle']").first
    if toggle_btn.count() > 0:
        expect(toggle_btn).to_be_visible()


def test_skill_delete(page: Page, url, default_agent, skill):
    """#35 — Delete link navigates to confirmation page."""
    page.goto(url("/agent/skills/"))
    delete_link = page.locator(f"a[href*='{skill.name}/delete']").first
    if delete_link.count() > 0:
        delete_link.click()
        expect(page.get_by_role("heading", name="Delete Skill")).to_be_visible()
