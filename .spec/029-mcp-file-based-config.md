# 029 — MCP File-Based Configuration

## Goal

將 MCP server 設定的 source of truth 從 PostgreSQL 資料庫改為
`agent/workspace/mcp_servers.json` 檔案，對齊 MCP 生態標準（Claude Code、
Cursor 等客戶端使用 JSON 設定檔），同時保留現有 Web UI 的檢視與編輯功能。
移除 `MCPServer` Django model 及相關 migration。

---

## Background

### 現狀與問題

GavinAgent 目前把 MCP server 設定存在 PostgreSQL 的 `agent_mcpserver` 資料表，
造成三個問題：

**1. 兩個 source of truth 會漂移**

```
DB (MCPServer model)
  │
  └── sync_claude_code（手動執行）── ~/.claude.json
```

新增 server 後若忘記跑 `sync_claude_code`，Claude Code CLI 看不到該 server。
直接改 `~/.claude.json` 時，GavinAgent 完全不知情。沒有機制提示漂移。

**2. 不符合 MCP 生態標準**

Claude Code、Cursor、Windsurf 等 MCP 客戶端都以 JSON 設定檔為 source of truth。
DB 方案是 GavinAgent 的孤立設計，未來整合其他工具摩擦大。

**3. 可攜性差**

換機器或分享設定需要 DB export/import。JSON 檔案可以直接複製或版控。

### Hermes-agent 的參考做法

Hermes-agent 使用 `~/.hermes/config.yaml`，CLI 管理（`hermes mcp add/remove/list`），
敏感資訊以 `${VAR_NAME}` 參照存放，實際值在 `.env`。
GavinAgent 採用同樣的理念，但保留 Web UI 管理介面。

---

## Proposed Solution

### 1. 設定檔格式

檔案路徑：`<AGENT_WORKSPACE_DIR>/mcp_servers.json`
（預設為 `agent/workspace/mcp_servers.json`）

格式對齊 Claude Code 的 `mcpServers` schema，加上 GavinAgent 專屬欄位：

```json
{
  "mcpServers": {
    "fab-mcp": {
      "type": "sse",
      "url": "https://mcp.internal/fab",
      "headers": {
        "Authorization": "Bearer ${FAB_MCP_API_KEY}"
      },
      "enabled": true,
      "description": "Winbond FAB MCP server",
      "auto_approve_tools": ["fab-mcp/get_hold_lot_list"],
      "auto_approve_resources": false,
      "always_include_resources": [],
      "session_dead_error_codes": [-32602],
      "health_probe_tool": ""
    },
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      },
      "enabled": true,
      "description": "",
      "auto_approve_tools": [],
      "auto_approve_resources": false,
      "always_include_resources": [],
      "session_dead_error_codes": [],
      "health_probe_tool": ""
    }
  }
}
```

**欄位說明：**

| 欄位 | 標準 / 擴充 | 說明 |
|---|---|---|
| `type` | Claude Code 標準 | `"sse"` 或 `"stdio"` |
| `url` | Claude Code 標準 | SSE 端點 |
| `headers` | Claude Code 標準 | SSE HTTP headers（支援 `${VAR}` 參照） |
| `command` | Claude Code 標準 | stdio 啟動指令 |
| `args` | Claude Code 標準 | stdio 啟動參數陣列 |
| `env` | Claude Code 標準 | stdio 環境變數（支援 `${VAR}` 參照） |
| `enabled` | GavinAgent 擴充 | 是否啟用 |
| `description` | GavinAgent 擴充 | 說明文字 |
| `auto_approve_tools` | GavinAgent 擴充 | 免審批工具清單 |
| `auto_approve_resources` | GavinAgent 擴充 | 是否自動核准資源讀取 |
| `always_include_resources` | GavinAgent 擴充 | 每次 run 注入 context 的資源 URI |
| `session_dead_error_codes` | GavinAgent 擴充 | 表示 session 過期的 JSON-RPC 錯誤碼 |
| `health_probe_tool` | GavinAgent 擴充 | 健康檢查用的工具名稱 |

