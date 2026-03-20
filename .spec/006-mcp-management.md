# 004 — MCP Server Management

**Status:** Draft
**Created:** 2026-03-19

---

## Goal

Integrate Model Context Protocol (MCP) as a first-class tool and resource source for
the agent. Users configure MCP servers via the agent dashboard; the agent discovers
their tools and resources automatically and can call them alongside built-in tools and
Skills.

---

## Background

Spec 003 defines the agent tool system (`agent/tools/`) and skill system
(`agent/skills/`). MCP is the third and most extensible source of agent capabilities:
a standard protocol that connects the agent to external servers (local processes or
remote HTTP endpoints), each exposing multiple tools and resources. This spec covers
the database model, secure secret storage, process lifecycle management, resource
support, and management UI.

---

## Decisions

| Question | Decision |
|---|---|
| How to store MCP server secrets securely? | Encrypt the `env` JSONField at rest using `django-fernet-fields`. Secrets never stored in plaintext in the DB. |
| How to manage stdio server process lifecycle? | Start all enabled stdio servers on Django/Celery worker init via `AppConfig.ready()` and Celery `worker_init` signal. Each process maintains its own `MCPConnectionPool`. Terminate on server disable or process shutdown. |
| Support MCP Resources (not just Tools)? | Yes. Resources are discovered via `list_resources()` and injected into `assemble_context` as additional context. Also exposed as a `read_resource` tool so the LLM can fetch them on demand. |

---

## Database Model

### `MCPServer`

```python
from django_fernet_fields import EncryptedJSONField

class MCPServer(TimeStampedModel):
    class Transport(models.TextChoices):
        STDIO = "stdio"   # local process (e.g. filesystem, git, postgres)
        SSE   = "sse"     # remote HTTP endpoint

    id          = UUIDField(primary_key=True)
    name        = CharField(max_length=100, unique=True)
    description = TextField(blank=True)
    transport   = CharField(max_length=10, choices=Transport)

    # stdio: shell command to launch the server process
    # e.g. "npx -y @modelcontextprotocol/server-filesystem /workspace"
    command     = CharField(max_length=500, blank=True)

    # sse: remote endpoint URL
    url         = CharField(max_length=500, blank=True)

    # Encrypted at rest. Values are injected as env vars when launching stdio
    # servers, or sent as auth headers for SSE servers.
    # Format: {"GITHUB_TOKEN": "ghp_...", "API_KEY": "sk-..."}
    env         = EncryptedJSONField(default=dict)

    # Tool names that may execute without user approval.
    # All other tools from this server require approval.
    auto_approve_tools    = JSONField(default=list)

    # If True, all read_resource calls for this server are auto-approved.
    # Kept separate from auto_approve_tools for clarity.
    auto_approve_resources = BooleanField(default=False)

    # Last known connection state — persisted for UI display across restarts.
    class ConnectionStatus(models.TextChoices):
        UNKNOWN      = "unknown"
        CONNECTED    = "connected"
        DISCONNECTED = "disconnected"
        ERROR        = "error"

    connection_status  = CharField(max_length=20, choices=ConnectionStatus, default=ConnectionStatus.UNKNOWN)
    last_connected_at  = DateTimeField(null=True, blank=True)
    last_error         = TextField(blank=True)

    enabled     = BooleanField(default=True)

    class Meta:
        ordering = ["name"]
```

`EncryptedJSONField` uses Fernet symmetric encryption (AES-128-CBC + HMAC). The
encryption key is read from `settings.FERNET_KEYS` (a list to support key rotation).

---

## Directory Structure

```
agent/
└── mcp/
    ├── __init__.py
    ├── client.py       ← MCPClient: connects to one server, calls list_tools /
    │                     list_resources / call_tool / read_resource
    ├── pool.py         ← MCPConnectionPool: process-level singleton, manages
    │                     all active MCP connections
    └── registry.py     ← MCPToolRegistry: wraps MCP tools as LangGraph tools,
                          namespaced as "<server_name>__<tool_name>"
```

---

