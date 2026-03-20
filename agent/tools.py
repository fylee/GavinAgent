from __future__ import annotations
from typing import Callable

_REGISTRY: dict[str, Callable] = {}


def register(name: str):
    def decorator(fn: Callable):
        _REGISTRY[name] = fn
        return fn
    return decorator


def get_tool(name: str) -> Callable | None:
    return _REGISTRY.get(name)


def get_tools(names: list[str]) -> list[Callable]:
    return [_REGISTRY[n] for n in names if n in _REGISTRY]


# Example built-in tools
@register("echo")
def echo(text: str) -> str:
    """Echo the input text back."""
    return text