> **注意**：現有 DB model 的 `env` 欄位同時儲存 stdio env 和 SSE headers，
> 新設計明確拆分為 `env`（stdio）和 `headers`（SSE），語意更清晰。

### 2. `MCPServerConfig` dataclass

新增 `agent/mcp/config.py`，取代 `MCPServer` model：

```python
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from django.conf import settings


@dataclass
class MCPServerConfig:
    name: str
    type: str                              # "sse" | "stdio"
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

    def resolved_env(self) -> dict[str, str]:
        """Resolve ${VAR_NAME} references in env / headers from os.environ."""
        return {k: _resolve(v) for k, v in self.env.items()}

    def resolved_headers(self) -> dict[str, str]:
        return {k: _resolve(v) for k, v in self.headers.items()}


_VAR_RE = re.compile(r"\$\{(\w+)\}")

def _resolve(value: str) -> str:
    return _VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
```

### 3. 檔案 I/O 函式

```python
def _config_path() -> Path:
    return Path(settings.AGENT_WORKSPACE_DIR) / "mcp_servers.json"


def load_servers() -> dict[str, MCPServerConfig]:
    """
    Load all MCP server configs from mcp_servers.json.
    Returns empty dict if file does not exist.
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
    """Atomically write all servers to mcp_servers.json."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        name: {k: v for k, v in asdict(cfg).items() if k != "name"}
        for name, cfg in servers.items()
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"mcpServers": raw}, indent=2, ensure_ascii=False), encoding="utf-8")
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
```

### 4. `MCPConnectionPool` 改寫

所有 DB 查詢改為讀檔案。主要改動點：

**`_start_all_async()`**

```python
async def _start_all_async(self) -> None:
    from agent.mcp.config import load_servers
    servers = load_servers()
    tasks = [
        self._start_server_async(cfg)
        for cfg in servers.values()
        if cfg.enabled
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

**`_start_server_async(cfg: MCPServerConfig)`**

接受 `MCPServerConfig` 而非 `MCPServer` model instance：

```python
if cfg.type == "stdio":
    coro = run_stdio_connection(
        cfg.name, cfg.command, cfg.resolved_env(),
        conn, on_ready, on_error, args=cfg.args,
    )
else:
    coro = run_sse_connection(
        cfg.name, cfg.url, cfg.resolved_headers(),
        conn, on_ready, on_error,
    )
```

**`_retry_loop()`**

```python
from agent.mcp.config import load_servers
servers = load_servers()
for cfg in servers.values():
    if not cfg.enabled:
        continue
    # ...health check and reconnect logic...
```

**`_reconnect_server_async()`**

```python
from agent.mcp.config import get_server
cfg = get_server(server_name)
if cfg:
    await self._start_server_async(cfg)
```

**`fetch_always_include_resources()`**

```python
from agent.mcp.config import load_servers
for cfg in load_servers().values():
    if cfg.enabled and cfg.always_include_resources:
        for uri in cfg.always_include_resources:
            ...
```

**`_update_db_status()`**

移除。Connection status 是 runtime 狀態，由 `self._connections` 管理，
不需持久化。Pool 的 `get_status(name)` 維持不變。

### 5. Views 改寫

所有 `MCPServer.objects.*` 改為 `agent.mcp.config` 的函式：

| 現在 | 改後 |
|---|---|
| `MCPServer.objects.all()` | `load_servers().values()` |
| `MCPServer.objects.filter(enabled=True)` | `[s for s in load_servers().values() if s.enabled]` |
| `get_object_or_404(MCPServer, pk=pk)` | `get_server(name)` or 404 |
| `server.save()` | `upsert_server(cfg)` |
| `MCPServer.objects.delete(pk=pk)` | `remove_server(name)` |

> **注意**：`MCPServerDetailView` 現在以 `pk`（UUID）查詢，改為以 `name` 查詢。
> URL pattern 需從 `/agent/mcp/<uuid>/` 改為 `/agent/mcp/<name>/`。

**`MCPServerToggleView`**

```python
from agent.mcp.config import get_server, upsert_server
cfg = get_server(name)
if not cfg:
    return HttpResponse(status=404)
