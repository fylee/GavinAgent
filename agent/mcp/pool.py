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
        """Connect all enabled MCP servers. Called on Django/Celery startup."""
        future = asyncio.run_coroutine_threadsafe(self._start_all_async(), self._loop)
        try:
            future.result(timeout=5)
        except Exception as exc:
            logger.error("MCP pool start_all error: %s", exc)

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
            servers = MCPServer.objects.filter(enabled=True)
            for server in servers:
                await self._start_server_async(server)
        except Exception as exc:
            logger.error("MCP _start_all_async error: %s", exc)

    async def _start_server_async(self, server) -> None:
        name = server.name
        if name in self._connections:
            return  # already running

        conn = _ServerConnection()
        self._connections[name] = conn

        async def on_ready(srv_name: str, session: Any) -> None:
            await self._discover_tools(srv_name, session)
            await self._update_db_status(srv_name, "connected")

        async def on_error(srv_name: str, error: str) -> None:
            await self._update_db_status(srv_name, "error", error)
            self._connections.pop(srv_name, None)
            self._tasks.pop(srv_name, None)
            get_registry().unregister_server(srv_name)

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

    async def _call_tool_async(self, server_name: str, tool_name: str, args: dict) -> dict:
        conn = self._connections.get(server_name)
        if conn is None or conn.session is None:
            raise MCPTimeoutError(f"No active connection to MCP server: {server_name}")
        result = await conn.session.call_tool(tool_name, args)
        return {"content": extract_tool_content(result)}

    async def _read_resource_async(self, server_name: str, uri: str) -> str:
        conn = self._connections.get(server_name)
        if conn is None or conn.session is None:
            raise MCPTimeoutError(f"No active connection to MCP server: {server_name}")
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
                "MCP server %s: discovered %d tools", server_name, len(entries)
            )
        except Exception as exc:
            logger.error("MCP tool discovery failed for %s: %s", server_name, exc)

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
