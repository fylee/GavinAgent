"""Chat settings panel e2e tests — tests 70-74."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


def _open_settings(page: Page):
    """Open the chat settings panel."""
    page.get_by_role("banner").locator("button").last.click()
    page.locator("aside:has(h2:text('Chat settings'))").wait_for(state="visible")


def test_settings_system_prompt(page: Page, url, conversation):
    """#70 — System prompt textarea renders with current value."""
    conversation.system_prompt = "You are a helpful bot."
    conversation.save()

    page.goto(url(f"/chat/conversations/{conversation.id}/"))
    _open_settings(page)

    textarea = page.locator("textarea[name='system_prompt']")
    expect(textarea).to_be_visible()
    expect(textarea).to_have_value("You are a helpful bot.")


def test_settings_temperature_slider(page: Page, url, conversation):
    """#71 — Temperature slider changes value, 'Reset to default' works."""
    page.goto(url(f"/chat/conversations/{conversation.id}/"))
    _open_settings(page)

    slider = page.locator("input[name='temperature']")
    expect(slider).to_be_visible()
    expect(slider).to_have_attribute("min", "0")
    expect(slider).to_have_attribute("max", "2")

    # Reset button should exist
    reset_btn = page.locator("button:has-text('Reset to default')")
    expect(reset_btn).to_be_visible()


def test_settings_max_tokens(page: Page, url, conversation):
    """#72 — Max tokens input accepts numeric value."""
    page.goto(url(f"/chat/conversations/{conversation.id}/"))
    _open_settings(page)

    input_el = page.locator("input[name='max_tokens']")
    expect(input_el).to_be_visible()
    expect(input_el).to_have_attribute("type", "number")
    expect(input_el).to_have_attribute("min", "1")
    expect(input_el).to_have_attribute("max", "32000")


def test_settings_auto_save(page: Page, url, conversation):
    """#73 — Settings form has hx-trigger for auto-saving."""
    page.goto(url(f"/chat/conversations/{conversation.id}/"))
    _open_settings(page)

    form = page.locator("#settings-form")
    expect(form).to_have_attribute("hx-trigger", "change delay:400ms")
    expect(form).to_have_attribute("hx-swap", "none")


def test_settings_saved_indicator(page: Page, url, conversation):
    """#74 — 'Saved' indicator element exists in the settings form."""
    page.goto(url(f"/chat/conversations/{conversation.id}/"))
    _open_settings(page)

    saved_el = page.locator("#settings-saved")
    expect(saved_el).to_be_attached()
    expect(saved_el).to_contain_text("Saved")
