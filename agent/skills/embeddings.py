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
        text = skill_md.read_text(encoding="utf-8-sig")
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


def embed_all_skills(
    native_only: bool = False,
    force: bool = False,
    platform: str | None = None,
) -> list[str]:
    """
    Scan trusted skill source directories, embed each skill whose content has
    changed, and upsert into SkillEmbedding.

    Spec 023:
    - native_only=True: only scans agent/workspace/skills/ (used at startup to
      avoid latency from large external skill collections).
    - native_only=False: scans all trusted dirs from all_skill_dirs().
    - Untrusted dirs are always skipped with a warning.
    - force=True: re-embeds all skills regardless of content hash.

    Spec 026:
    - platform: when set, only embeds skills whose `platforms:` frontmatter
      includes this platform (or have no `platforms:` field at all), and skips
      skills that are disabled for the given platform.

    Returns list of skill names actually embedded (changed or forced).
    """
    from agent.skills.discovery import (
        all_skill_dirs, _native_dir, iter_skill_dirs,
        _read_skill_name, skill_matches_platform, get_disabled_skills,
    )

    if native_only:
        dirs_to_scan = [(_native_dir(), True)]
    else:
        dirs_to_scan = [
            (src.path, src.trusted)
            for src in all_skill_dirs(check_db_trust=True)
        ]

    disabled = get_disabled_skills(platform=platform)
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

        for skill_dir in iter_skill_dirs(Path(skills_dir)):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            name = _read_skill_name(skill_md)
            if name in seen_names:
                continue  # shadowed by higher-precedence dir

            # Spec 026: platform filtering
            if platform is not None:
                try:
                    text = skill_md.read_text(encoding="utf-8-sig")
                    meta: dict = {}
                    if text.startswith("---"):
                        parts = text.split("---", 2)
                        if len(parts) >= 3:
                            meta = yaml.safe_load(parts[1]) or {}
                    if not skill_matches_platform(meta, platform):
                        continue
                except Exception:
                    pass

            if name in disabled:
                continue

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


def _read_category_description(category_dir: Path) -> str:
    """
    Spec 026: Read the DESCRIPTION.md for a category directory.
    Returns the `description` frontmatter field, or empty string if absent.
    """
    desc_md = category_dir / "DESCRIPTION.md"
    if not desc_md.exists():
        return ""
    try:
        text = desc_md.read_text(encoding="utf-8-sig")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                meta = yaml.safe_load(parts[1]) or {}
                return (meta.get("description") or "").strip()
    except Exception:
        pass
    return ""


def skill_catalog_for_prompt(
    base_dir: Path | None = None,
    platform: str | None = None,
) -> str:
    """
    Spec 026: Build a Tier 0 + Tier 1 skill catalog for system prompt injection.

    Tier 0: Category summary — "Available skill categories: cim (4 skills): ..."
    Tier 1: Per-skill bullet — "**name**: description"

    When base_dir is given, scans only that directory (for testing).
    Otherwise uses all trusted skill source directories via collect_all_skills().

    Skills without a category are listed in Tier 1 but excluded from Tier 0.
    """
    from agent.models import Skill
    from agent.skills.discovery import collect_all_skills, _get_category_from_path

    try:
        disabled_db = set(Skill.objects.filter(enabled=False).values_list("name", flat=True))
    except Exception:
        disabled_db = set()

    try:
        if base_dir is not None:
            # Test mode: scan base_dir directly without DB trust checks
            from agent.skills.discovery import iter_skill_dirs, _read_skill_name, skill_matches_platform, get_disabled_skills
            disabled_env = get_disabled_skills(platform=platform)
            all_skills: list[dict] = []
            for skill_dir in iter_skill_dirs(base_dir):
                skill_md = skill_dir / "SKILL.md"
                name = _read_skill_name(skill_md)
                category = _get_category_from_path(skill_md, base_dir)
                try:
                    text = skill_md.read_text(encoding="utf-8-sig")
                    meta: dict = {}
                    if text.startswith("---"):
                        parts = text.split("---", 2)
                        if len(parts) >= 3:
                            meta = yaml.safe_load(parts[1]) or {}
                    description = (meta.get("description") or "").strip()
                    if platform is not None and not skill_matches_platform(meta, platform):
                        continue
                except Exception:
                    description = ""
                if name in disabled_db or name in disabled_env:
                    continue
                all_skills.append({
                    "name": name,
                    "description": description,
                    "skill_dir": skill_dir,
                    "source_dir": base_dir,
                    "trusted": True,
                    "category": category,
                })
        else:
            raw_skills = collect_all_skills(check_db_trust=True, platform=platform)
            all_skills = []
            for skill_info in raw_skills:
                if not skill_info["trusted"]:
                    continue
                name = skill_info["name"]
                if name in disabled_db:
                    continue
                skill_md = skill_info["skill_dir"] / "SKILL.md"
                try:
                    text = skill_md.read_text(encoding="utf-8-sig")
                    meta = {}
                    if text.startswith("---"):
                        parts = text.split("---", 2)
                        if len(parts) >= 3:
                            meta = yaml.safe_load(parts[1]) or {}
                    description = (meta.get("description") or "").strip()
                except Exception:
                    description = ""
                all_skills.append({**skill_info, "description": description})
    except Exception:
        return ""

    if not all_skills:
        return ""

    # ── Tier 0: category summary ──────────────────────────────────────────
    # Group categorised skills; collect base_dir → category_dir mapping
    category_skills: dict[str, list[dict]] = {}
    uncategorised: list[dict] = []
    for s in all_skills:
        cat = s.get("category")
        if cat:
            category_skills.setdefault(cat, []).append(s)
        else:
            uncategorised.append(s)

    tier0_lines: list[str] = []
    if category_skills:
        tier0_lines.append("Available skill categories:")
        for cat in sorted(category_skills):
            count = len(category_skills[cat])
            # Try to read DESCRIPTION.md from the category dir
            # skill_dir is base_dir/cat/skill-name, so category dir = skill_dir.parent
            sample_skill_dir = category_skills[cat][0]["skill_dir"]
            cat_dir = sample_skill_dir.parent
            desc = _read_category_description(cat_dir)
            count_str = f"{count} skill{'s' if count != 1 else ''}"
            if desc:
                tier0_lines.append(f"- {cat} ({count_str}): {desc}")
            else:
                tier0_lines.append(f"- {cat} ({count_str})")

    # ── Tier 1: per-skill bullets ─────────────────────────────────────────
    skill_lines: list[str] = []
    for s in all_skills:
        if s["description"]:
            skill_lines.append(f"- **{s['name']}**: {s['description']}")

    if not skill_lines:
        return ""

    parts: list[str] = []
    if tier0_lines:
        parts.append("\n".join(tier0_lines))
    parts.append("## Available Skills\n\n" + "\n".join(skill_lines))
    parts.append(
        "When a user request matches a skill above, "
        "the skill's full instructions will be loaded automatically."
    )
    return "\n\n".join(parts)


def build_skill_catalog(platform: str | None = None) -> str:
    """
    Return a compact skill catalog for injection into the system prompt (Spec 022).

    Spec 026: Delegates to skill_catalog_for_prompt() which includes Tier 0
    category summary and Tier 1 per-skill bullets. Accepts optional `platform`
    to filter skills by interface.

    Spec 023: spans all trusted skill source directories.
    Excludes disabled and untrusted skills.
    """
    return skill_catalog_for_prompt(platform=platform)

