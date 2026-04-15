from __future__ import annotations

import asyncio
import logging
import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp import ClientSession

logger = logging.getLogger(__name__)


def _unwrap_error(exc: BaseException) -> str:
    """Return a human-readable error string, unwrapping ExceptionGroup if needed."""
    if isinstance(exc, BaseExceptionGroup):
        sub = exc.exceptions[0] if exc.exceptions else exc
        return _unwrap_error(sub)
    return str(exc)


class MCPTimeoutError(Exception):
    pass


class _ServerConnection:
    """Holds state for one live MCP server connection."""

    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self.ready: asyncio.Event = asyncio.Event()
        self.stop: asyncio.Event = asyncio.Event()


async def run_stdio_connection(
    server_name: str,
    command: str,
    env: dict,
    conn: _ServerConnection,
    on_ready: callable,
    on_error: callable,
    args: list[str] | None = None,
) -> None:
    """Long-running coroutine that maintains a stdio MCP connection."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters

    retries = 0
    max_retries = 3

    while retries < max_retries and not conn.stop.is_set():
        try:
            parts = shlex.split(command)
            cmd = parts[0] if parts else command
            cmd_args = args if args else (parts[1:] if len(parts) > 1 else [])
            params = StdioServerParameters(
                command=cmd,
                args=cmd_args,
                env=env or None,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    conn.session = session
                    conn.ready.set()
                    await on_ready(server_name, session)
                    await conn.stop.wait()
                    conn.session = None
                    conn.ready.clear()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("MCP stdio server %s error: %s", server_name, exc)
            conn.session = None
            conn.ready.clear()
            retries += 1
            if not conn.stop.is_set():
                if retries < max_retries:
                    await asyncio.sleep(5 * retries)
                else:
                    await on_error(server_name, _unwrap_error(exc))


async def run_sse_connection(
    server_name: str,
    url: str,
    headers: dict,
    conn: _ServerConnection,
    on_ready: callable,
    on_error: callable,
) -> None:
    """Long-running coroutine that maintains an SSE MCP connection.

    Retries indefinitely on transient errors (network blips, incomplete chunked
    reads, peer resets) with exponential backoff capped at 60 s.  Only calls
    on_error and exits when the stop signal is set or an unrecoverable error
    occurs after MAX_HARD_FAILURES consecutive failures without any successful
    connection.
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    MAX_HARD_FAILURES = 10   # give up only after this many consecutive failures
    consecutive_failures = 0

    while not conn.stop.is_set():
        try:
            async with sse_client(url, headers=headers) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    conn.session = session
                    conn.ready.set()
                    consecutive_failures = 0   # reset on successful connect
                    await on_ready(server_name, session)
                    await conn.stop.wait()
                    conn.session = None
                    conn.ready.clear()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            conn.session = None
            conn.ready.clear()
            consecutive_failures += 1
            if conn.stop.is_set():
                break
            if consecutive_failures >= MAX_HARD_FAILURES:
                logger.error(
                    "MCP SSE server %s: %d consecutive failures — giving up. Last error: %s",
                    server_name, consecutive_failures, exc,
                )
                await on_error(server_name, _unwrap_error(exc))
                return
            # Exponential backoff: 5s, 10s, 20s, 40s, capped at 60s
            delay = min(5 * (2 ** (consecutive_failures - 1)), 60)
            logger.warning(
                "MCP SSE server %s error (attempt %d/%d), retrying in %ds: %s",
                server_name, consecutive_failures, MAX_HARD_FAILURES, delay, _unwrap_error(exc),
            )
            await asyncio.sleep(delay)


def extract_tool_content(result) -> str:
    """Convert MCP tool result content to a plain string."""
    parts = []
    for item in result.content:
        if hasattr(item, "text"):
            parts.append(item.text)
        elif hasattr(item, "data"):
            mime = getattr(item, "mimeType", "binary")
            parts.append(f"[binary content: {mime}]")
    return "\n".join(parts)


def extract_resource_content(result) -> str:
    """Convert MCP resource result contents to a plain string."""
    parts = []
    for item in result.contents:
        if hasattr(item, "text"):
            parts.append(item.text)
        elif hasattr(item, "data"):
            parts.append(f"[binary resource]")
    return "\n".join(parts)
