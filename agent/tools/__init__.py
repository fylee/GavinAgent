from agent.tools.base import BaseTool, ToolResult, ApprovalPolicy, ToolTimeoutError
from agent.tools.file import FileReadTool, FileWriteTool
from agent.tools.shell import ShellTool
from agent.tools.api import ApiGetTool, ApiPostTool
from agent.tools.web import WebReadTool

__all__ = [
    "BaseTool",
    "ToolResult",
    "ApprovalPolicy",
    "ToolTimeoutError",
    "FileReadTool",
    "FileWriteTool",
    "ShellTool",
    "ApiGetTool",
    "ApiPostTool",
    "WebReadTool",
]

# Registry: name -> tool instance
_REGISTRY: dict[str, "BaseTool"] = {}


def _register(*tools: "BaseTool") -> None:
    for tool in tools:
        _REGISTRY[tool.name] = tool


def get_tool(name: str) -> "BaseTool | None":
    return _REGISTRY.get(name)


def all_tools() -> dict[str, "BaseTool"]:
    return dict(_REGISTRY)


def _init_registry() -> None:
    _register(
        FileReadTool(),
        FileWriteTool(),
        ShellTool(),
        ApiGetTool(),
        ApiPostTool(),
        WebReadTool(),
    )


_init_registry()