cfg.enabled = not cfg.enabled
upsert_server(cfg)
pool = MCPConnectionPool.get()
if cfg.enabled:
    pool.start_server(cfg)
else:
    pool.stop_server(name)
```

**`MCPServerAddView` POST** 會在存檔後觸發 `pool.start_server(cfg)`，
不再需要 `server.full_clean()`（改為直接驗證 dataclass 欄位）。

### 6. `sync_claude_code._sync_mcp()` 改寫

```python
def _sync_mcp(self, claude_dir: Path, dry_run: bool) -> None:
    from agent.mcp.config import load_servers

    servers = {
        name: cfg
        for name, cfg in load_servers().items()
        if cfg.enabled
    }
    if not servers:
        self.stdout.write("  MCP: no active servers found — skipping")
        return

    mcp_entries: dict = {}
    for name, cfg in servers.items():
        if cfg.type == "sse":
            entry = {"type": "sse", "url": cfg.url}
            if cfg.headers:
                entry["headers"] = cfg.headers
        else:
            parts = cfg.command.strip().split()
            entry = {
                "type": "stdio",
                "command": parts[0] if parts else cfg.command,
            }
            if cfg.args:
                entry["args"] = cfg.args
            elif len(parts) > 1:
                entry["args"] = parts[1:]
            if cfg.env:
                entry["env"] = cfg.env
        mcp_entries[name] = entry
        self.stdout.write(f"  MCP [{cfg.type}]: {name}")

    # ... 後續寫入 ~/.claude.json 的邏輯不變 ...
```

### 7. 移除 `MCPServer` DB model

**步驟：**

1. 建立管理指令 `export_mcp_to_file`，把現有 DB 記錄匯出至
   `mcp_servers.json`（含 migration 前執行說明）
2. 移除 `agent/models.py` 中的 `MCPServer` model
3. 建立 migration 刪除 `agent_mcpserver` 資料表
4. 移除所有 `from agent.models import MCPServer` import

**`export_mcp_to_file` 管理指令：**

```bash
uv run python manage.py export_mcp_to_file
# → 讀取 DB 中所有 MCPServer，寫入 agent/workspace/mcp_servers.json
# → 印出每個匯出的 server 名稱
# → 提示：匯出完成後請執行 migrate 移除 DB 資料表
```

> **注意**：原本 DB model 的 `env` 欄位同時放 stdio env 和 SSE headers。
> 匯出時依 `transport` 自動拆分：`stdio` → `env`，`sse` → `headers`。

### 8. 設定新增

```python
# config/settings/base.py

# Spec 029: MCP server 設定檔路徑（預設在 AGENT_WORKSPACE_DIR）
# 若需要自訂位置可覆寫，如 /etc/gavinagent/mcp_servers.json
MCP_SERVERS_CONFIG_PATH: str = config(
    "MCP_SERVERS_CONFIG_PATH", default=""
)
# 空字串表示使用預設值 AGENT_WORKSPACE_DIR/mcp_servers.json
```

---

## Migration Path（執行順序）

```bash
# 1. 先把現有 DB 記錄匯出到檔案（上線前）
uv run python manage.py export_mcp_to_file

# 2. 確認 mcp_servers.json 內容正確

# 3. 部署新版程式碼

# 4. 執行 migration 移除 DB 資料表
uv run python manage.py migrate

