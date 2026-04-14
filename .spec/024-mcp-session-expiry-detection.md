# 024 — MCP Session Expiry Detection for SSE Servers

## Goal

Fix GavinAgent's `MCPConnectionPool` to detect and auto-recover from server-side SSE session expiry that manifests as JSON-RPC errors (e.g., EDWM MCP's `-32602`) rather than transport-level errors, without falsely reconnecting on legitimate parameter errors.

## Background

GavinAgent holds a **persistent SSE connection** to each MCP server via `MCPConnectionPool`. When an SSE session expires server-side, two different error patterns can occur:

**Pattern A — Transport-level (already handled):**
```
ClosedResourceError / EndOfStream / BrokenResourceError
```
`_is_session_dead_error()` catches these and triggers `_reconnect_server_async()`. ✅

**Pattern B — JSON-RPC level (NOT handled):**
```
MCP error -32602: Invalid request parameters
```
The SSE stream is still open at the TCP level, but the server's session state is gone. It re-uses `-32602` (normally "Invalid params") as a catch-all. `_is_session_dead_error()` does not recognise this — the session object exists, so the pool considers the connection healthy, and all subsequent tool calls silently return `-32602` forever.

### Why Pattern B is hard to detect

`-32602` is a legitimate JSON-RPC error for malformed parameters. If GavinAgent blindly reconnects on every `-32602`, it would reconnect on actual bugs in tool argument construction, masking real errors and wasting reconnection cycles.

The correct approach: **probe with a known no-param tool call**. If the probe also returns `-32602`, the session is dead (a no-param call cannot have invalid parameters). If the probe succeeds, the original error is a real parameter problem.

### The 60-second retry loop also misses Pattern B

`pool.py:_retry_loop` checks `get_status(server.name) != "connected"`, which only checks `conn.session is not None`. A zombie session (object present, server-side state gone) always returns `"connected"` and is never retried.

## Proposed Solution

### 1. `MCPServer` model — two new fields

```python
# agent/models.py — MCPServer
session_dead_error_codes = ArrayField(
    models.IntegerField(), default=list, blank=True,
    help_text="JSON-RPC error codes that indicate server-side session expiry (e.g. [-32602])"
)
health_probe_tool = models.CharField(
    max_length=100, blank=True,
    help_text="Tool name with no required params used to probe session health (e.g. get_current_date)"
)
```

EDWM MCP configuration after this change:
- `session_dead_error_codes: [-32602]`
- `health_probe_tool: get_current_date`

These are configured per-server in the Django admin or MCP management UI. Most servers leave both fields empty (existing behaviour unchanged).

### 2. `_is_jsonrpc_session_dead()` probe in `pool.py`

Add a new async helper that performs the probe-then-decide logic:

```python
async def _is_jsonrpc_session_dead(
    self,
    server_name: str,
    exc: Exception,
    conn: _ServerConnection,
) -> bool:
    """
    Return True if exc looks like a session-expiry JSON-RPC error.

    Steps:
    1. Check if the error code is in MCPServer.session_dead_error_codes.
    2. If a health_probe_tool is configured, call it with no args.
       - Probe also fails with matching code → session dead → True
       - Probe succeeds → real parameter error → False
    3. If no probe tool is configured, treat matching code as session dead directly.
    """
    from agent.models import MCPServer
    try:
        server = await sync_to_async(
            lambda: MCPServer.objects.get(name=server_name)
        )()
    except Exception:
        return False

    # Check if error code matches configured dead-session codes
    dead_codes = server.session_dead_error_codes or []
    if not dead_codes:
        return False

    # Use word-boundary regex to avoid false positives (e.g. code 602 matching -32602)
    import re
    exc_str = str(exc)
    matched = any(
        re.search(rf"(?<!\d){re.escape(str(code))}(?!\d)", exc_str)
        for code in dead_codes
    )
    if not matched:
        return False

    probe_tool = (server.health_probe_tool or "").strip()
    if not probe_tool:
        # No probe configured — treat matching code as session dead
        logger.warning(
            "MCP %s: error matches session_dead_error_codes but no probe tool configured "
            "— assuming session dead", server_name
        )
        return True

    # Probe with no-param tool call
    try:
        await conn.session.call_tool(probe_tool, {})
        # Probe succeeded → the original error was a real parameter problem
        logger.debug("MCP %s: probe '%s' succeeded — original error is a real param error", server_name, probe_tool)
        return False
    except Exception as probe_exc:
        probe_str = str(probe_exc)
        if any(str(code) in probe_str for code in dead_codes):
            logger.warning(
                "MCP %s: probe '%s' also returned session-dead error — session is dead",
                server_name, probe_tool,
            )
            return True
        # Probe failed for a different reason (e.g. tool not found) — inconclusive
        logger.warning(
            "MCP %s: probe '%s' failed with unexpected error '%s' — not reconnecting",
            server_name, probe_tool, probe_exc,
        )
        return False
```

