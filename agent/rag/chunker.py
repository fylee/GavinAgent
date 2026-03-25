"""Recursive text splitter for RAG document chunking.

No external dependencies — uses tiktoken for token counting and a simple
recursive split strategy: \\n\\n → \\n → '. ' → ' '.
"""

from __future__ import annotations

import hashlib

import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def _token_len(text: str) -> int:
    return len(_ENCODING.encode(text))


def _split_text(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    separators: list[str] | None = None,
) -> list[str]:
    """Recursively split *text* into chunks of at most *chunk_size* tokens."""
    if separators is None:
        separators = ["\n\n", "\n", ". ", " "]

    if _token_len(text) <= chunk_size:
        return [text] if text.strip() else []

    # Find the best separator that actually appears in the text
    sep = separators[0] if separators else " "
    remaining_separators = separators[1:] if separators else []
    for s in separators:
        if s in text:
            sep = s
            remaining_separators = separators[separators.index(s) + 1 :]
            break

    parts = text.split(sep)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for part in parts:
        part_len = _token_len(part)
        # If adding this part would exceed the chunk size, flush current
        sep_len = _token_len(sep) if current else 0
        if current and current_len + sep_len + part_len > chunk_size:
            merged = sep.join(current)
            # If merged is still too large, recurse with finer separators
            if _token_len(merged) > chunk_size and remaining_separators:
                chunks.extend(
                    _split_text(merged, chunk_size, chunk_overlap, remaining_separators)
                )
            else:
                chunks.append(merged)
            # Overlap: keep trailing parts that fit within overlap budget
            overlap_parts: list[str] = []
            overlap_len = 0
            for p in reversed(current):
                p_len = _token_len(p)
                if overlap_len + p_len > chunk_overlap:
                    break
                overlap_parts.insert(0, p)
                overlap_len += p_len
            current = overlap_parts
            current_len = overlap_len

        current.append(part)
        current_len += part_len + (_token_len(sep) if len(current) > 1 else 0)

    # Flush remaining
    if current:
        merged = sep.join(current)
        if _token_len(merged) > chunk_size and remaining_separators:
            chunks.extend(
                _split_text(merged, chunk_size, chunk_overlap, remaining_separators)
            )
        else:
            chunks.append(merged)

    return [c for c in chunks if c.strip()]


def chunk_text(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[dict]:
    """Split *text* into chunks and return metadata for each.

    Returns a list of dicts:
        {"content": str, "chunk_index": int, "token_count": int, "content_hash": str}
    """
    raw_chunks = _split_text(text, chunk_size, chunk_overlap)
    result = []
    for i, content in enumerate(raw_chunks):
        result.append(
            {
                "content": content,
                "chunk_index": i,
                "token_count": _token_len(content),
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    return result
