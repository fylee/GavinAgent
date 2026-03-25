"""Management command to ingest (or re-ingest) knowledge base documents."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from agent.models import KnowledgeDocument
from agent.rag.ingest import ingest_document


class Command(BaseCommand):
    help = "Ingest (chunk + embed) knowledge base documents. Re-ingests all by default."

    def add_arguments(self, parser):
        parser.add_argument(
            "--id",
            type=str,
            default=None,
            help="UUID of a single document to re-ingest.",
        )

    def handle(self, *args, **options):
        doc_id = options["id"]

        if doc_id:
            try:
                doc = KnowledgeDocument.objects.get(id=doc_id)
            except KnowledgeDocument.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Document {doc_id} not found."))
                return

            self.stdout.write(f"Ingesting: {doc.title} …")
            doc.status = KnowledgeDocument.Status.PROCESSING
            doc.save(update_fields=["status"])
            try:
                count = ingest_document(doc)
                doc.status = KnowledgeDocument.Status.READY
                doc.save(update_fields=["status"])
                self.stdout.write(self.style.SUCCESS(f"  ✓ {count} chunks"))
            except Exception as exc:
                doc.status = KnowledgeDocument.Status.ERROR
                doc.metadata["error"] = str(exc)
                doc.save(update_fields=["status", "metadata"])
                self.stderr.write(self.style.ERROR(f"  ✗ {exc}"))
            return

        docs = KnowledgeDocument.objects.all()
        if not docs.exists():
            self.stdout.write("No documents in the knowledge base.")
            return

        total_chunks = 0
        for doc in docs:
            self.stdout.write(f"Ingesting: {doc.title} …")
            doc.status = KnowledgeDocument.Status.PROCESSING
            doc.save(update_fields=["status"])
            try:
                count = ingest_document(doc)
                doc.status = KnowledgeDocument.Status.READY
                doc.save(update_fields=["status"])
                total_chunks += count
                self.stdout.write(self.style.SUCCESS(f"  ✓ {count} chunks"))
            except Exception as exc:
                doc.status = KnowledgeDocument.Status.ERROR
                doc.metadata["error"] = str(exc)
                doc.save(update_fields=["status", "metadata"])
                self.stderr.write(self.style.ERROR(f"  ✗ {exc}"))

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. {docs.count()} documents, {total_chunks} total chunks.")
        )
