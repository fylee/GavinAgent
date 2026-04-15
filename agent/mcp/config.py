"""
Spec 029 — MCP File-Based Configuration

MCPServerConfig dataclass + atomic file I/O functions.
Source of truth: <AGENT_WORKSPACE_DIR>/mcp_servers.json
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _resolve(value: str) -> str:
    """Substitute ${VAR_NAME} with the value from os.environ (empty string if missing)."""
    return _VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)


@dataclass
class MCPServerConfig:
    name: str
    type: str                                          # "sse" | "stdio"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""
    auto_approve_tools: list[str] = field(default_factory=list)
    auto_approve_resources: bool = False
    always_include_resources: list[str] = field(default_factory=list)
    session_dead_error_codes: list[int] = field(default_factory=list)
    health_probe_tool: str = ""

    # Runtime attribute — set by views, not persisted
    live_status: str = field(default="", repr=False)

    def resolved_env(self) -> dict[str, str]:
        """Resolve ${VAR_NAME} references in env from os.environ."""
        return {k: _resolve(v) for k, v in self.env.items()}

    def resolved_headers(self) -> dict[str, str]:
        """Resolve ${VAR_NAME} references in headers from os.environ."""
        return {k: _resolve(v) for k, v in self.headers.items()}

    # ── Template-compatibility properties ─────────────────────────────────────

    @property
    def transport(self) -> str:
        """Alias for 'type' — used in legacy templates."""
        return self.type

    @property
    def connection_status(self) -> str:
        """Runtime status — returns live_status (no DB persistence in Spec 029)."""
        return self.live_status

    @property
    def last_error(self) -> str:
        """No longer persisted — always empty."""
        return ""

    @property
    def env_json(self) -> str:
        """JSON of the relevant credentials dict for template display."""
        data = self.env if self.type == "stdio" else self.headers
        return json.dumps(data, indent=2) if data else ""

    def get_transport_display(self) -> str:
        """Human-readable transport label (mirrors Django model's get_FOO_display)."""
        return "stdio (local process)" if self.type == "stdio" else "SSE (remote HTTP)"


# ── File path ─────────────────────────────────────────────────────────────────


def _config_path() -> Path:
    custom = getattr(settings, "MCP_SERVERS_CONFIG_PATH", "")
    if custom:
        return Path(custom)
    return Path(settings.AGENT_WORKSPACE_DIR) / "mcp_servers.json"


# ── CRUD functions ────────────────────────────────────────────────────────────

_EXCLUDED_FIELDS = {"name", "live_status"}


def load_servers() -> dict[str, MCPServerConfig]:
    """
    Load all MCP server configs from mcp_servers.json.
    Returns an empty dict if the file does not exist or is malformed.
    """
    path = _config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("mcpServers", {})
        return {
            name: MCPServerConfig(name=name, **cfg)
            for name, cfg in raw.items()
        }
    except Exception as exc:
        logger.warning("Failed to load mcp_servers.json: %s", exc)
        return {}


def save_servers(servers: dict[str, MCPServerConfig]) -> None:
    """Atomically write all servers to mcp_servers.json (.tmp + rename)."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        name: {k: v for k, v in asdict(cfg).items() if k not in _EXCLUDED_FIELDS}
        for name, cfg in servers.items()
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"mcpServers": raw}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def get_server(name: str) -> MCPServerConfig | None:
    return load_servers().get(name)


def upsert_server(cfg: MCPServerConfig) -> None:
    servers = load_servers()
    servers[cfg.name] = cfg
    save_servers(servers)


def remove_server(name: str) -> bool:
    servers = load_servers()
    if name not in servers:
        return False
    del servers[name]
    save_servers(servers)
    return True
