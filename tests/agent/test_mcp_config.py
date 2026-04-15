"""Tests for Spec 029 — MCP File-Based Configuration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# load_servers
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadServers:
    def test_returns_empty_when_file_absent(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        from agent.mcp.config import load_servers
        assert load_servers() == {}

    def test_loads_sse_server(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        _write_config(tmp_path, {"mcpServers": {
            "fab-mcp": {"type": "sse", "url": "https://mcp.internal", "enabled": True}
        }})
        from agent.mcp.config import load_servers
        servers = load_servers()
        assert "fab-mcp" in servers
        assert servers["fab-mcp"].url == "https://mcp.internal"
        assert servers["fab-mcp"].type == "sse"

    def test_loads_stdio_server(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        _write_config(tmp_path, {"mcpServers": {
            "github": {"type": "stdio", "command": "npx",
                       "args": ["-y", "@mcp/github"], "enabled": True}
        }})
        from agent.mcp.config import load_servers
        assert load_servers()["github"].command == "npx"

    def test_malformed_json_returns_empty(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        (tmp_path / "mcp_servers.json").write_text("NOT JSON", encoding="utf-8")
        from agent.mcp.config import load_servers
        assert load_servers() == {}


# ══════════════════════════════════════════════════════════════════════════════
# save_servers / upsert_server / remove_server
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveServers:
    def test_round_trip(self, tmp_path, settings):
        """save → load produces identical data."""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        from agent.mcp.config import MCPServerConfig, save_servers, load_servers
        cfg = MCPServerConfig(name="test", type="sse", url="https://x.com", enabled=True)
        save_servers({"test": cfg})
        loaded = load_servers()
        assert loaded["test"].url == "https://x.com"

    def test_atomic_write_leaves_no_tmp_file(self, tmp_path, settings):
        """After save, no .tmp file should remain."""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        from agent.mcp.config import MCPServerConfig, save_servers
        cfg = MCPServerConfig(name="s", type="stdio", command="npx")
        save_servers({"s": cfg})
        assert not (tmp_path / "mcp_servers.json.tmp").exists()

    def test_upsert_adds_new_server(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        from agent.mcp.config import MCPServerConfig, upsert_server, load_servers
        upsert_server(MCPServerConfig(name="new", type="sse", url="https://new.com"))
        assert "new" in load_servers()

    def test_upsert_overwrites_existing(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        from agent.mcp.config import MCPServerConfig, upsert_server, load_servers
        upsert_server(MCPServerConfig(name="s", type="sse", url="https://old.com"))
        upsert_server(MCPServerConfig(name="s", type="sse", url="https://new.com"))
        assert load_servers()["s"].url == "https://new.com"

    def test_remove_existing_server(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        from agent.mcp.config import MCPServerConfig, upsert_server, remove_server, load_servers
        upsert_server(MCPServerConfig(name="del", type="sse", url="https://x.com"))
        assert remove_server("del") is True
        assert "del" not in load_servers()

    def test_remove_nonexistent_returns_false(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        from agent.mcp.config import remove_server
        assert remove_server("ghost") is False


# ══════════════════════════════════════════════════════════════════════════════
# MCPServerConfig — env / headers resolution
# ══════════════════════════════════════════════════════════════════════════════

class TestMCPServerConfigResolution:
    def test_resolved_env_substitutes_var(self, monkeypatch):
        from agent.mcp.config import MCPServerConfig
        monkeypatch.setenv("MY_TOKEN", "secret123")
        cfg = MCPServerConfig(name="s", type="stdio", env={"TOKEN": "${MY_TOKEN}"})
        assert cfg.resolved_env()["TOKEN"] == "secret123"

    def test_resolved_env_missing_var_returns_empty_string(self, monkeypatch):
        from agent.mcp.config import MCPServerConfig
        monkeypatch.delenv("MISSING_VAR", raising=False)
        cfg = MCPServerConfig(name="s", type="stdio", env={"KEY": "${MISSING_VAR}"})
        assert cfg.resolved_env()["KEY"] == ""

    def test_resolved_headers_substitutes_var(self, monkeypatch):
        from agent.mcp.config import MCPServerConfig
        monkeypatch.setenv("API_KEY", "tok-abc")
        cfg = MCPServerConfig(
            name="s", type="sse",
            headers={"Authorization": "Bearer ${API_KEY}"}
        )
        assert cfg.resolved_headers()["Authorization"] == "Bearer tok-abc"

    def test_literal_value_unchanged(self):
        from agent.mcp.config import MCPServerConfig
        cfg = MCPServerConfig(name="s", type="sse", headers={"X-Custom": "plain"})
        assert cfg.resolved_headers()["X-Custom"] == "plain"


# ══════════════════════════════════════════════════════════════════════════════
# MCPServerConfig — template-compatibility properties
# ══════════════════════════════════════════════════════════════════════════════

class TestMCPServerConfigProperties:
    def test_transport_alias(self):
        from agent.mcp.config import MCPServerConfig
        cfg = MCPServerConfig(name="s", type="sse")
        assert cfg.transport == "sse"

    def test_get_transport_display_sse(self):
        from agent.mcp.config import MCPServerConfig
        cfg = MCPServerConfig(name="s", type="sse")
        assert cfg.get_transport_display() == "SSE (remote HTTP)"

    def test_get_transport_display_stdio(self):
        from agent.mcp.config import MCPServerConfig
        cfg = MCPServerConfig(name="s", type="stdio")
        assert cfg.get_transport_display() == "stdio (local process)"

    def test_env_json_for_stdio(self):
        from agent.mcp.config import MCPServerConfig
        cfg = MCPServerConfig(name="s", type="stdio", env={"A": "1"})
        assert json.loads(cfg.env_json) == {"A": "1"}

    def test_env_json_for_sse(self):
        from agent.mcp.config import MCPServerConfig
        cfg = MCPServerConfig(name="s", type="sse", headers={"Authorization": "Bearer x"})
        assert json.loads(cfg.env_json) == {"Authorization": "Bearer x"}

    def test_last_error_is_always_empty(self):
        from agent.mcp.config import MCPServerConfig
        cfg = MCPServerConfig(name="s", type="sse")
        assert cfg.last_error == ""


# ══════════════════════════════════════════════════════════════════════════════
# MCP_SERVERS_CONFIG_PATH setting override
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigPathOverride:
    def test_custom_path_is_used(self, tmp_path, settings):
        custom = tmp_path / "custom" / "mcp.json"
        custom.parent.mkdir(parents=True)
        settings.MCP_SERVERS_CONFIG_PATH = str(custom)
        from agent.mcp.config import MCPServerConfig, upsert_server
        upsert_server(MCPServerConfig(name="x", type="sse", url="https://x.com"))
        assert custom.exists()
        data = json.loads(custom.read_text())
        assert "x" in data["mcpServers"]


# ══════════════════════════════════════════════════════════════════════════════
# sync_claude_code — reads file, not DB
# ══════════════════════════════════════════════════════════════════════════════

class TestSyncClaudeCodeMcp:
    def test_sync_reads_from_file_not_db(self, tmp_path, settings):
        """_sync_mcp reads mcp_servers.json, does not query DB."""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        _write_config(tmp_path, {"mcpServers": {
            "fab-mcp": {
                "type": "sse", "url": "https://mcp.internal",
                "headers": {}, "enabled": True,
                "auto_approve_tools": [], "auto_approve_resources": False,
                "always_include_resources": [], "session_dead_error_codes": [],
                "health_probe_tool": "", "description": "", "args": [],
                "command": "", "env": {}, "live_status": "",
            }
        }})
        from io import StringIO
        from unittest.mock import MagicMock, patch
        from agent.management.commands.sync_claude_code import Command

        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()
        cmd.style = MagicMock()

        home = tmp_path / "home"
        with patch.object(Path, "home", return_value=home):
            cmd._sync_mcp(home / ".claude", dry_run=True)

        output = cmd.stdout.getvalue()
        assert "fab-mcp" in output

    def test_disabled_server_excluded_from_sync(self, tmp_path, settings):
        """enabled=false servers are not written to ~/.claude.json."""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        settings.MCP_SERVERS_CONFIG_PATH = ""
        _write_config(tmp_path, {"mcpServers": {
            "disabled-mcp": {
                "type": "sse", "url": "https://x.com",
                "headers": {}, "enabled": False,
                "auto_approve_tools": [], "auto_approve_resources": False,
                "always_include_resources": [], "session_dead_error_codes": [],
                "health_probe_tool": "", "description": "", "args": [],
                "command": "", "env": {}, "live_status": "",
            }
        }})
        from io import StringIO
        from unittest.mock import MagicMock, patch
        from agent.management.commands.sync_claude_code import Command

        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()
        cmd.style = MagicMock()

        home = tmp_path / "home"
        with patch.object(Path, "home", return_value=home):
            cmd._sync_mcp(home / ".claude", dry_run=False)

        claude_json = home / ".claude.json"
        if claude_json.exists():
            data = json.loads(claude_json.read_text())
            for project in data.get("projects", {}).values():
                assert "disabled-mcp" not in project.get("mcpServers", {})