# 5. 確認 UI 正常，連線狀態正確
```

---

## Out of Scope

- **自動將 `${VAR}` 值存入 `.env`**：UI 儲存時不自動寫入 `.env`，使用者自行管理
- **檔案加密**：`mcp_servers.json` 明文儲存；敏感值應使用 `${VAR}` 參照
- **Git 版控整合**：不自動 commit 設定檔變更
- **多環境設定檔**（dev/prod 分離）：透過 `MCP_SERVERS_CONFIG_PATH` 手動指定
- **Import from `~/.claude.json`**：不自動匯入 Claude Code 的設定
- **UI discovery-first 流程**（Hermes 風格，新增時先連線探測工具）：留待後續

---

## Acceptance Criteria

**設定檔**
- [x] `agent/workspace/mcp_servers.json` 為 source of truth；不存在時 pool 啟動不崩潰
- [x] 新增、編輯、刪除、toggle 透過 UI 操作後，`mcp_servers.json` 立即更新
- [x] `${VAR_NAME}` 參照在連線時從 `os.environ` 解析（支援 env 和 headers）
- [x] 多次讀寫不損壞 JSON 格式（atomic write via `.tmp` + rename）

**Pool**
- [x] Django/Celery 啟動時 `pool.start_all()` 讀檔案，不查 DB
- [x] retry loop 讀檔案，不查 DB
- [x] `pool.start_server(cfg)` 接受 `MCPServerConfig`，不接受 `MCPServer` model
- [x] `_update_db_status()` 移除，不影響 `get_status()` 的正確性

**Views**
- [x] MCP 列表頁正常顯示所有 server 與即時連線狀態
- [x] 新增 server → 存入檔案 → pool 立即連線
- [x] 編輯 server → 存入檔案 → pool refresh
- [x] Toggle → 存入檔案 → pool start/stop
- [x] 刪除 → 從檔案移除 → pool stop
- [x] URL pattern 改用 `name` 而非 UUID（`/agent/mcp/<name>/`）

**sync_claude_code**
- [x] `sync_claude_code --mcp-only` 讀 `mcp_servers.json`，寫 `~/.claude.json`
- [x] `--dry-run` 顯示會寫入的 server 清單

**Migration**
- [x] `export_mcp_to_file` 正確把 DB 記錄轉為 `mcp_servers.json`（含 env/headers 拆分）
- [x] Migration 後 `agent_mcpserver` 資料表不存在（migration 0015 已建立）
- [x] 既有的 `LLMUsage`、`ToolExecution` model 不受影響（無 FK 到 MCPServer）

---

## Open Questions

1. **`mcp_servers.json` 是否應列入 `.gitignore`？**
   檔案可能含有 `${VAR}` 參照（安全），但也可能含明文 token（不安全）。
   建議：預設加入 `.gitignore`，但允許使用者自行決定是否版控。

2. **URL pattern 從 UUID 改為 name**：`MCPServerDetailView` 的路由需要更新。
   若有外部系統（bookmarks、Claude Code hooks）記錄了 UUID 格式的 URL，
   改名後會 404。是否需要提供重導向？

3. **並發寫入的安全性**：`upsert_server()` 是 read-modify-write 操作，
   若多個請求同時寫入（例如同時 toggle 兩個 server），可能有 race condition。
   是否需要 file lock（`fcntl` / `msvcrt`）？

---

## Test Cases

測試檔：`tests/agent/test_mcp_config.py`

```python
"""Tests for Spec 029 — MCP File-Based Configuration."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
        from agent.mcp.config import load_servers
        assert load_servers() == {}

    def test_loads_sse_server(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
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
        _write_config(tmp_path, {"mcpServers": {
            "github": {"type": "stdio", "command": "npx",
                       "args": ["-y", "@mcp/github"], "enabled": True}
        }})
        from agent.mcp.config import load_servers
        assert load_servers()["github"].command == "npx"

    def test_malformed_json_returns_empty(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        (tmp_path / "mcp_servers.json").write_text("NOT JSON", encoding="utf-8")
        from agent.mcp.config import load_servers
        assert load_servers() == {}


# ══════════════════════════════════════════════════════════════════════════════
# save_servers / upsert_server / remove_server
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveServers:
    def test_round_trip(self, tmp_path, settings):
        """save → load 後資料一致"""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        from agent.mcp.config import MCPServerConfig, save_servers, load_servers
        cfg = MCPServerConfig(name="test", type="sse", url="https://x.com", enabled=True)
        save_servers({"test": cfg})
        loaded = load_servers()
        assert loaded["test"].url == "https://x.com"

    def test_atomic_write_uses_tmp_file(self, tmp_path, settings):
        """中途失敗不應留下損壞的 json（透過 .tmp rename 保證）"""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        from agent.mcp.config import MCPServerConfig, save_servers
        cfg = MCPServerConfig(name="s", type="stdio", command="npx")
        save_servers({"s": cfg})
        # .tmp 檔案不應殘留
        assert not (tmp_path / "mcp_servers.json.tmp").exists()

    def test_upsert_adds_new_server(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        from agent.mcp.config import MCPServerConfig, upsert_server, load_servers
        upsert_server(MCPServerConfig(name="new", type="sse", url="https://new.com"))
        assert "new" in load_servers()

    def test_upsert_overwrites_existing(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        from agent.mcp.config import MCPServerConfig, upsert_server, load_servers
        upsert_server(MCPServerConfig(name="s", type="sse", url="https://old.com"))
        upsert_server(MCPServerConfig(name="s", type="sse", url="https://new.com"))
        assert load_servers()["s"].url == "https://new.com"

    def test_remove_existing_server(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        from agent.mcp.config import MCPServerConfig, upsert_server, remove_server, load_servers
        upsert_server(MCPServerConfig(name="del", type="sse", url="https://x.com"))
        assert remove_server("del") is True
        assert "del" not in load_servers()

    def test_remove_nonexistent_returns_false(self, tmp_path, settings):
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
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
# export_mcp_to_file 管理指令
# ══════════════════════════════════════════════════════════════════════════════

class TestExportMcpToFile:
    @pytest.mark.django_db
    def test_exports_sse_server_to_file(self, tmp_path, settings):
        """DB 中的 SSE server 正確匯出至 mcp_servers.json"""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        # 建立舊 MCPServer DB record（匯出前的暫時狀態）
        from agent.models import MCPServer
        MCPServer.objects.create(
            name="fab-mcp",
            transport="sse",
            url="https://mcp.internal",
            env={"Authorization": "Bearer token123"},
            enabled=True,
        )
        from io import StringIO
        from agent.management.commands.export_mcp_to_file import Command
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.handle()
        servers = json.loads((tmp_path / "mcp_servers.json").read_text())
        fab = servers["mcpServers"]["fab-mcp"]
        assert fab["type"] == "sse"
        assert fab["url"] == "https://mcp.internal"
        # SSE env → headers
        assert "Authorization" in fab.get("headers", {})

    @pytest.mark.django_db
    def test_exports_stdio_server_to_file(self, tmp_path, settings):
        """DB 中的 stdio server env 保留在 env 欄位"""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        from agent.models import MCPServer
        MCPServer.objects.create(
            name="github",
            transport="stdio",
            command="npx -y @mcp/github",
            env={"GITHUB_TOKEN": "ghp_xxx"},
            enabled=True,
        )
        from io import StringIO
        from agent.management.commands.export_mcp_to_file import Command
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.handle()
        servers = json.loads((tmp_path / "mcp_servers.json").read_text())
        gh = servers["mcpServers"]["github"]
        assert gh["type"] == "stdio"
        assert "GITHUB_TOKEN" in gh.get("env", {})


# ══════════════════════════════════════════════════════════════════════════════
# sync_claude_code — 讀檔案而非 DB
# ══════════════════════════════════════════════════════════════════════════════

class TestSyncClaudeCodeMcp:
    def test_sync_reads_from_file_not_db(self, tmp_path, settings):
        """sync_mcp 讀 mcp_servers.json，不查 DB"""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        _write_config(tmp_path, {"mcpServers": {
            "fab-mcp": {
                "type": "sse", "url": "https://mcp.internal",
                "headers": {}, "enabled": True,
                "auto_approve_tools": [], "auto_approve_resources": False,
                "always_include_resources": [], "session_dead_error_codes": [],
                "health_probe_tool": "", "description": ""
            }
        }})
        from io import StringIO
        from unittest.mock import MagicMock, patch
        from pathlib import Path
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
        """enabled=false のserver は ~/.claude.json に書き込まない"""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        _write_config(tmp_path, {"mcpServers": {
            "disabled-mcp": {
                "type": "sse", "url": "https://x.com",
                "headers": {}, "enabled": False,
                "auto_approve_tools": [], "auto_approve_resources": False,
                "always_include_resources": [], "session_dead_error_codes": [],
                "health_probe_tool": "", "description": ""
            }
        }})
        from io import StringIO
        from unittest.mock import MagicMock, patch
        from pathlib import Path
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
```

---

## Implementation Notes

### 實作時的偏差與決策

1. **`MCPServerConfig` 新增 template 相容性屬性**
   - `transport` property（alias for `type`）、`get_transport_display()`、`env_json` property、`last_error` property（永遠回傳 `""`）、`connection_status` property（回傳 `live_status`）
   - 目的：讓既有 templates 無需大幅改寫即可使用 dataclass

2. **`live_status` 作為 dataclass field（而非純 runtime attribute）**
   - 加入 `live_status: str = field(default="", repr=False)` 讓 view 可以 `cfg.live_status = "connected"` 賦值
   - `_EXCLUDED_FIELDS = {"name", "live_status"}` 確保不會被序列化到 JSON

3. **Form 的 env/headers 合併欄位**
   - UI 沿用單一 `env_json` 欄位；view 依據 transport 決定存入 `env`（stdio）或 `headers`（SSE）
   - 避免大改 template；未來若需要分開欄位可獨立改進

4. **`run_stdio_connection` 新增 `args` 參數**
   - 舊 command string（如 `"npx -y @mcp/github"`）透過 `shlex.split` fallback 處理；新 config 可用 `command="npx"` + `args=["-y", "@mcp/github"]` 明確分離

5. **`TestExportMcpToFile` 測試未實作**
   - 移除 `MCPServer` model 後無法在測試中建立 DB records；改為 `export_mcp_to_file.py` command 的 try/except 防禦
   - 實際用法：在部署前用舊程式碼執行一次

6. **`EncryptedJSONField` 保留在 models.py**
   - `MCPServer` 已移除，但 `EncryptedJSONField` class 仍在 `agent/models.py`（供 `export_mcp_to_file` migration command 讀取舊 DB 時使用）
   - 新設計不再加密；敏感資訊改用 `${VAR}` 參照

7. **`tests/e2e/test_mcp.py` 更新**
   - 移除 `MCPServerFactory`，改用 `MCPServerConfig` + `upsert_server()` 寫入檔案
   - `mcp_server.id`（UUID）改為 `mcp_server.name`

### 新增檔案
- `agent/mcp/config.py` — `MCPServerConfig` dataclass + I/O functions
- `agent/management/commands/export_mcp_to_file.py` — 一次性 migration 指令
- `agent/migrations/0015_remove_mcpserver.py` — 刪除 `agent_mcpserver` 資料表
- `tests/agent/test_mcp_config.py` — 23 個測試全部通過
- `.testreport/029-mcp-file-based-config.md`

### 修改檔案
- `agent/mcp/client.py` — `run_stdio_connection` 新增 `args` 參數
- `agent/mcp/pool.py` — 全面移除 DB 查詢，改用 `load_servers()` / `get_server()`
- `agent/models.py` — 移除 `MCPServer` model
- `agent/views.py` — MCP views 改用 file-based config；新增 `_validate_mcp_post` / `_build_mcp_config_from_post` helpers
- `agent/urls.py` — MCP routes 從 `<uuid:pk>` 改為 `<str:name>`
- `agent/templates/agent/_mcp_server.html` — URL args 改用 `name`；移除 DB 狀態欄位引用
- `agent/templates/agent/_mcp_add_form.html` — form action URL 改用 `name`
- `agent/management/commands/sync_claude_code.py` — `_sync_mcp` 改讀 `mcp_servers.json`
- `config/settings/base.py` — 新增 `MCP_SERVERS_CONFIG_PATH`
- `tests/factories.py` — 移除 `MCPServerFactory`
- `tests/e2e/conftest.py` — 移除 `MCPServerFactory` import
- `tests/e2e/test_mcp.py` — 改用 file-based fixtures
