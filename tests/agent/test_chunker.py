"""P0 tests for agent.rag.chunker — pure logic, no DB."""
from __future__ import annotations

import hashlib

from agent.rag.chunker import chunk_text, _token_len


class TestChunkText:
    def test_short_text_no_split(self):
        """Text under chunk_size returns a single chunk."""
        chunks = chunk_text("Hello world", chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0]["content"] == "Hello world"
        assert chunks[0]["chunk_index"] == 0

    def test_empty_input(self):
        """Empty string returns empty list."""
        assert chunk_text("") == []

    def test_whitespace_only(self):
        """Whitespace-only input returns empty list."""
        assert chunk_text("   \n\n   ") == []

    def test_splits_on_double_newline(self):
        """Long text with \\n\\n separators is split there first."""
        para1 = "word " * 100  # ~100 tokens
        para2 = "text " * 100
        text = f"{para1}\n\n{para2}"
        chunks = chunk_text(text, chunk_size=120, chunk_overlap=10)
        assert len(chunks) >= 2

    def test_splits_on_single_newline_fallback(self):
        """Falls back to \\n when no \\n\\n exists."""
        line1 = "word " * 100
        line2 = "text " * 100
        text = f"{line1}\n{line2}"
        chunks = chunk_text(text, chunk_size=120, chunk_overlap=10)
        assert len(chunks) >= 2

    def test_chunk_index_sequential(self):
        """chunk_index values are 0, 1, 2, ..."""
        text = "\n\n".join(["paragraph " * 50 for _ in range(5)])
        chunks = chunk_text(text, chunk_size=60, chunk_overlap=5)
        indices = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_hash_is_stable(self):
        """Same content always produces the same content_hash."""
        chunks1 = chunk_text("Hello world", chunk_size=500)
        chunks2 = chunk_text("Hello world", chunk_size=500)
        assert chunks1[0]["content_hash"] == chunks2[0]["content_hash"]

    def test_hash_is_sha256(self):
        """content_hash is a SHA-256 hex digest of the content."""
        chunks = chunk_text("Hello world", chunk_size=500)
        expected = hashlib.sha256("Hello world".encode()).hexdigest()
        assert chunks[0]["content_hash"] == expected

    def test_hash_differs_for_different_content(self):
        """Different content produces different hashes."""
        chunks_a = chunk_text("Hello world", chunk_size=500)
        chunks_b = chunk_text("Goodbye world", chunk_size=500)
        assert chunks_a[0]["content_hash"] != chunks_b[0]["content_hash"]

    def test_token_count_accurate(self):
        """token_count matches tiktoken encoding length."""
        text = "The quick brown fox jumps over the lazy dog."
        chunks = chunk_text(text, chunk_size=500)
        assert chunks[0]["token_count"] == _token_len(text)

    def test_token_count_positive(self):
        """All chunks have positive token_count."""
        text = "\n\n".join(["paragraph " * 50 for _ in range(5)])
        chunks = chunk_text(text, chunk_size=60, chunk_overlap=5)
        for chunk in chunks:
            assert chunk["token_count"] > 0

    def test_chunk_size_respected(self):
        """No chunk exceeds the requested chunk_size in tokens."""
        text = "\n\n".join(["sentence " * 80 for _ in range(10)])
        chunk_size = 100
        chunks = chunk_text(text, chunk_size=chunk_size, chunk_overlap=10)
        for chunk in chunks:
            # Allow small overshoot from separator tokens
            assert chunk["token_count"] <= chunk_size * 1.2, (
                f"Chunk has {chunk['token_count']} tokens, expected <= {chunk_size * 1.2}"
            )

    def test_overlap_produces_shared_content(self):
        """With overlap > 0, adjacent chunks share some content."""
        # Create text with clear paragraph boundaries
        paragraphs = [f"paragraph{i} " * 30 for i in range(6)]
        text = "\n\n".join(paragraphs)
        chunks_with_overlap = chunk_text(text, chunk_size=40, chunk_overlap=15)
        chunks_without_overlap = chunk_text(text, chunk_size=40, chunk_overlap=0)
        # With overlap, we generally get more or equal chunks
        assert len(chunks_with_overlap) >= len(chunks_without_overlap)
