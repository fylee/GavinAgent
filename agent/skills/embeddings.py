"""
Skill embedding helpers — semantic indexing and retrieval for workspace skills.

Skills are embedded once (or on change) and stored in SkillEmbedding.
find_relevant_skills() uses cosine similarity to select skills above threshold.

Spec 021/022: GavinAgent extension fields (triggers, examples, trigger_patterns,
version, approval_required) must live inside the `metadata` key, not at the top
level.  _parse_metadata_list() reads pipe-separated strings from metadata.

Spec 023: embed_all_skills() / embed_skill_dir() scan all trusted source dirs via
discovery.all_skill_dirs().  Untrusted dirs are skipped with a warning.
build_skill_catalog() also spans all trusted dirs.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import yaml
from django.conf import settings

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = getattr(settings, "AGENT_SKILL_SIMILARITY_THRESHOLD", 0.55)
EMBED_TEXT_MAX_CHARS = 500


def _parse_metadata_list(metadata: dict, key: str) -> list[str]:
    """Read a separated string from metadata as a list.

    Spec 021: GavinAgent extension fields are stored as separated strings
    inside the `metadata` map (metadata values must be strings, not lists).
    Also accepts legacy list values for backwards compatibility during migration.

    `trigger_patterns` uses ';;' as separator (since regex patterns contain '|').
    All other fields use '|' as separator.
    """
    raw = metadata.get(key)
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    sep = ";;" if key == "trigger_patterns" else "|"
    return [item.strip() for item in str(raw).split(sep) if item.strip()]


def _skill_embed_text(
    name: str,
    description: str,
    body: str,
    examples: list[str] | None = None,
    triggers: list[str] | None = None,
) -> str:
    """Build the text that gets embedded for a skill."""
    parts = [f"{name}: {description}"]
    if triggers:
        parts.append("Keywords: " + ", ".join(triggers[:20]))
    if examples:
        parts.append("Example requests: " + "; ".join(examples[:10]))
    excerpt = body[:EMBED_TEXT_MAX_CHARS].strip()
    if excerpt:
        parts.append(excerpt)
    return "\n\n".join(parts)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _embed_skill_dir(skill_dir: Path) -> str | None:
    """
    Embed a single skill directory and upsert into SkillEmbedding.

    Returns the skill name if embedded/updated, or None if unchanged or failed.
    Used by embed_all_skills(), embed_skill_dir() public API, and embed_skills command.
    """
    from agent.models import SkillEmbedding
    from core.memory import embed_text

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

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
        nested_meta = meta.get("metadata", {}) or {}

        # Spec 021: read from metadata; legacy top-level also supported
        examples = _parse_metadata_list(nested_meta, "examples") or _parse_metadata_list(meta, "examples")
        triggers = _parse_metadata_list(nested_meta, "triggers") or _parse_metadata_list(meta, "triggers")

        embed_input = _skill_embed_text(name, description, body, examples, triggers)
        chash = _content_hash(embed_input)

        existing = SkillEmbedding.objects.filter(skill_name=name).first()
        if existing and existing.content_hash == chash:
            return None  # unchanged

        vector = embed_text(embed_input)
        SkillEmbedding.objects.update_or_create(
            skill_name=name,
            defaults={"embedding": vector, "content_hash": chash},
        )
        logger.info("Embedded skill: %s", name)
        return name

    except Exception as exc:
        logger.warning("Failed to embed skill %s: %s", skill_dir.name, exc)
        return None


def embed_all_skills(native_only: bool = False, force: bool = False) -> list[str]:
    """
    Scan trusted skill source directories, embed each skill whose content has
    changed, and upsert into SkillEmbedding.

    Spec 023:
    - native_only=True: only scans agent/workspace/skills/ (used at startup to
      avoid latency from large external skill collections).
    - native_only=False: scans all trusted dirs from all_skill_dirs().
    - Untrusted dirs are always skipped with a warning.
    - force=True: re-embeds all skills regardless of content hash.

    Returns list of skill names actually embedded (changed or forced).
    """
    from agent.skills.discovery import all_skill_dirs, _native_dir

    if native_only:
        from agent.skills.discovery import iter_skill_dirs
        dirs_to_scan = [((_native_dir()), True)]
    else:
        dirs_to_scan = [
            (src.path, src.trusted)
            for src in all_skill_dirs(check_db_trust=True)
        ]

    processed: list[str] = []
    seen_names: set[str] = set()

    for skills_dir, trusted in dirs_to_scan:
        if not trusted:
            logger.warning(
                "Skipping untrusted skill source: %s — approve it in the Skills UI first",
                skills_dir,
            )
            continue
        if not Path(skills_dir).exists():
            continue

        for skill_dir in sorted(Path(skills_dir).iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            # Derive name quickly to check for precedence collision
            from agent.skills.discovery import _read_skill_name
            name = _read_skill_name(skill_md)
            if name in seen_names:
                continue  # shadowed by higher-precedence dir
            seen_names.add(name)

            if force:
                # Clear hash so _embed_skill_dir always re-embeds
                from agent.models import SkillEmbedding
                SkillEmbedding.objects.filter(skill_name=name).update(content_hash="")

            result = _embed_skill_dir(skill_dir)
            if result:
                processed.append(result)

    return processed


def find_relevant_skills(query: str, threshold: float = SIMILARITY_THRESHOLD) -> list[tuple[str, float]]:
    """
    Return (skill_name, similarity_score) pairs above the threshold for query.
    Returns empty list if embeddings are not available.
    Only returns skills from trusted source directories (Spec 023).
    """
    from agent.models import SkillEmbedding
    from core.memory import embed_text
    from pgvector.django import CosineDistance

    try:
        from agent.models import Skill

        disabled = set(
            Skill.objects.filter(enabled=False).values_list("name", flat=True)
        )
        query_vector = embed_text(query)
        qs = (
            SkillEmbedding.objects
            .annotate(distance=CosineDistance("embedding", query_vector))
            .filter(distance__lte=1 - threshold)
            .order_by("distance")
        )
        if disabled:
            qs = qs.exclude(skill_name__in=disabled)
        return [(r.skill_name, float(1 - r.distance)) for r in qs]
    except Exception as exc:
        logger.warning("Skill similarity search failed: %s", exc)
        return []


def build_skill_catalog() -> str:
    """
    Return a compact skill catalog for injection into the system prompt (Spec 022).
    Format: one bullet per enabled skill — "**name**: description"

    Spec 023: spans all trusted skill source directories.
    Excludes disabled and untrusted skills.
    """
    from agent.models import Skill
    from agent.skills.discovery import collect_all_skills

    try:
        disabled = set(Skill.objects.filter(enabled=False).values_list("name", flat=True))
    except Exception:
        disabled = set()

    lines: list[str] = []
    try:
        all_skills = collect_all_skills(check_db_trust=True)
    except Exception:
        return ""

    for skill_info in all_skills:
        if not skill_info["trusted"]:
            continue
        name = skill_info["name"]
        if name in disabled:
            continue
        skill_md = skill_info["skill_dir"] / "SKILL.md"
        try:
            text = skill_md.read_text(encoding="utf-8")
            meta: dict = {}
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
            description = (meta.get("description") or "").strip()
            if description:
                lines.append(f"- **{name}**: {description}")
        except Exception:
            continue

    if not lines:
        return ""
    return (
        "## Available Skills\n\n"
        + "\n".join(lines)
        + "\n\nWhen a user request matches a skill above, "
        "the skill's full instructions will be loaded automatically."
    )

