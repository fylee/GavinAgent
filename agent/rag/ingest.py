"""Ingestion pipeline: chunk a KnowledgeDocument, embed, and store."""

from __future__ import annotations

import logging

import litellm
from django.conf import settings

from agent.rag.chunker import chunk_text

logger = logging.getLogger(__name__)

# Maximum texts per embedding API call
_BATCH_SIZE = 64


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts in one API call, returning vectors in order."""
    response = litellm.embedding(
        model="openai/text-embedding-3-small",
        input=texts,
    )
    # litellm returns data sorted by index
    sorted_data = sorted(response.data, key=lambda d: d["index"])
    return [d["embedding"] for d in sorted_data]


def _fetch_url_content(url: str) -> str:
    """Fetch page content via Jina reader, same approach as web_read tool."""
    import httpx

    reader_url = f"https://r.jina.ai/{url}"
    headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
    timeout = getattr(settings, "AGENT_TOOL_TIMEOUT_SECONDS", 30)
    resp = httpx.get(reader_url, headers=headers, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _extract_pdf_text(content_bytes: bytes) -> str:
    """Extract text from PDF bytes using pymupdf."""
    import pymupdf  # noqa: F401 — fitz is the pymupdf import

    doc = pymupdf.open(stream=content_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def ingest_document(doc) -> int:
    """Chunk, embed, and store a KnowledgeDocument. Returns chunk count.

    Accepts a KnowledgeDocument instance. Deletes old chunks before re-ingesting.
    """
    from agent.models import DocumentChunk

    text = doc.raw_content
    if not text or not text.strip():
        return 0

    chunk_size = getattr(settings, "RAG_CHUNK_SIZE_TOKENS", 500)
    chunk_overlap = getattr(settings, "RAG_CHUNK_OVERLAP_TOKENS", 50)

    # Split into chunks
    chunk_dicts = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not chunk_dicts:
        return 0

    # Batch embed
    all_texts = [c["content"] for c in chunk_dicts]
    all_embeddings: list[list[float]] = []
    for i in range(0, len(all_texts), _BATCH_SIZE):
        batch = all_texts[i : i + _BATCH_SIZE]
        all_embeddings.extend(_embed_batch(batch))

    # Delete old chunks and bulk-create new ones
    doc.chunks.all().delete()
    objs = []
    for chunk_dict, embedding in zip(chunk_dicts, all_embeddings):
        objs.append(
            DocumentChunk(
                document=doc,
                content=chunk_dict["content"],
                embedding=embedding,
                chunk_index=chunk_dict["chunk_index"],
                token_count=chunk_dict["token_count"],
                content_hash=chunk_dict["content_hash"],
            )
        )
    DocumentChunk.objects.bulk_create(objs)

    # Update document metadata
    doc.chunk_count = len(objs)
    doc.save(update_fields=["chunk_count"])

    logger.info("Ingested %d chunks for document %s (%s)", len(objs), doc.id, doc.title)
    return len(objs)