## Process Lifecycle (stdio)

### Startup

```python
# agent/apps.py
class AgentConfig(AppConfig):
    def ready(self):
        from agent.mcp.pool import MCPConnectionPool
        MCPConnectionPool.get().start_all()
```

```python
# config/celery.py
from celery.signals import worker_init

@worker_init.connect
def init_mcp(sender, **kwargs):
    from agent.mcp.pool import MCPConnectionPool
    MCPConnectionPool.get().start_all()
```

### Async/sync boundary

`AppConfig.ready()` is synchronous. MCP stdio connections require asyncio.
The pool runs its own dedicated background thread with a persistent event loop:

```python
class MCPConnectionPool:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def start_all(self):
        asyncio.run_coroutine_threadsafe(self._start_all_async(), self._loop)
```

All async MCP operations are submitted to this loop via `run_coroutine_threadsafe()`.
This avoids conflicts with Django's own sync context and works in both Django and
Celery worker processes.

### Multi-worker consideration

In production with multiple Gunicorn workers, each worker process spawns its own
`MCPConnectionPool` and its own stdio subprocesses. For stdio servers that hold
exclusive resources (e.g. filesystem locks), run with a **single worker** or use
an external MCP proxy process. Document this requirement in deployment notes.

### Pool behaviour

```
MCPConnectionPool (process-level singleton, runs in dedicated thread)
    │
    ├── stdio servers → asyncio subprocess, persistent stdin/stdout pipe
    │     - Restart automatically on crash (max 3 retries, then disable + log)
    │     - Health check: ping every 60s; silent failure triggers reconnect
    │
    └── sse servers   → persistent httpx async client with keep-alive
          - Health check: GET /health or send ping every 60s
```

### On config change

Django signal on `MCPServer.post_save`:
- If `enabled` flipped to `False` → terminate connection
- If command/url/env changed → restart connection
- If `enabled` flipped to `True` → start connection

### On shutdown

`AppConfig` `shutdown` hook and Celery `worker_shutdown` signal call
`MCPConnectionPool.get().stop_all()`.

---

## Tool Discovery & Registration

On connection established:

```
MCPClient.list_tools()
    │
    ▼
MCPToolRegistry.register(server_name, tools)
    │
    ▼
Each tool wrapped as LangGraph StructuredTool:
    name:        "<server_name>__<tool_name>"
    description: from MCP tool schema
    args_schema: from MCP inputSchema (JSON Schema → Pydantic)
    func:        MCPClient.call_tool(tool_name, input)
    │
    ▼
Injected into ToolExecutor alongside built-in tools and Skills
```

Approval check: if `tool_name` (without prefix) is in `MCPServer.auto_approve_tools`
→ auto execute; otherwise → `ToolExecution(status=pending)`, run pauses for approval.

---

## Resource Support

### Discovery

On connection, also call `MCPClient.list_resources()`. Store resource metadata
(uri, name, mimeType) in memory (not DB — resources are dynamic).

### Two access modes

**Mode 1 — Context injection (`assemble_context` node)**

Resources where `MCPServer.always_include_resources` contains their URI are fetched
and appended to the system context at the start of every agent run. This is a
`JSONField(default=list)` on `MCPServer` — not a standard MCP protocol field.
Suitable for small, stable resources (e.g. database schema, project README).

**Mode 2 — On-demand via `read_resource` tool**

A single meta-tool exposed to the LLM:

```
tool: read_resource
input: { "server": "postgres", "uri": "schema://public" }
output: resource content (text or base64)
```

The LLM decides when to fetch a resource. Approval policy follows the server's
`auto_approve_tools` setting (include `"read_resource"` to auto-approve all resource
reads from that server).

---

## Secure Secret Storage

### Encryption

```bash
uv add django-fernet-fields
```

```python
# config/settings/base.py
FERNET_KEYS = [
    config("FERNET_KEY"),            # current key
    config("FERNET_KEY_PREVIOUS", default=""),  # previous key (rotation)
]
```