### 3. Update `_call_tool_async()` to use the new probe

```python
async def _call_tool_async(self, server_name: str, tool_name: str, args: dict) -> dict:
    conn = self._connections.get(server_name)
    if conn is None or conn.session is None:
        raise MCPTimeoutError(f"No active connection to MCP server: {server_name}")
    try:
        result = await conn.session.call_tool(tool_name, args)
        return {"content": extract_tool_content(result)}
    except Exception as exc:
        # Pattern A: transport-level dead session (existing)
        if _is_session_dead_error(exc):
            logger.warning("MCP %s: %s — reconnecting", server_name, type(exc).__name__)
        # Pattern B: JSON-RPC dead session (new)
        elif await self._is_jsonrpc_session_dead(server_name, exc, conn):
            logger.warning("MCP %s: JSON-RPC session dead — reconnecting", server_name)
        else:
            raise  # real error, don't reconnect

        await self._reconnect_server_async(server_name)
        conn = self._connections.get(server_name)
        if conn is None or conn.session is None:
            raise MCPTimeoutError(f"MCP {server_name}: reconnect succeeded but session unavailable")
        # Retry exactly once — do NOT recurse into dead-session detection again
        # to prevent infinite reconnect loops if the server is in a bad state.
        result = await conn.session.call_tool(tool_name, args)
        return {"content": extract_tool_content(result)}
```

Apply the same pattern to `_read_resource_async()` — this is **in scope** (see Acceptance Criteria). The retry-once guard applies equally: after reconnect, call `read_resource` once without re-entering dead-session detection.

### 4. Fix the retry loop — active session health check

The 60-second retry loop currently only checks `conn.session is not None`. Extend it to actively probe sessions that have a `health_probe_tool` configured:

```python
async def _check_session_health(self, server) -> bool:
    """
    Return True if the session is alive. Probes with health_probe_tool if configured.
    Falls back to checking conn.session is not None.
    """
    conn = self._connections.get(server.name)
    if conn is None or conn.session is None:
        return False

    probe_tool = (server.health_probe_tool or "").strip()
    if not probe_tool:
        return True  # no probe — assume alive

    dead_codes = server.session_dead_error_codes or []
    try:
        await conn.session.call_tool(probe_tool, {})
        return True
    except Exception as exc:
        # Pattern A: transport-level error on probe → session definitely dead
        if _is_session_dead_error(exc):
            logger.warning("MCP %s: health probe raised transport error — session dead", server.name)
            return False
        # Pattern B: JSON-RPC dead-session code on probe
        import re
        exc_str = str(exc)
        if dead_codes and any(
            re.search(rf"(?<!\d){re.escape(str(code))}(?!\d)", exc_str)
            for code in dead_codes
        ):
            logger.warning("MCP %s: health probe failed — session dead", server.name)
            return False
        return True  # probe failed for unrelated reason — don't disconnect
```

