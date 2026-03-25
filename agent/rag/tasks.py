"""Celery task for async document ingestion."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def ingest_document_task(self, document_id: str) -> str | None:
    """Ingest a KnowledgeDocument asynchronously."""
    from agent.models import KnowledgeDocument
    from agent.rag.ingest import ingest_document

    try:
        doc = KnowledgeDocument.objects.get(id=document_id)
    except KnowledgeDocument.DoesNotExist:
        logger.warning("KnowledgeDocument %s not found, skipping ingestion.", document_id)
        return None

    doc.status = KnowledgeDocument.Status.PROCESSING
    doc.save(update_fields=["status"])

    try:
        chunk_count = ingest_document(doc)
        doc.status = KnowledgeDocument.Status.READY
        doc.save(update_fields=["status"])
        logger.info("Document %s ingested: %d chunks", doc.title, chunk_count)
        return document_id
    except Exception as exc:
        doc.status = KnowledgeDocument.Status.ERROR
        doc.metadata["error"] = str(exc)
        doc.save(update_fields=["status", "metadata"])
        logger.exception("Failed to ingest document %s", doc.title)
        raise self.retry(exc=exc, countdown=10)
