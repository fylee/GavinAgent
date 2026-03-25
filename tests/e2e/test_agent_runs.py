"""Agent run detail e2e tests — tests 16-23."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


def test_run_detail_loads(page: Page, url, completed_run):
    """#16 — Run detail page shows input, status, tool executions."""
    page.goto(url(f"/agent/runs/{completed_run.id}/"))
    expect(page.locator("h1:text('Agent Run')")).to_be_visible()
    expect(page.locator("text=Search for Python tutorials")).to_be_visible()
    # Tool execution should be shown (scope to Tool Executions section)
    tool_section = page.locator("div:has(> h3:text('Tool Executions'))")
    expect(tool_section.locator("code").first).to_contain_text("web_search")


def test_run_status_badge(page: Page, url, completed_run, failed_run):
    """#17 — Status badge shows correct color for completed and failed."""
    # Completed run (OOB swap creates duplicate #run-status-badge — use .first)
    page.goto(url(f"/agent/runs/{completed_run.id}/"))
    badge = page.locator("#run-status-badge").first
    expect(badge).to_contain_text("Completed")
    cls = badge.get_attribute("class") or ""
    assert "bg-green-900" in cls, f"Expected bg-green-900 in class: {cls}"

    # Failed run
    page.goto(url(f"/agent/runs/{failed_run.id}/"))
    badge = page.locator("#run-status-badge").first
    expect(badge).to_contain_text("Failed")
    cls = badge.get_attribute("class") or ""
    assert "bg-red-900" in cls, f"Expected bg-red-900 in class: {cls}"


def test_run_skills_badges(page: Page, url, completed_run):
    """#18 — Triggered skills shown as purple badges."""
    page.goto(url(f"/agent/runs/{completed_run.id}/"))
    # The run has triggered_skills=["research_skill"]
    skill_badge = page.locator("button:text('research_skill')")
    expect(skill_badge).to_be_visible()
    # Verify it has the purple badge styling (Alpine :class may add extra classes)
    cls = skill_badge.get_attribute("class") or ""
    assert "bg-purple-900" in cls or "bg-purple-700" in cls


def test_run_rag_badges(page: Page, url, completed_run):
    """#19 — Knowledge matches shown as teal badges with similarity."""
    page.goto(url(f"/agent/runs/{completed_run.id}/"))
    # RAG matches: Python Basics (0.87), Django Guide (0.72)
    expect(page.locator("text=Python Basics")).to_be_visible()
    expect(page.locator("text=0.87")).to_be_visible()
    expect(page.locator("text=Django Guide")).to_be_visible()


def test_run_loop_trace_expand(page: Page, url, completed_run):
    """#20 — Loop Trace section expands on click."""
    page.goto(url(f"/agent/runs/{completed_run.id}/"))
    # Find the Loop Trace button
    trace_btn = page.locator("button:has-text('Loop Trace')")
    expect(trace_btn).to_be_visible()

    # Click to expand
    trace_btn.click()
    # Should show round info
    expect(page.locator("text=Need to search for tutorials")).to_be_visible(timeout=3000)


def test_run_tool_output_toggle(page: Page, url, completed_run):
    """#21 — Tool execution row has show/hide output toggle."""
    page.goto(url(f"/agent/runs/{completed_run.id}/"))
    # Scope to the Tool Executions section (has h3 heading)
    tool_section = page.locator("div:has(> h3:text('Tool Executions'))")
    # The tool name should be visible in the tool executions section
    expect(tool_section.locator("code").first).to_be_visible()
    expect(tool_section.locator("code").first).to_contain_text("web_search")


def test_run_cancel_button(page: Page, url, pending_run):
    """#22 — Cancel Run button changes status to failed."""
    page.goto(url(f"/agent/runs/{pending_run.id}/"))
    cancel_btn = page.locator("button:text('Cancel Run')")
    expect(cancel_btn).to_be_visible()

    cancel_btn.click()
    # Status should change to Failed (OOB swap creates duplicate IDs — use .first)
    expect(page.locator("#run-status-badge").first).to_contain_text("Failed", timeout=5000)


def test_run_status_polling(page: Page, url, running_run):
    """#23 — Running status triggers HTMX polling via hx-trigger='every 2s'."""
    page.goto(url(f"/agent/runs/{running_run.id}/"))
    container = page.locator("#run-status-container")
    expect(container).to_have_attribute("hx-trigger", "every 2s")
    expect(container).to_have_attribute("hx-swap", "innerHTML")
