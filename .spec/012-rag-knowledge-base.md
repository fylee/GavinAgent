# 012 — RAG Knowledge Base

## Goal

Give the agent the ability to search a curated knowledge base of documents
(Markdown, plain text, PDF, web pages) using retrieval-augmented generation (RAG),
so it can ground answers in project-specific or domain-specific content that isn't
part of its training data or system prompt.

## Background

The agent can already fetch live web pages (`web_read`) and search long-term memory
(`Memory` model via pgvector). However, neither covers the case of a **static,
curated corpus** — e.g. internal docs, runbooks, product specs, or reference
material — that the user wants the agent to consult before answering.

Long-term memory (`Memory`) is auto-populated from `MEMORY.md` and stores short
paragraphs the agent has learned during conversations. RAG documents are different:
they are **user-uploaded, multi-page sources** that need chunking, embedding, and
source attribution.

### Existing infrastructure we can reuse

| Component | Location | Reuse |
|---|---|---|
| pgvector + HNSW index | `agent/models.py` (`Memory`, `SkillEmbedding`) | Same pattern for `DocumentChunk` |
| `embed_text()` | `core/memory.py` | Shared embedding helper (text-embedding-3-small, 1536 dims) |
| `CosineDistance` queries | `core/memory.py` → `search_memories()` | Same ORM query pattern |
| `reembed_memory` command | `agent/management/commands/` | Pattern for `ingest_documents` command |
| `BaseTool` / tool registry | `agent/tools/` | New `rag_search` tool |
| HTMX UI patterns | `agent/templates/agent/` | New Knowledge Base page |

## Proposed Solution

### 1. Models (`agent/models.py`)

Two new models:

```python
class KnowledgeDocument(TimeStampedModel):
    """A source document in the knowledge base."""
    id = models.UUIDField(primary_key=True, default=uuid4)
    title = models.CharField(max_length=255)
    source_type = models.CharField(
        max_length=20,
        choices=[
            ("upload", "File Upload"),
            ("url", "Web URL"),
            ("text", "Pasted Text"),
        ],
    )
    source_url = models.URLField(blank=True)
    raw_content = models.TextField(help_text="Original full text")
    metadata = models.JSONField(default=dict)
    chunk_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)  # soft-disable without deleting

    def __str__(self):
        return self.title


class DocumentChunk(TimeStampedModel):
    """An embedded chunk of a KnowledgeDocument for vector search."""
    id = models.UUIDField(primary_key=True, default=uuid4)
    document = models.ForeignKey(
        KnowledgeDocument,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    content = models.TextField()
    embedding = VectorField(dimensions=1536)
    chunk_index = models.PositiveIntegerField()  # position within document
    token_count = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(max_length=64)  # SHA-256, for dedup on re-ingest

    class Meta:
        ordering = ["document", "chunk_index"]
        indexes = [
            HnswIndex(
                name="docchunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            )
        ]
```

### 2. Chunking (`agent/rag/chunker.py`)

A simple recursive text splitter:

- Target chunk size: **500 tokens** (~2000 chars)
- Overlap: **50 tokens** (~200 chars)
- Split hierarchy: `\n\n` → `\n` → `. ` → ` `
- Each chunk gets a SHA-256 `content_hash` for dedup on re-ingest
- Token counting via `tiktoken` (`cl100k_base`)

No LangChain dependency — implement the splitter directly (~40 lines).

### 3. Ingestion pipeline (`agent/rag/ingest.py`)

```python
def ingest_document(doc: KnowledgeDocument) -> int:
    """Chunk, embed, and store a document. Returns chunk count."""
```

Steps:
1. Extract text from `raw_content` (already text for upload/paste; for URLs, fetch via Jina reader)
2. Split into chunks via the chunker
3. Embed each chunk via `core.memory.embed_text()`
4. Bulk-create `DocumentChunk` rows (delete old chunks first for re-ingest)
5. Update `doc.chunk_count` and set `status = "ready"`

