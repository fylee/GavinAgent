from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from .client import (
    MCPTimeoutError,
    _ServerConnection,
    extract_resource_content,
    extract_tool_content,
    run_sse_connection,
    run_stdio_connection,
)
from .registry import MCPToolEntry, get_registry

logger = logging.getLogger(__name__)


def _is_session_dead_error(exc: Exception) -> bool:
    """Return True if the exception indicates an expired or closed MCP session."""
    exc_type = type(exc).__name__
    if exc_type in ("ClosedResourceError", "EndOfStream", "BrokenResourceError"):
        return True
    try:
        import anyio
        if isinstance(exc, (anyio.ClosedResourceError, anyio.EndOfStream)):
            return True
    except (ImportError, AttributeError):
        pass
    return False


class MCPConnectionPool:
    """
    Process-level singleton that manages all MCP server connections.

    Runs an asyncio event loop in a dedicated background thread so that
    synchronous Django/Celery code can call MCP tools via run_coroutine_threadsafe.
    """

    _instance: MCPConnectionPool | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="mcp-pool"
        )
        self._thread.start()
        self._connections: dict[str, _ServerConnection] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    @classmethod
    def get(cls) -> MCPConnectionPool:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    # ── public sync API ────────────────────────────────────────────────────

    def start_all(self) -> None:
        """Connect all enabled MCP servers and wait for tool discovery. Called on Django/Celery startup."""
        future = asyncio.run_coroutine_threadsafe(self._start_all_async(), self._loop)
        try:
            future.result(timeout=30)
        except Exception as exc:
            logger.error("MCP pool start_all error: %s", exc, exc_info=True)
        # Start background retry loop so newly-added servers and transient
        # failures are healed without requiring a restart.
        threading.Thread(target=self._retry_loop, daemon=True, name="mcp-retry").start()

    def _retry_loop(self) -> None:
        """Background thread: every 60 s reconnect any server that is not connected."""
        import time
        first_run = True
        while True:
            # Check quickly on first iteration (10 s) to catch startup race conditions,
            # then settle to 60 s intervals.
            time.sleep(10 if first_run else 60)
            first_run = False
            try:
                from agent.models import MCPServer
                servers = list(MCPServer.objects.filter(enabled=True))
                reg = get_registry()
                tool_count = len(reg.all())
                logger.info("MCP registry health: %d tools registered", tool_count)
                for server in servers:
                    # Spec 024: use active health probe when configured;
                    # fall back to simple session-present check for unconfigured servers.
                    try:
                        is_healthy = asyncio.run_coroutine_threadsafe(
                            self._check_session_health(server), self._loop
                        ).result(timeout=10)
                    except Exception:
                        is_healthy = self.get_status(server.name) == "connected"

                    if not is_healthy:
                        logger.info("MCP retry: reconnecting %s (session dead or disconnected)", server.name)
                        self.start_server(server)
                    else:
                        # Server connected but registry empty → re-discover tools
                        server_tools = [
                            t for t in reg.all().values()
                            if t.server_name == server.name
                        ]
                        if not server_tools:
                            logger.warning(
                                "MCP server %s connected but no tools in registry — re-discovering",
                                server.name,
                            )
                            conn = self._connections.get(server.name)
                            if conn and conn.session:
                                future = asyncio.run_coroutine_threadsafe(
                                    self._discover_tools(server.name, conn.session),
                                    self._loop,
                                )
                                try:
                                    future.result(timeout=15)
                                except Exception as exc:
                                    logger.error("MCP re-discover %s error: %s", server.name, exc)
            except Exception as exc:
                logger.debug("MCP retry loop error: %s", exc)

    def stop_all(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self._stop_all_async(), self._loop)
        try:
            future.result(timeout=10)
        except Exception:
            pass

    def start_server(self, server) -> None:
        """Connect a single MCPServer model instance."""
        future = asyncio.run_coroutine_threadsafe(
            self._start_server_async(server), self._loop
        )
        try:
            future.result(timeout=5)
        except Exception as exc:
            logger.error("MCP start_server %s error: %s", server.name, exc)

    def stop_server(self, server_name: str) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self._stop_server_async(server_name), self._loop
        )
        try:
            future.result(timeout=10)
        except Exception:
            pass

    def call_tool(self, server_name: str, tool_name: str, args: dict) -> dict:
        from django.conf import settings
        timeout = getattr(settings, "AGENT_TOOL_TIMEOUT_SECONDS", 30)
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(server_name, tool_name, args), self._loop
        )
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            raise MCPTimeoutError(f"MCP tool {server_name}::{tool_name} timed out")

    def read_resource(self, server_name: str, uri: str) -> str:
        from django.conf import settings
        timeout = getattr(settings, "AGENT_TOOL_TIMEOUT_SECONDS", 30)
        future = asyncio.run_coroutine_threadsafe(
            self._read_resource_async(server_name, uri), self._loop
        )
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            raise MCPTimeoutError(f"MCP resource {server_name}::{uri} timed out")

    def refresh_server(self, server_name: str) -> None:
        """Re-discover tools and resources for a connected server."""
        future = asyncio.run_coroutine_threadsafe(
            self._refresh_server_async(server_name), self._loop
        )
        try:
            future.result(timeout=15)
        except Exception as exc:
            logger.error("MCP refresh_server %s error: %s", server_name, exc)

    def get_status(self, server_name: str) -> str:
        conn = self._connections.get(server_name)
        if conn is None:
            return "disconnected"
        return "connected" if conn.session is not None else "disconnected"

    def fetch_always_include_resources(self) -> list[str]:
        """
        Return content of all resources marked always_include_resources,
        for injection into the agent system context.
        """
        results = []
        try:
            from agent.models import MCPServer
            servers = MCPServer.objects.filter(enabled=True).exclude(
                always_include_resources=[]
            )
            for server in servers:
                for uri in server.always_include_resources:
                    try:
                        content = self.read_resource(server.name, uri)
                        if content:
                            results.append(f"### Resource: {uri}\n\n{content}")
                    except Exception as exc:
                        logger.warning("Could not fetch resource %s: %s", uri, exc)
        except Exception:
            pass
        return results

    # ── async internals ────────────────────────────────────────────────────

    async def _start_all_async(self) -> None:
        try:
            from agent.models import MCPServer
            from asgiref.sync import sync_to_async
            servers = await sync_to_async(lambda: list(MCPServer.objects.filter(enabled=True)))()
            tasks = [self._start_server_async(server) for server in servers]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as exc:
            logger.error("MCP _start_all_async error: %s", exc, exc_info=True)

    async def _start_server_async(self, server) -> None:
        name = server.name
        if name in self._connections:
            return  # already running

        conn = _ServerConnection()
        self._connections[name] = conn

        ready_event = asyncio.Event()
        error_event = asyncio.Event()

        async def on_ready(srv_name: str, session: Any) -> None:
            await self._discover_tools(srv_name, session)
            await self._update_db_status(srv_name, "connected")
            ready_event.set()

        async def on_error(srv_name: str, error: str) -> None:
            logger.error("MCP server '%s' connection error: %s", srv_name, error)
            await self._update_db_status(srv_name, "error", error)
            self._connections.pop(srv_name, None)
            self._tasks.pop(srv_name, None)
            get_registry().unregister_server(srv_name)
            error_event.set()

        if server.transport == "stdio":
            coro = run_stdio_connection(
                name, server.command, dict(server.env or {}), conn, on_ready, on_error
            )
        else:
            headers = {k: v for k, v in (server.env or {}).items()}
            coro = run_sse_connection(
                name, server.url, headers, conn, on_ready, on_error
            )

        task = self._loop.create_task(coro)
        self._tasks[name] = task

        # Wait up to 25 s for ready or error so start_all knows discovery is done.
        try:
            done, _ = await asyncio.wait(
                [asyncio.ensure_future(ready_event.wait()),
                 asyncio.ensure_future(error_event.wait())],
                timeout=25,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                logger.warning("MCP server '%s' did not connect within 25 s", name)
            elif ready_event.is_set():
                logger.info("MCP server '%s' ready", name)
        except Exception as exc:
            logger.error("MCP server '%s' wait error: %s", name, exc)

    async def _stop_server_async(self, server_name: str) -> None:
        conn = self._connections.pop(server_name, None)
        if conn:
            conn.stop.set()
        task = self._tasks.pop(server_name, None)
        if task:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except Exception:
                pass
        get_registry().unregister_server(server_name)
        await self._update_db_status(server_name, "disconnected")

    async def _stop_all_async(self) -> None:
        names = list(self._connections.keys())
        for name in names:
            await self._stop_server_async(name)

    async def _reconnect_server_async(self, server_name: str) -> None:
        """Stop and restart a server whose SSE session has expired."""
        logger.info("MCP %s: session dead — reconnecting", server_name)
        await self._stop_server_async(server_name)
        try:
            from asgiref.sync import sync_to_async
            from agent.models import MCPServer
            server = await sync_to_async(
                lambda: MCPServer.objects.get(name=server_name)
            )()
            await self._start_server_async(server)
        except Exception as exc:
            logger.error("MCP %s: reconnect failed: %s", server_name, exc)
            raise

    async def _is_jsonrpc_session_dead(
        self,
        server_name: str,
        exc: Exception,
        conn: "_ServerConnection",
    ) -> bool:
        """
        Spec 024 — Pattern B: detect server-side session expiry that manifests
        as a JSON-RPC error code rather than a transport-level disconnect.

        Steps:
        1. Check if the error code is in MCPServer.session_dead_error_codes.
        2. If a health_probe_tool is configured, call it with no args.
           - Probe also fails with matching code  →  session dead  →  True
           - Probe succeeds                        →  real param error  →  False
        3. If no probe tool is configured, treat matching code as session dead.
        """
        import re
        from asgiref.sync import sync_to_async
        from agent.models import MCPServer

        try:
            server = await sync_to_async(
                lambda: MCPServer.objects.get(name=server_name)
            )()
        except Exception:
            return False

        dead_codes: list = server.session_dead_error_codes or []
        if not dead_codes:
            return False

        # Word-boundary match to avoid e.g. code 602 matching -32602
        exc_str = str(exc)
        matched = any(
            re.search(rf"(?<!\d){re.escape(str(code))}(?!\d)", exc_str)
            for code in dead_codes
        )
        if not matched:
            return False

        probe_tool = (server.health_probe_tool or "").strip()
        if not probe_tool:
            logger.warning(
                "MCP %s: error matches session_dead_error_codes but no probe tool configured "
                "\u2014 assuming session dead",
                server_name,
            )
            return True

        # Probe with a known no-param tool call
        try:
            await conn.session.call_tool(probe_tool, {})
            # Probe succeeded → the original error was a real parameter problem
            logger.debug(
                "MCP %s: probe '%s' succeeded — original error is a real param error",
                server_name, probe_tool,
            )
            return False
        except Exception as probe_exc:
            probe_str = str(probe_exc)
            if any(
                re.search(rf"(?<!\d){re.escape(str(code))}(?!\d)", probe_str)
                for code in dead_codes
            ):
                logger.warning(
                    "MCP %s: probe '%s' also returned session-dead error — session is dead",
                    server_name, probe_tool,
                )
                return True
            # Probe failed for unrelated reason (e.g. tool not found) — inconclusive
            logger.warning(
                "MCP %s: probe '%s' failed with unexpected error '%s' — not reconnecting",
                server_name, probe_tool, probe_exc,
            )
            return False

    async def _check_session_health(self, server) -> bool:
        """
        Spec 024: return True if the session is alive.

        If health_probe_tool is configured, actively calls it to detect zombie
        sessions (Pattern B: SSE stream open but server-side state gone).
        Falls back to checking conn.session is not None for unconfigured servers.
        """
        import re
        conn = self._connections.get(server.name)
        if conn is None or conn.session is None:
            return False

        probe_tool = (server.health_probe_tool or "").strip()
        if not probe_tool:
            return True  # no probe configured — assume alive (existing behaviour)

        dead_codes: list = server.session_dead_error_codes or []
        try:
            await conn.session.call_tool(probe_tool, {})
            return True
        except Exception as exc:
            # Pattern A: transport-level error on probe → session definitely dead
            if _is_session_dead_error(exc):
                logger.warning(
                    "MCP %s: health probe raised transport error — session dead",
                    server.name,
                )
                return False
            # Pattern B: JSON-RPC dead-session code on probe
            if dead_codes:
                exc_str = str(exc)
                if any(
                    re.search(rf"(?<!\d){re.escape(str(code))}(?!\d)", exc_str)
                    for code in dead_codes
                ):
                    logger.warning(
                        "MCP %s: health probe failed with session-dead code — session dead",
                        server.name,
                    )
                    return False
            # Probe failed for unrelated reason — don't treat as dead
            return True

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
                logger.warning("MCP %s: %s — reconnecting and retrying", server_name, type(exc).__name__)
            # Pattern B: JSON-RPC dead session (Spec 024)
            elif await self._is_jsonrpc_session_dead(server_name, exc, conn):
                logger.warning("MCP %s: JSON-RPC session dead — reconnecting and retrying", server_name)
            else:
                raise  # real error, don't reconnect

            await self._reconnect_server_async(server_name)
            conn = self._connections.get(server_name)
            if conn is None or conn.session is None:
                raise MCPTimeoutError(f"MCP {server_name}: reconnect succeeded but session unavailable")
            # Retry exactly once — no dead-session detection on retry to prevent loops
            result = await conn.session.call_tool(tool_name, args)
            return {"content": extract_tool_content(result)}

    async def _read_resource_async(self, server_name: str, uri: str) -> str:
        conn = self._connections.get(server_name)
        if conn is None or conn.session is None:
            raise MCPTimeoutError(f"No active connection to MCP server: {server_name}")
        try:
            result = await conn.session.read_resource(uri)
            return extract_resource_content(result)
        except Exception as exc:
            # Pattern A
            if _is_session_dead_error(exc):
                logger.warning("MCP %s: %s on read_resource — reconnecting and retrying", server_name, type(exc).__name__)
            # Pattern B (Spec 024)
            elif await self._is_jsonrpc_session_dead(server_name, exc, conn):
                logger.warning("MCP %s: JSON-RPC session dead on read_resource — reconnecting and retrying", server_name)
            else:
                raise
            await self._reconnect_server_async(server_name)
            conn = self._connections.get(server_name)
            if conn is None or conn.session is None:
                raise MCPTimeoutError(f"MCP {server_name}: reconnect succeeded but session unavailable")
            result = await conn.session.read_resource(uri)
            return extract_resource_content(result)

    async def _refresh_server_async(self, server_name: str) -> None:
        conn = self._connections.get(server_name)
        if conn is None or conn.session is None:
            return
        await self._discover_tools(server_name, conn.session)

    async def _discover_tools(self, server_name: str, session: Any) -> None:
        try:
            tools_result = await session.list_tools()
            entries = [
                MCPToolEntry(
                    server_name=server_name,
                    tool_name=tool.name,
                    description=tool.description or "",
                    input_schema=(
                        tool.inputSchema
                        if isinstance(tool.inputSchema, dict)
                        else {}
                    ),
                )
                for tool in tools_result.tools
            ]
            get_registry().register(server_name, entries)
            logger.info(
                "MCP server %s: discovered %d tools: %s",
                server_name,
                len(entries),
                [e.llm_function_name for e in entries],
            )
        except Exception as exc:
            logger.error("MCP tool discovery failed for %s: %s", server_name, exc, exc_info=True)

    async def _update_db_status(
        self, server_name: str, status: str, error: str = ""
    ) -> None:
        try:
            from asgiref.sync import sync_to_async
            await sync_to_async(self._update_db_status_sync)(server_name, status, error)
        except Exception as exc:
            logger.warning("Could not update MCPServer status: %s", exc)

    @staticmethod
    def _update_db_status_sync(server_name: str, status: str, error: str = "") -> None:
        from django.utils import timezone
        from agent.models import MCPServer

        update: dict = {"connection_status": status, "last_error": error}
        if status == "connected":
            update["last_connected_at"] = timezone.now()
        MCPServer.objects.filter(name=server_name).update(**update)
        if status == "error":
            MCPServer.objects.filter(name=server_name).update(enabled=False)
