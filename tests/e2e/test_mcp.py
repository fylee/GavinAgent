"""MCP server page e2e tests — tests 55-62."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture()
def mcp_server(settings, _workspace):
    """Create an enabled MCP server via file-based config."""
    from pathlib import Path
    from agent.mcp.config import MCPServerConfig, upsert_server
    cfg = MCPServerConfig(
        name="e2e-test-server",
        type="stdio",
        command="echo hello",
        enabled=True,
    )
    upsert_server(cfg)
    return cfg


@pytest.fixture()
def mcp_server_error(settings, _workspace):
    """Create an MCP server that will show a disconnected/error state."""
    from agent.mcp.config import MCPServerConfig, upsert_server
    cfg = MCPServerConfig(
        name="e2e-error-server",
        type="stdio",
        command="false",
        enabled=True,
    )
    upsert_server(cfg)
    return cfg


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
        f"[hx-post*='{mcp_server.name}/toggle']"
    ).first
    expect(toggle_btn).to_be_visible()
    expect(toggle_btn).to_contain_text("Enabled")


def test_mcp_refresh_server(page: Page, url, mcp_server):
    """#58 — Refresh button exists and is clickable."""
    page.goto(url("/agent/mcp/"))
    refresh_btn = page.locator(
        f"[hx-post*='{mcp_server.name}/refresh']"
    ).first
    expect(refresh_btn).to_be_visible()
    expect(refresh_btn).to_contain_text("↺")


def test_mcp_delete_server(page: Page, url, mcp_server):
    """#59 — Delete button has hx-confirm for safety."""
    page.goto(url("/agent/mcp/"))
    delete_btn = page.locator(
        f"[hx-post*='{mcp_server.name}/delete']"
    ).first
    expect(delete_btn).to_be_visible()
    # Verify hx-confirm attribute is present (contains the server name)
    attr = delete_btn.get_attribute("hx-confirm")
    assert attr is not None and "Delete MCP server" in attr


def test_mcp_edit_server(page: Page, url, mcp_server):
    """#60 — Edit button loads detail form into #add-form-slot."""
    page.goto(url("/agent/mcp/"))
    edit_btn = page.locator(
        f"[hx-get*='{mcp_server.name}']"
    ).first
    expect(edit_btn).to_be_visible()
    expect(edit_btn).to_contain_text("Edit")


def test_mcp_connection_status_dot(page: Page, url, mcp_server):
    """#61 — Status dot is present for the server card."""
    page.goto(url("/agent/mcp/"))
    server_card = page.locator(f"#mcp-server-{mcp_server.name}")
    expect(server_card).to_be_visible()


def test_mcp_error_display(page: Page, url, mcp_server_error):
    """#62 — Server card is visible for an error-state server."""
    page.goto(url("/agent/mcp/"))
    server_card = page.locator(f"#mcp-server-{mcp_server_error.name}")
    expect(server_card).to_be_visible()