Support **batch embedding** to reduce API calls: `litellm.embedding(input=[list_of_chunks])`.

### 3a. Async ingestion via Celery

Ingestion runs as a **Celery task** (`agent/rag/tasks.py`):

```python
@shared_task
def ingest_document_task(document_id: str) -> None:
    doc = KnowledgeDocument.objects.get(id=document_id)
    doc.status = "processing"
    doc.save(update_fields=["status"])
    try:
        ingest_document(doc)
        doc.status = "ready"
    except Exception as e:
        doc.status = "error"
        doc.metadata["error"] = str(e)
    doc.save(update_fields=["status", "metadata"])
```

The `KnowledgeDocument` model gains a **`status`** field:

```python
status = models.CharField(
    max_length=20,
    choices=[
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("error", "Error"),
    ],
    default="pending",
)
```

UI flow:
- `KnowledgeCreateView` creates the document with `status="pending"` and dispatches `ingest_document_task.delay(doc.id)`
- The document row shows a status badge (spinner for pending/processing, green for ready, red for error)
- HTMX polls the row (`hx-trigger="every 2s"`) while status is `pending` or `processing`; stops polling once `ready` or `error`

### 4. File parsing

For the initial implementation, support:

| Format | Extraction |
|---|---|
| `.md`, `.txt` | Read as-is (UTF-8) |
| `.pdf` | `pymupdf` (fitz) — text extraction only, no OCR |
| URL | Jina reader (`r.jina.ai/`) — same as `web_read` tool |

Add formats later (`.docx`, `.csv`, `.html`) as needed.

### 5. Automatic context injection (like skills)

Knowledge chunks are **auto-injected into the system prompt** based on semantic
similarity to the user's query — the same pattern used for skill routing
(`_build_skills_section`).

New function in `agent/rag/retriever.py`:

```python
def retrieve_knowledge(query: str, limit: int = 5, threshold: float = 0.3) -> list[dict]:
    """Return relevant knowledge chunks for the user query.

    Returns list of {content, document_title, source_url, similarity}.
    Only searches active documents with status='ready'.
    """
```

Integration point — `_build_system_context()` in `agent/graph/nodes.py`:

```python
def _build_system_context(query: str, ...) -> str:
    # ... existing system prompt, skills section ...

    # Knowledge base context (auto-injected)
    knowledge_section = _build_knowledge_section(query)
    if knowledge_section:
        parts.append(knowledge_section)
```

New helper `_build_knowledge_section(query)` in `nodes.py`:

1. Calls `retrieve_knowledge(query)` with configured threshold and limit
2. If matches found, formats them as a system prompt section:

```
## Reference Knowledge

The following excerpts from your knowledge base are relevant to this query.
Use them to ground your answer. Cite the source when appropriate.

### From: {document_title}
{chunk_content}

### From: {document_title}
{chunk_content}
```

3. If no matches above threshold → section is omitted (no noise)

This is **selective** — only chunks that semantically match the query are injected,
just like how only relevant skills are promoted to "Active" and have their full
body injected.

The `knowledge_search` tool is **not needed** in this approach. The agent doesn't
need to decide when to search — relevant knowledge is always available in context
when it's relevant.

### 6. Management command (`agent/management/commands/ingest_documents.py`)

```bash
uv run python manage.py ingest_documents          # re-ingest all documents
uv run python manage.py ingest_documents --id=UUID # re-ingest one document
```

Useful for bulk re-embedding after model changes (same pattern as `reembed_memory`).

### 7. UI — Knowledge Base page (`/agent/knowledge/`)

Following existing HTMX patterns (like Skills, Tools, Memory pages):

**Main page: `knowledge.html`**
- List of documents with title, source type, chunk count, active status, created date
- "Add Document" button → inline form via HTMX

**Add form: `_knowledge_add_form.html`**
- Three tabs: Upload file / Paste URL / Paste text
- Title field (auto-populated from filename or page title)
- Submit via `hx-post` → triggers async ingestion → returns `_knowledge_row.html`

