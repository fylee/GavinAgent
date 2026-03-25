"""Retrieve relevant knowledge chunks by semantic similarity."""

from __future__ import annotations

import logging

from django.conf import settings
from pgvector.django import CosineDistance

from core.memory import embed_text

logger = logging.getLogger(__name__)


def retrieve_knowledge(
    query: str,
    limit: int | None = None,
    threshold: float | None = None,
) -> list[dict]:
    """Return knowledge chunks relevant to *query*, ordered by similarity.

    Each result is a dict with keys:
        content, document_title, source_url, similarity
    Only searches active documents with status='ready'.
    """
    from agent.models import DocumentChunk, KnowledgeDocument

    if limit is None:
        limit = getattr(settings, "RAG_SEARCH_LIMIT", 5)
    if threshold is None:
        threshold = getattr(settings, "RAG_SIMILARITY_THRESHOLD", 0.3)

    query_embedding = embed_text(query)

    distance_expr = CosineDistance("embedding", query_embedding)

    chunks = (
        DocumentChunk.objects.filter(
            document__is_active=True,
            document__status=KnowledgeDocument.Status.READY,
        )
        .annotate(distance=distance_expr)
        .filter(distance__lte=1 - threshold)  # cosine distance ≤ 1 - threshold
        .select_related("document")
        .order_by("distance")[:limit]
    )

    results = []
    for chunk in chunks:
        results.append(
            {
                "content": chunk.content,
                "document_title": chunk.document.title,
                "source_url": chunk.document.source_url or "",
                "similarity": round(1 - chunk.distance, 4),
            }
        )
    return results
