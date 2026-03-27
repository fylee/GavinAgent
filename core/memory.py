from pgvector.django import CosineDistance
from django.conf import settings


def embed_text(text: str) -> list[float]:
    """Generate embedding via litellm (model controlled by EMBEDDING_MODEL setting)."""
    import litellm
    model = getattr(settings, "EMBEDDING_MODEL", "openai/text-embedding-3-small")
    response = litellm.embedding(model=model, input=[text])
    return response.data[0]["embedding"]


def search_memories(embedding: list[float], limit: int = 5):
    """Search memory records by cosine similarity (global — no agent filter)."""
    from agent.models import Memory
    return Memory.objects.all().order_by(CosineDistance("embedding", embedding))[:limit]
