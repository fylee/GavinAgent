from __future__ import annotations


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
        self.description = description
        self.input_schema = input_schema

    def to_llm_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.namespaced_name,
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
            self._tools[tool.namespaced_name] = tool

    def unregister_server(self, server_name: str) -> None:
        self._tools = {
            k: v for k, v in self._tools.items() if v.server_name != server_name
        }

    def get(self, namespaced_name: str) -> MCPToolEntry | None:
        return self._tools.get(namespaced_name)

    def to_llm_schemas(self) -> list[dict]:
        return [entry.to_llm_schema() for entry in self._tools.values()]

    def all(self) -> dict[str, MCPToolEntry]:
        return dict(self._tools)


# Process-level singleton
_registry = MCPToolRegistry()


def get_registry() -> MCPToolRegistry:
    return _registry
