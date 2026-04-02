from __future__ import annotations

import re

_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _safe_function_name(name: str) -> str:
    """Sanitize a string to match Azure/OpenAI tool name pattern ^[a-zA-Z0-9_-]+$."""
    return _INVALID_CHARS.sub("_", name)


class MCPToolEntry:
    """Metadata for a single tool exposed by an MCP server."""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict,
    ) -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        self.namespaced_name = f"{server_name}__{tool_name}"
        # LLM-safe function name: spaces/dots → underscores
        self.llm_function_name = _safe_function_name(self.namespaced_name)
        self.description = description
        self.input_schema = input_schema

    def to_llm_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.llm_function_name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


class MCPToolRegistry:
    """In-memory registry of all tools discovered from connected MCP servers."""

    def __init__(self) -> None:
        self._tools: dict[str, MCPToolEntry] = {}

    def register(self, server_name: str, tools: list[MCPToolEntry]) -> None:
        # Remove old entries for this server before re-registering.
        self._tools = {
            k: v for k, v in self._tools.items() if v.server_name != server_name
        }
        for tool in tools:
            # Key by llm_function_name so the name the LLM returns maps back here.
            self._tools[tool.llm_function_name] = tool

    def unregister_server(self, server_name: str) -> None:
        self._tools = {
            k: v for k, v in self._tools.items() if v.server_name != server_name
        }

    def get(self, llm_function_name: str) -> MCPToolEntry | None:
        """Look up a tool by the sanitized name the LLM will return."""
        return self._tools.get(llm_function_name)

    def to_llm_schemas(self) -> list[dict]:
        return [entry.to_llm_schema() for entry in self._tools.values()]

    def all(self) -> dict[str, MCPToolEntry]:
        return dict(self._tools)


# Process-level singleton
_registry = MCPToolRegistry()


def get_registry() -> MCPToolRegistry:
    return _registry
