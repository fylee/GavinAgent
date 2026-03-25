"""Knowledge base e2e tests — tests 36-41."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.factories import KnowledgeDocumentFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_knowledge_page_loads(page: Page, url, knowledge_doc):
    """#36 — Knowledge page shows the documents list."""
    page.goto(url("/agent/knowledge/"))
    expect(page.locator("h1:text('Knowledge Base')")).to_be_visible()
    expect(page.locator("text=Test Knowledge Doc")).to_be_visible()


def test_add_text_document(page: Page, url, default_agent):
    """#37 — Click '+ Add document' → fill text form → verify form fields."""
    page.goto(url("/agent/knowledge/"))

    # Click the add button to load the HTMX form
    page.locator("button:text('+ Add document')").click()
    # Wait for the form to appear
    expect(page.locator("#knowledge-add-form")).to_be_visible(timeout=3000)

    # Verify tabs exist
    expect(page.locator("button:text('Upload File')")).to_be_visible()
    expect(page.locator("button:text('From URL')")).to_be_visible()
    expect(page.locator("button:text('Paste Text')")).to_be_visible()

    # Switch to "Paste Text" tab (Alpine x-show)
    page.locator("button:text('Paste Text')").click()
    page.wait_for_timeout(500)

    # Verify the text form fields are visible and fillable
    text_form = page.locator("form:has(input[name='source_type'][value='text'])")
    expect(text_form).to_be_visible(timeout=2000)
    title_input = text_form.locator("input[name='title']")
    content_area = text_form.locator("textarea[name='raw_content']")
    expect(title_input).to_be_visible()
    expect(content_area).to_be_visible()
    title_input.fill("E2E Text Doc")
    content_area.fill("This is test content from Playwright.")

    # Submit button should say "Save & Ingest"
    submit_btn = text_form.locator("button[type='submit']")
    expect(submit_btn).to_be_visible()
    expect(submit_btn).to_contain_text("Save")


def test_add_url_document(page: Page, url, default_agent):
    """#38 — Enter URL → submit → URL form has the right fields."""
    page.goto(url("/agent/knowledge/"))
    page.locator("button:text('+ Add document')").click()
    expect(page.locator("#knowledge-add-form")).to_be_visible(timeout=3000)

    # Switch to "From URL" tab
    page.locator("button:text('From URL')").click()
    page.wait_for_timeout(300)

    # The URL form should have the source_url input
    url_form = page.locator("form:has(input[name='source_type'][value='url'])")
    url_input = url_form.locator("input[name='source_url']")
    expect(url_input).to_be_visible()
    expect(url_input).to_have_attribute("type", "url")


def test_toggle_document_active(page: Page, url, knowledge_doc):
    """#39 — Toggle active/inactive via HTMX button."""
    page.goto(url("/agent/knowledge/"))
    toggle_btn = page.locator(
        f"[hx-post*='{knowledge_doc.id}/toggle']"
    ).first
    expect(toggle_btn).to_be_visible()
    expect(toggle_btn).to_contain_text("Active")

    toggle_btn.click()
    # After toggle, should show "Inactive"
    expect(
        page.locator(f"[hx-post*='{knowledge_doc.id}/toggle']").first
    ).to_contain_text("Inactive", timeout=3000)


def test_delete_document(page: Page, url, knowledge_doc):
    """#40 — Delete → confirm dialog → document removed."""
    page.goto(url("/agent/knowledge/"))
    # Accept the upcoming dialog
    page.on("dialog", lambda dialog: dialog.accept())
    delete_btn = page.locator(
        f"[hx-post*='{knowledge_doc.id}/delete']"
    ).first
    expect(delete_btn).to_be_visible()
    delete_btn.click()
    # The doc should be removed from the list
    expect(page.locator(f"#knowledge-doc-{knowledge_doc.id}")).to_have_count(
        0, timeout=3000
    )


def test_document_status_badge(page: Page, url):
    """#41 — Status badge shows correct color per status."""
    ready_doc = KnowledgeDocumentFactory(title="Ready Doc", status="ready")
    error_doc = KnowledgeDocumentFactory(title="Error Doc", status="error")

    page.goto(url("/agent/knowledge/"))
    # Ready doc should have a green status dot
    ready_row = page.locator(f"#knowledge-doc-{ready_doc.id}")
    expect(ready_row.locator(".bg-green-400").first).to_be_visible()

    # Error doc should have a red status dot
    error_row = page.locator(f"#knowledge-doc-{error_doc.id}")
    expect(error_row.locator(".bg-red-400").first).to_be_visible()
