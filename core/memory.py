from pgvector.django import CosineDistance
from django.conf import settings


def embed_text(text: str) -> list[float]:
    """Generate embedding via litellm."""
    import litellm
    response = litellm.embedding(model="openai/text-embedding-3-small", input=[text])
    return response.data[0]["embedding"]


def search_memories(embedding: list[float], limit: int = 5):
    """Search memory records by cosine similarity (global — no agent filter)."""
    from agent.models import Memory
    return Memory.objects.all().order_by(CosineDistance("embedding", embedding))[:limit]
