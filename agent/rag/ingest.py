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
    """Fetch page content: try Jina reader first, fall back to trafilatura."""
    import httpx

    timeout = getattr(settings, "AGENT_TOOL_TIMEOUT_SECONDS", 30)

    # 1. Try Jina reader
    try:
        resp = httpx.get(
            f"https://r.jina.ai/{url}",
            headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
            timeout=timeout,
            follow_redirects=True,
        )
        if resp.status_code < 400:
            return resp.text
    except Exception:
        pass

    # 2. Fallback: direct fetch + trafilatura
    import trafilatura

    resp = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
    )
    resp.raise_for_status()
    extracted = trafilatura.extract(resp.text, include_links=True, include_tables=True, output_format="txt")
    if not extracted:
        raise ValueError(f"Could not extract content from {url}")
    return extracted


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
