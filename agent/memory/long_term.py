from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings

MEMORY_FILE_RELATIVE = "memory/MEMORY.md"


def _memory_path() -> Path:
    return Path(settings.AGENT_WORKSPACE_DIR) / MEMORY_FILE_RELATIVE


def _split_paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs (double-newline separated)."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def reembed(triggered_by: str = "auto") -> None:
    """Re-embed only paragraphs that have changed since last embed.

    Called automatically after the agent writes to MEMORY.md.
    """
    from agent.models import Memory, ReembedLog
    from core.memory import embed_text

    path = _memory_path()
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    paragraphs = _split_paragraphs(text)

    existing = {m.metadata.get("hash"): m for m in Memory.objects.filter(source="memory_md")}
    current_hashes = set()
    records_added = 0
    records_deleted = 0

    for para in paragraphs:
        h = _hash(para)
        current_hashes.add(h)
        if h not in existing:
            embedding = embed_text(para)
            Memory.objects.create(
                content=para,
                embedding=embedding,
                source="memory_md",
                metadata={"hash": h},
            )
            records_added += 1

    # Remove records no longer in the file
    for h, memory in existing.items():
        if h not in current_hashes:
            memory.delete()
            records_deleted += 1

    ReembedLog.objects.create(
        paragraph_count=len(paragraphs),
        records_added=records_added,
        records_deleted=records_deleted,
        triggered_by=triggered_by,
    )


def full_reembed(triggered_by: str = "manual") -> None:
    """Full reembed: diff all paragraphs, upsert new/changed, delete orphaned."""
    reembed(triggered_by=triggered_by)


def search_long_term(query: str, limit: int = 5) -> list[str]:
    """Return top-N relevant memory snippets by cosine similarity."""
    from core.memory import embed_text, search_memories

    embedding = embed_text(query)
    results = search_memories(embedding, limit=limit)
    return [m.content for m in results]
