from agent.tools.base import BaseTool, ToolResult, ApprovalPolicy, ToolTimeoutError

__all__ = [
    "BaseTool",
    "ToolResult",
    "ApprovalPolicy",
    "ToolTimeoutError",
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
    import importlib
    import inspect
    from pathlib import Path

    tools_dir = Path(__file__).parent
    for path in sorted(tools_dir.glob("*.py")):
        if path.stem in ("__init__", "base"):
            continue
        module = importlib.import_module(f"agent.tools.{path.stem}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseTool) and obj is not BaseTool and obj.__module__ == module.__name__:
                _register(obj())


_init_registry()