Generate a key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add `FERNET_KEY` to `.env`. The `env` JSONField is encrypted before write and
decrypted on read — transparent to the rest of the application.

### Key rotation

Set `FERNET_KEY` to the new key and `FERNET_KEY_PREVIOUS` to the old key.
`django-fernet-fields` decrypts with either key and re-encrypts with the current key
on next save. Run a management command to re-encrypt all rows after rotation.

---

## Management UI

New views added to `agent/views.py` and `agent/urls.py`:

| Method | Path | Description | Response |
|---|---|---|---|
| `GET` | `/agent/mcp/` | List servers + their tools and resources | Full HTML |
| `POST` | `/agent/mcp/add/` | Add new MCP server | HTMX partial |
| `GET` | `/agent/mcp/<id>/` | Server detail — tools, resources, connection status | HTMX partial |
| `POST` | `/agent/mcp/<id>/toggle/` | Enable / disable | HTMX partial |
| `POST` | `/agent/mcp/<id>/refresh/` | Re-discover tools and resources | HTMX partial |
| `DELETE` | `/agent/mcp/<id>/delete/` | Remove server and terminate connection | HTMX partial |

Templates:
```
agent/templates/agent/
├── mcp.html              ← server list
├── _mcp_server.html      ← single server card (tools + resources + status)
└── _mcp_add_form.html    ← add server form partial
```

---

## Runtime Prerequisites

**Node.js** is required for most official MCP servers (run via `npx`).
Add to deployment documentation:

```bash
# macOS
brew install node

# Verify
node --version   # >= 18
npx --version
```

For production Docker deployments, use a base image that includes Node.js
(e.g. `python:3.12-slim` + `apt-get install nodejs npm`).

---

## New Dependencies

```bash
uv add django-fernet-fields   # encrypted model fields
uv add mcp                    # MCP Python SDK (client)
uv add langchain-mcp-adapters # converts MCP tools to LangGraph StructuredTools
```

---

## New Environment Variables

```bash
FERNET_KEY=           # required — generated once, stored securely
FERNET_KEY_PREVIOUS=  # optional — only during key rotation
```

---

## Common MCP Servers (reference)

| Server | Transport | Auto-approve tools |
|---|---|---|
| `@modelcontextprotocol/server-filesystem` | stdio | `read_file`, `list_directory` |
| `@modelcontextprotocol/server-git` | stdio | `git_log`, `git_diff`, `git_status` |
| `@modelcontextprotocol/server-postgres` | stdio | `query` (read-only queries) |
| `@modelcontextprotocol/server-github` | sse | `get_issue`, `list_pull_requests` |
| `@modelcontextprotocol/server-brave-search` | stdio | `brave_web_search` |

---

## Validation

### Model-level

| Model | Rule | Enforcement |
|---|---|---|
| `MCPServer` | `transport=stdio` requires non-empty `command` | `clean()` |
| `MCPServer` | `transport=sse` requires non-empty `url` (valid URL format) | `clean()` |
| `MCPServer` | `name` unique, slugified (no spaces) | `unique=True` + `clean()` |
| `MCPServer` | `env` values must be strings (no nested objects) | `clean()` validates JSONField structure |
| `MCPServer` | `always_include_resources` entries must be valid URIs | `clean()` with basic URI format check |

### View-level

- `POST /agent/mcp/add/`: return 400 with field errors if `clean()` fails
- `POST /agent/mcp/<id>/toggle/`: if enabling fails to connect after 10s → return 200 with error state partial, do not raise 500
- `DELETE /agent/mcp/<id>/delete/`: require confirmation token in POST body (prevent accidental deletion)

---

## Testing

### Additional test dependency

```bash
uv add --dev pytest-asyncio   # for async pool and client tests
```

### Fixtures (`agent/tests/conftest.py`)

