from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ApprovalPolicy:
    AUTO = "auto"
    REQUIRES_APPROVAL = "requires_approval"


class ToolTimeoutError(Exception):
    pass


@dataclass
class ToolResult:
    output: Any
    error: str | None = None
    duration_ms: int = 0

    @property
    def success(self) -> bool:
        return self.error is None

    def as_dict(self) -> dict:
        if self.error:
            return {"error": self.error}
        return {"output": self.output}


class BaseTool(ABC):
    name: str
    description: str
    approval_policy: str = ApprovalPolicy.AUTO
    parameters: dict = field(default_factory=dict)

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Run the tool synchronously. Raise ToolTimeoutError on timeout."""

    def to_llm_schema(self) -> dict:
        """Return OpenAI-compatible function definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
