"""
Skill embedding helpers — semantic indexing and retrieval for workspace skills.

Skills are embedded once (or on change) and stored in SkillEmbedding.
_build_skills_section() uses cosine similarity to decide which skills are relevant
to a given query instead of keyword/regex matching.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import yaml
from django.conf import settings

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = getattr(settings, "AGENT_SKILL_SIMILARITY_THRESHOLD", 0.35)
EMBED_TEXT_MAX_CHARS = 500


def _skills_dir() -> Path:
    return Path(settings.AGENT_WORKSPACE_DIR) / "skills"


def _skill_embed_text(name: str, description: str, body: str) -> str:
    """Build the text that gets embedded for a skill."""
    excerpt = body[:EMBED_TEXT_MAX_CHARS].strip()
    return f"{name}: {description}\n\n{excerpt}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def embed_all_skills() -> list[str]:
    """
    Scan workspace/skills/, embed each skill whose content has changed,
    and upsert into SkillEmbedding. Returns list of skill names processed.
    """
    from agent.models import SkillEmbedding
    from core.memory import embed_text

    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return []

    processed: list[str] = []

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            text = skill_md.read_text(encoding="utf-8")
            meta: dict = {}
            body = text
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
                    body = parts[2].strip()

            name = meta.get("name", skill_dir.name)
            description = meta.get("description", "")
            embed_input = _skill_embed_text(name, description, body)
            chash = _content_hash(embed_input)

            existing = SkillEmbedding.objects.filter(skill_name=name).first()
            if existing and existing.content_hash == chash:
                continue  # unchanged — skip

            vector = embed_text(embed_input)

            SkillEmbedding.objects.update_or_create(
                skill_name=name,
                defaults={"embedding": vector, "content_hash": chash},
            )
            processed.append(name)
            logger.info("Embedded skill: %s", name)

        except Exception as exc:
            logger.warning("Failed to embed skill %s: %s", skill_dir.name, exc)

    return processed


def find_relevant_skills(query: str, threshold: float = SIMILARITY_THRESHOLD) -> list[str]:
    """
    Return skill names whose embedding is above the similarity threshold for query.
    Returns empty list if embeddings are not available.
    """
    from agent.models import SkillEmbedding
    from core.memory import embed_text
    from pgvector.django import CosineDistance

    try:
        query_vector = embed_text(query)
        results = (
            SkillEmbedding.objects
            .annotate(distance=CosineDistance("embedding", query_vector))
            .filter(distance__lte=1 - threshold)
            .order_by("distance")
        )
        return [r.skill_name for r in results]
    except Exception as exc:
        logger.warning("Skill similarity search failed: %s", exc)
        return []