```python
@pytest.fixture
def stdio_mcp_server(db):
    return MCPServer.objects.create(
        name="test-fs",
        transport=MCPServer.Transport.STDIO,
        command="npx -y @modelcontextprotocol/server-filesystem /tmp",
        auto_approve_tools=["read_file", "list_directory"],
        auto_approve_resources=True,
        enabled=True,
    )

@pytest.fixture
def mock_mcp_client(mocker):
    """Patches MCPClient to avoid real subprocess/network calls."""
    client = mocker.MagicMock()
    client.list_tools.return_value = [
        {"name": "read_file", "description": "Read a file", "inputSchema": {...}},
    ]
    client.list_resources.return_value = [
        {"uri": "file:///tmp/README.md", "name": "README", "mimeType": "text/plain"},
    ]
    client.call_tool.return_value = {"content": "file contents"}
    client.read_resource.return_value = "resource contents"
    return client
```

### Unit tests

#### Encryption (`agent/tests/test_mcp_encryption.py`)

| Test | What it verifies |
|---|---|
| `test_env_field_encrypted_at_rest` | Raw DB value of `env` column is not plaintext JSON |
| `test_env_field_decrypts_on_read` | Reading `server.env` returns original dict |
| `test_env_survives_key_rotation` | After rotating `FERNET_KEY`, old records still decrypt |
| `test_env_empty_dict_stored_safely` | Empty `env={}` does not raise on encrypt/decrypt |

#### MCPClient (`agent/tests/test_mcp_client.py`)

| Test | What it verifies |
|---|---|
| `test_list_tools_returns_parsed_schema` | Tool list parsed from MCP protocol response |
| `test_call_tool_sends_correct_request` | Correct tool name and input sent to server |
| `test_call_tool_timeout` | Raises `MCPTimeoutError` if server does not respond |
| `test_list_resources_returns_metadata` | Resource uri, name, mimeType parsed correctly |
| `test_read_resource_returns_content` | Resource content returned as string |

#### MCPConnectionPool (`agent/tests/test_mcp_pool.py`)

| Test | What it verifies |
|---|---|
| `test_start_all_connects_enabled_servers` | All `enabled=True` servers get a client in pool |
| `test_disabled_server_not_started` | `enabled=False` server has no pool entry |
| `test_crash_triggers_restart` | Simulated subprocess crash → pool restarts client |
| `test_max_retries_disables_server` | After 3 crashes → `MCPServer.enabled=False`, `connection_status=error` |
| `test_health_check_detects_silent_failure` | Mock silent drop → health check triggers reconnect |
| `test_stop_all_terminates_processes` | `stop_all()` closes all subprocess connections |

#### MCPToolRegistry (`agent/tests/test_mcp_registry.py`)

| Test | What it verifies |
|---|---|
| `test_tools_namespaced_correctly` | Tool registered as `test-fs__read_file` |
| `test_tool_callable_via_registry` | Calling registry tool invokes `MCPClient.call_tool` |
| `test_two_servers_no_naming_conflict` | Tools from two servers coexist without collision |
| `test_auto_approve_tool_bypasses_approval` | Tool in `auto_approve_tools` → no `ToolExecution(pending)` |
| `test_non_auto_approve_requires_approval` | Tool not in list → `ToolExecution(status=pending)` created |
| `test_auto_approve_resources_flag` | `auto_approve_resources=True` → `read_resource` auto-executes |

### Integration tests

#### Full MCP tool call via agent loop (`agent/tests/test_mcp_integration.py`)

