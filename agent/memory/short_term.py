from __future__ import annotations

import json
from typing import Any

import redis
from django.conf import settings

MAX_MESSAGES = 20
TTL_SECONDS = 4 * 60 * 60  # 4 hours


def _get_redis() -> redis.Redis:
    return redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


class ShortTermMemory:
    """Redis-backed conversation context. Stores last N messages with TTL."""

    def __init__(self, run_id: str) -> None:
        self.key = f"agent:run:{run_id}:context"
        self._redis = _get_redis()

    def store(self, messages: list[dict[str, Any]]) -> None:
        """Replace the stored message list (keeps last MAX_MESSAGES)."""
        trimmed = messages[-MAX_MESSAGES:]
        self._redis.set(self.key, json.dumps(trimmed), ex=TTL_SECONDS)

    def append(self, message: dict[str, Any]) -> None:
        """Append a single message, evicting oldest if over limit."""
        existing = self.retrieve()
        existing.append(message)
        self.store(existing)

    def retrieve(self) -> list[dict[str, Any]]:
        """Return stored messages (empty list if key missing/expired)."""
        raw = self._redis.get(self.key)
        if not raw:
            return []
        return json.loads(raw)

    def clear(self) -> None:
        self._redis.delete(self.key)
