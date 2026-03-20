from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class SkillEntry:
    name: str
    description: str
    instructions: str
    approval_required: bool
    path: str
    handler: Callable | None = None


class SkillRegistry:
    """In-memory registry of loaded skills."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillEntry] = {}

    def register(self, entry: SkillEntry) -> None:
        self._skills[entry.name] = entry

    def get(self, name: str) -> SkillEntry | None:
        return self._skills.get(name)

    def all(self) -> dict[str, SkillEntry]:
        return dict(self._skills)

    def names(self) -> list[str]:
        return list(self._skills.keys())

    def to_llm_tools(self) -> list[dict]:
        """Return skill handler tools in OpenAI function schema format."""
        tools = []
        for entry in self._skills.values():
            if entry.handler is not None:
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"skill_{entry.name.replace('-', '_')}",
                            "description": entry.description,
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "input": {
                                        "type": "string",
                                        "description": "Input for the skill.",
                                    }
                                },
                                "required": ["input"],
                            },
                        },
                    }
                )
        return tools
