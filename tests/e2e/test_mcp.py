"""MCP server page e2e tests — tests 55-62."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.factories import MCPServerFactory

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture()
def mcp_server(db):
    """Create an enabled MCP server."""
    return MCPServerFactory(
        name="e2e-test-server",
        transport="stdio",
        command="echo hello",
        enabled=True,
        connection_status="connected",
    )


@pytest.fixture()
def mcp_server_error(db):
    """Create an MCP server with error status."""
    return MCPServerFactory(
        name="e2e-error-server",
        transport="stdio",
        command="false",
        enabled=True,
        connection_status="error",
        last_error="Connection refused: ECONNREFUSED",
    )


def test_mcp_page_loads(page: Page, url, mcp_server):
    """#55 — /agent/mcp/ renders server list + warning banner."""
    page.goto(url("/agent/mcp/"))
    expect(page.locator("h1:text('MCP Servers')")).to_be_visible()
    # Warning banner about per-process connections
    expect(page.locator("text=per-process")).to_be_visible()
    # Server should be listed
    expect(page.locator("text=e2e-test-server")).to_be_visible()


def test_mcp_add_form_opens(page: Page, url):
    """#56 — '+ Add server' button loads HTMX form into #add-form-slot."""
    page.goto(url("/agent/mcp/"))
    add_btn = page.locator("button:text('+ Add server')")
    expect(add_btn).to_be_visible()
    add_btn.click()
    # Wait for the form to load via HTMX
    expect(page.locator("#add-form-slot")).not_to_be_empty(timeout=3000)


def test_mcp_toggle_server(page: Page, url, mcp_server):
    """#57 — Enable/Disable button swaps server card via HTMX."""
    page.goto(url("/agent/mcp/"))
    toggle_btn = page.locator(
        f"[hx-post*='{mcp_server.id}/toggle']"
    ).first
    expect(toggle_btn).to_be_visible()
    expect(toggle_btn).to_contain_text("Enabled")


def test_mcp_refresh_server(page: Page, url, mcp_server):
    """#58 — Refresh button exists and is clickable."""
    page.goto(url("/agent/mcp/"))
    refresh_btn = page.locator(
        f"[hx-post*='{mcp_server.id}/refresh']"
    ).first
    expect(refresh_btn).to_be_visible()
    expect(refresh_btn).to_contain_text("↺")


def test_mcp_delete_server(page: Page, url, mcp_server):
    """#59 — Delete button has hx-confirm for safety."""
    page.goto(url("/agent/mcp/"))
    delete_btn = page.locator(
        f"[hx-post*='{mcp_server.id}/delete']"
    ).first
    expect(delete_btn).to_be_visible()
    # Verify hx-confirm attribute is present (contains the server name)
    attr = delete_btn.get_attribute("hx-confirm")
    assert attr is not None and "Delete MCP server" in attr


def test_mcp_edit_server(page: Page, url, mcp_server):
    """#60 — Edit button loads detail form into #add-form-slot."""
    page.goto(url("/agent/mcp/"))
    edit_btn = page.locator(
        f"[hx-get*='{mcp_server.id}']"
    ).first
    expect(edit_btn).to_be_visible()
    expect(edit_btn).to_contain_text("Edit")


def test_mcp_connection_status_dot(page: Page, url, mcp_server):
    """#61 — Status dot color reflects connected state."""
    page.goto(url("/agent/mcp/"))
    server_card = page.locator(f"#mcp-server-{mcp_server.id}")
    expect(server_card).to_be_visible()
    # Connected = green dot
    expect(server_card.locator(".bg-green-400").first).to_be_visible()


def test_mcp_error_display(page: Page, url, mcp_server_error):
    """#62 — Error state shows last_error message."""
    page.goto(url("/agent/mcp/"))
    server_card = page.locator(f"#mcp-server-{mcp_server_error.id}")
    expect(server_card).to_be_visible()
    # Error = red dot
    expect(server_card.locator(".bg-red-400").first).to_be_visible()
    # Error message should be visible
    expect(server_card).to_contain_text("Connection refused")