```python
@pytest.mark.django_db
def test_mcp_tool_called_by_agent(agent_run, mock_mcp_client, mock_llm_with_tool_call):
    """Agent loop calls an MCP tool and feeds result back to LLM."""
    mock_llm_with_tool_call.side_effect = [
        FakeLLMResponse(tool_call="test-fs__read_file", args={"path": "/tmp/notes.txt"}),
        FakeLLMResponse("The file contains: file contents"),
    ]
    AgentRunner.run(agent_run)
    mock_mcp_client.call_tool.assert_called_once_with("read_file", {"path": "/tmp/notes.txt"})
    assert chat.Message.objects.filter(content__contains="file contents").exists()

@pytest.mark.django_db
def test_always_include_resource_injected_in_context(
    agent_run, stdio_mcp_server, mock_mcp_client, mock_llm
):
    """Resource URI in always_include_resources appears in assembled context."""
    stdio_mcp_server.always_include_resources = ["file:///tmp/README.md"]
    stdio_mcp_server.save()
    with patch("agent.graph.nodes.assemble_context") as mock_ctx:
        AgentRunner.run(agent_run)
        context_messages = mock_ctx.call_args[0][0]
        assert any("resource contents" in str(m) for m in context_messages)

@pytest.mark.django_db
def test_connection_status_persisted_after_connect(stdio_mcp_server, mock_mcp_client):
    """Successful connection updates connection_status and last_connected_at in DB."""
    MCPConnectionPool.get().start_server(stdio_mcp_server)
    stdio_mcp_server.refresh_from_db()
    assert stdio_mcp_server.connection_status == MCPServer.ConnectionStatus.CONNECTED
    assert stdio_mcp_server.last_connected_at is not None

@pytest.mark.django_db
def test_toggle_off_terminates_connection(stdio_mcp_server, mock_mcp_client, client):
    """POST /agent/mcp/<id>/toggle/ with enabled server → terminates connection."""
    MCPConnectionPool.get().start_server(stdio_mcp_server)
    response = client.post(f"/agent/mcp/{stdio_mcp_server.id}/toggle/")
    assert response.status_code == 200
    stdio_mcp_server.refresh_from_db()
    assert not stdio_mcp_server.enabled
    assert stdio_mcp_server.connection_status == MCPServer.ConnectionStatus.DISCONNECTED
```

#### Real stdio server smoke test (optional, skipped in CI)

```python
@pytest.mark.skipif(
    not shutil.which("npx"), reason="npx not available"
)
@pytest.mark.django_db
def test_real_filesystem_server(tmp_path):
    """Connects to real MCP filesystem server and reads a file."""
    (tmp_path / "hello.txt").write_text("hello world")
    server = MCPServer.objects.create(
        name="real-fs", transport="stdio",
        command=f"npx -y @modelcontextprotocol/server-filesystem {tmp_path}",
        auto_approve_tools=["read_file"],
        enabled=True,
    )
    pool = MCPConnectionPool.get()
    pool.start_server(server)
    result = pool.call_tool("real-fs", "read_file", {"path": "hello.txt"})
    assert "hello world" in result["content"]

```

### What is NOT tested (and why)

| Excluded | Reason |
|---|---|
| Real SSE MCP servers | Require external network; tested manually against GitHub/Brave servers |
| Fernet key generation correctness | Delegated to `cryptography` library; trust well-tested dependency |
| Multi-worker stdio process isolation | Infrastructure concern; covered by deployment runbook, not unit tests |

---

## Out of Scope

- MCP server **hosting** (this project is MCP client only)
- MCP **Prompts** (third MCP primitive — deferred; Tools and Resources cover all current needs)
- Multi-user secret isolation (single-user deployment per spec 001)

---

## Acceptance Criteria

- [ ] `MCPServer` records with `env` field cannot be read as plaintext from the database
- [ ] Enabled stdio MCP servers start automatically when Django or a Celery worker starts
- [ ] Crashed stdio server restarts automatically (up to 3 times), then disables with error log
- [ ] Silent connection drops are detected within 60s via health check and trigger reconnect
- [ ] Tools from MCP servers appear in the agent's tool list and can be called by the LLM
- [ ] Resources in `always_include_resources` are injected into every `assemble_context` call
- [ ] LLM can call `read_resource` to fetch any listed resource on demand
- [ ] `auto_approve_resources=True` auto-approves all resource reads; default requires approval
- [ ] `connection_status` and `last_connected_at` are persisted to DB and shown in the UI
- [ ] Disabling a server from the dashboard terminates its connection within 5 seconds
- [ ] Fernet key rotation re-encrypts all `env` fields without data loss
- [ ] Deployment docs note Node.js >= 18 as a runtime prerequisite

---

## Open Questions

_All questions resolved — spec ready for implementation._