**Document row: `_knowledge_row.html`**
- Title, type badge, chunk count, status toggle, delete button
- Toggle active/inactive via `hx-patch`
- Delete via `hx-delete` with confirmation

**Views:**
- `KnowledgeListView` — list all documents
- `KnowledgeCreateView` — handle upload/URL/text, create `KnowledgeDocument`, trigger ingestion
- `KnowledgeToggleView` — toggle `is_active`
- `KnowledgeDeleteView` — delete document + cascading chunks

### 8. URL patterns (`agent/urls.py`)

```python
path("knowledge/",          KnowledgeListView.as_view(),   name="knowledge"),
path("knowledge/add/",      KnowledgeCreateView.as_view(), name="knowledge-add"),
path("knowledge/<uuid:pk>/toggle/", KnowledgeToggleView.as_view(), name="knowledge-toggle"),
path("knowledge/<uuid:pk>/delete/", KnowledgeDeleteView.as_view(), name="knowledge-delete"),
```

### 9. Settings (`config/settings/base.py`)

```python
# RAG settings
RAG_CHUNK_SIZE_TOKENS = config("RAG_CHUNK_SIZE_TOKENS", default=500, cast=int)
RAG_CHUNK_OVERLAP_TOKENS = config("RAG_CHUNK_OVERLAP_TOKENS", default=50, cast=int)
RAG_SEARCH_LIMIT = config("RAG_SEARCH_LIMIT", default=5, cast=int)
RAG_SIMILARITY_THRESHOLD = config("RAG_SIMILARITY_THRESHOLD", default=0.3, cast=float)
```

### 10. Navigation

Add "Knowledge" link to the agent sidebar nav (in `base_agent.html`), between
"Memory" and "MCP".

## Out of Scope

- **Conversational memory integration** — RAG documents are separate from `Memory`. No auto-ingestion from conversations.
- **Multi-tenant / per-agent knowledge bases** — all documents are global (shared across agents). Per-agent scoping can be added later via an FK.
- **OCR / image extraction** — PDF text extraction only; scanned PDFs won't work.
- **Scheduled re-ingestion from URLs** — users must manually re-ingest to refresh URL-sourced documents.
- **Hybrid search** (vector + full-text) — start with pure vector search; add `SearchVector` later if needed.
- **Chunk-level editing** — users manage documents, not individual chunks.
- **Embedding model selection** — hardcoded to `text-embedding-3-small` (same as Memory).

## Acceptance Criteria

- [ ] User can upload a `.md`, `.txt`, or `.pdf` file and it appears in the Knowledge Base list
- [ ] User can paste a URL and the page content is fetched, chunked, and embedded
- [ ] User can paste raw text and it is chunked and embedded
- [ ] Ingestion runs asynchronously via Celery; document status updates from `pending` → `processing` → `ready`
- [ ] UI shows real-time status with HTMX polling during ingestion
- [ ] Relevant knowledge chunks are automatically injected into the agent's system prompt based on query similarity
- [ ] Chunks from inactive documents or documents not in `ready` status are excluded
- [ ] Deleting a document cascades to its chunks
- [ ] `ingest_documents` management command re-embeds all or a specific document
- [ ] Knowledge Base page is accessible from the agent sidebar
- [ ] All operations work via HTMX without full page reloads

## Resolved Questions

1. **Async ingestion** — ✅ Yes, via Celery task. Document has a `status` field (`pending` → `processing` → `ready` / `error`). UI polls status via HTMX.
2. **Chunk size tuning** — ✅ 500 tokens default, configurable via `RAG_CHUNK_SIZE_TOKENS` setting.
3. **Automatic context injection** — ✅ Auto-injected into system prompt via embedding similarity (same pattern as skill routing). No explicit `knowledge_search` tool needed. Only chunks above `RAG_SIMILARITY_THRESHOLD` are injected.

## Open Questions

_None at this time._

## Implementation Notes

<!-- Filled in during or after implementation. -->