In `_retry_loop`, replace the simple status check:
```python
# Before
if self.get_status(server.name) != "connected":
    self.start_server(server)

# After
is_healthy = asyncio.run_coroutine_threadsafe(
    self._check_session_health(server), self._loop
).result(timeout=10)
if not is_healthy:
    logger.info("MCP retry: reconnecting %s (session dead or disconnected)", server.name)
    self.start_server(server)
```

### 5. Django migration

New migration for `session_dead_error_codes` (`ArrayField(IntegerField)` — PostgreSQL-only, consistent with the existing pgvector dependency) and `health_probe_tool` (`CharField(max_length=100, blank=True)`) on `MCPServer`.

Both fields default to empty (`[]` and `""` respectively) so existing server rows require no data migration.

### 6. Admin / UI configuration

The MCP server edit form should expose both new fields so operators can configure EDWM (and future SSE servers with similar behaviour) without a code change.

## Edge Cases

**Tool used as probe is the same tool that failed**: the probe is called with `{}` (no args), not the original args. Since the probe tool must be a no-param tool, this is safe.

**Probe tool does not exist on the server**: the call will fail with a different error (e.g., "tool not found"), which is neither a transport error nor a matching JSON-RPC code. `_is_jsonrpc_session_dead()` returns `False` and logs a warning. No reconnect, original error re-raised — correct behaviour.

**Server reconnect fails**: `_reconnect_server_async()` already handles this and raises. The caller gets an `MCPTimeoutError`. No change needed.

**Probe itself triggers another session expiry mid-probe**: the probe exception is checked against `dead_codes` — it will be caught and return `True`. Reconnect proceeds.

**Post-reconnect call also fails**: the retry call after reconnect is a plain `call_tool` with no dead-session detection wrapper. If it also fails, the exception propagates normally to the agent. This is intentional — it prevents infinite reconnect loops if the server is persistently in a bad state. The agent will surface the error to the user who can then manually recover.

## Out of Scope

- Fixing the `ClosedResourceError` / Pattern A path (already working)
- Changing EDWM MCP server-side session timeout (requires admin access to EDWM MCP)
- Auto-detecting which error codes indicate session expiry without operator configuration
- Supporting non-PostgreSQL databases (ArrayField is PostgreSQL-only; already a project constraint)

## Acceptance Criteria

- [ ] `MCPServer` model has `session_dead_error_codes` (ArrayField) and `health_probe_tool` (CharField); migration exists
- [ ] EDWM MCP server configured with `session_dead_error_codes=[-32602]`, `health_probe_tool=get_current_date`
- [ ] When EDWM session expires and tool call returns `-32602`: probe runs, probe also returns `-32602`, reconnect triggers automatically, original tool call is retried
- [ ] When a real `-32602` parameter error occurs: probe succeeds, no reconnect, original error is re-raised to the caller
- [ ] `_retry_loop` detects zombie sessions via `_check_session_health()` and reconnects within 60s
- [ ] Servers without `session_dead_error_codes` configured behave identically to today (no regression)
- [ ] `_read_resource_async()` has the same Pattern B detection as `_call_tool_async()`

## Open Questions

1. **Probe call side effects**: `get_current_date` is assumed to be a pure read with no side effects. The spec documents `health_probe_tool` as a convention requiring an idempotent, no-param, read-only tool — but this is not enforced at runtime. Should the admin UI add a warning when saving a `health_probe_tool` that is not in the server's registered tool list?

2. **Probe timeout**: The probe call in `_is_jsonrpc_session_dead()` uses the same `AGENT_TOOL_TIMEOUT_SECONDS` (default 30s). On a dead session, the probe may hang for the full timeout before failing, adding 30s latency to every failed tool call. A dedicated `AGENT_HEALTH_PROBE_TIMEOUT_SECONDS` setting (default 5s) should be considered — but this is deferred from v1 to keep the implementation simple.

3. **Retry loop frequency**: The retry loop probes every 60s. A dead EDWM session may go undetected for up to 60s. If sessions expire frequently, a configurable per-server `health_check_interval_seconds` field would help — deferred from v1.

## Implementation Notes

<!-- Filled in during or after implementation. -->
