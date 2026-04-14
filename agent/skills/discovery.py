"""
Skill source directory discovery — Spec 023.

all_skill_dirs() is the single source of truth for where skill directories live.
It replaces the three separate _skills_dir() / hardcoded path copies that existed
in embeddings.py, loader.py, and nodes.py.

Precedence order (highest → lowest):
  1. agent/workspace/skills/           ← GavinAgent native (always trusted)
  2. .agents/skills/                   ← project-level (npx skills add)
  3. ~/.agents/skills/                 ← user-level (npx skills add -g)
  4. ~/.claude/skills/                 ← Claude Code compatibility
  5. AGENT_EXTRA_SKILLS_DIRS entries   ← operator-configured extras (always trusted)

Name collision rule: higher-precedence directory wins; lower-precedence duplicate
skills are shadowed and a warning is logged.

Trust model:
  - Native dir and AGENT_EXTRA_SKILLS_DIRS entries are always trusted.
  - Standard dirs (.agents/, ~/.agents/, ~/.claude/) require a TrustedSkillSource
    DB record before their skills are embedded or injected into the LLM context.
    They are always discovered and shown in the UI.

Spec 026: Category directory support, platform filtering, and skill enable/disable.
  - Two-level directory layout (category/skill-name/SKILL.md) is supported.
  - skill_matches_platform() filters by frontmatter `platforms:` field.
  - get_disabled_skills() / _parse_platform_disabled() support env-driven disable.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class SkillSourceDir:
    path: Path
    trusted: bool       # True = always trusted; False = requires TrustedSkillSource record
    always_trusted: bool  # True = native or AGENT_EXTRA_SKILLS_DIRS (no DB check needed)


def _native_dir() -> Path:
    """Return agent/workspace/skills/ — the GavinAgent native skill directory."""
    return Path(settings.AGENT_WORKSPACE_DIR) / "skills"


def _is_trusted_in_db(resolved: Path) -> bool:
    """Check DB for a TrustedSkillSource record for this resolved path."""
    try:
        from agent.models import TrustedSkillSource
        return TrustedSkillSource.objects.filter(path=str(resolved)).exists()
    except Exception:
        return False


def all_skill_dirs(check_db_trust: bool = True) -> list[SkillSourceDir]:
    """
    Return all skill source directories in precedence order.

    Directories that do not exist on disk are silently skipped.
    Name collisions are resolved by taking the highest-precedence entry and
    logging a warning for any shadowed lower-precedence entries.

    Args:
        check_db_trust: if True, standard dirs are marked trusted/untrusted by
            querying TrustedSkillSource.  Set False during startup before the DB
            is available.
    """
    candidates: list[SkillSourceDir] = []

    # 1. Native (always trusted)
    native = _native_dir()
    candidates.append(SkillSourceDir(path=native, trusted=True, always_trusted=True))

    scan_standard = getattr(settings, "AGENT_SCAN_STANDARD_SKILL_DIRS", True)
    if scan_standard:
        # 2. Project-level .agents/skills/
        project_agents = Path(".agents") / "skills"
        candidates.append(SkillSourceDir(path=project_agents, trusted=False, always_trusted=False))

        # 3. User-level ~/.agents/skills/
        home = Path.home()
        candidates.append(SkillSourceDir(path=home / ".agents" / "skills", trusted=False, always_trusted=False))

        # 4. ~/.claude/skills/ (Claude Code compat)
        candidates.append(SkillSourceDir(path=home / ".claude" / "skills", trusted=False, always_trusted=False))

    # 5. AGENT_EXTRA_SKILLS_DIRS (always trusted — operator-configured)
    extra_dirs_cfg = getattr(settings, "AGENT_EXTRA_SKILLS_DIRS", []) or []
    for raw in extra_dirs_cfg:
        raw = raw.strip()
        if raw:
            candidates.append(SkillSourceDir(path=Path(raw), trusted=True, always_trusted=True))

    # Filter to existing dirs, resolve, check DB trust for non-always-trusted
    result: list[SkillSourceDir] = []
    seen_paths: set[Path] = set()
    for src in candidates:
        try:
            resolved = src.path.resolve()
        except Exception:
            continue
        if not resolved.exists():
            continue
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)

        if src.always_trusted:
            trusted = True
        elif check_db_trust:
            trusted = _is_trusted_in_db(resolved)
        else:
            trusted = False

        result.append(SkillSourceDir(path=resolved, trusted=trusted, always_trusted=src.always_trusted))

    return result


def iter_skill_dirs(base: Path) -> list[Path]:
    """
    Return all skill directories (subdirs with a SKILL.md) inside base.

    Spec 026: supports two-level directory layout (category/skill-name/SKILL.md).
    A directory that contains SKILL.md is always a skill dir; a directory that
    lacks SKILL.md is treated as a category container and its subdirs are scanned
    one level deeper.
    """
    if not base.exists():
        return []
    result: list[Path] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / "SKILL.md").exists():
            # Flat layout: base/skill-name/SKILL.md
            result.append(entry)
        else:
            # Possibly a category dir — scan one level deeper
            for sub in sorted(entry.iterdir()):
                if sub.is_dir() and (sub / "SKILL.md").exists():
                    result.append(sub)
    return result


def _get_category_from_path(skill_path: Path, base_dir: Path) -> str | None:
    """
    Spec 026: Extract the category name from a skill SKILL.md path.

    Two-level layout: base_dir/category/skill-name/SKILL.md → "category"
    Flat layout:      base_dir/skill-name/SKILL.md            → None
    """
    try:
        rel = skill_path.relative_to(base_dir)
        # rel.parts = ("category", "skill-name", "SKILL.md") → len >= 3
        if len(rel.parts) >= 3:
            return rel.parts[0]
    except ValueError:
        pass
    return None


def skill_matches_platform(frontmatter: dict, platform: str | None) -> bool:
    """
    Spec 026: Return True if the skill is compatible with the given platform.

    Rules:
      - No `platforms` field → matches all platforms (always True)
      - `platforms: []` (empty list) → matches all (treated as unset)
      - `platform=None` → no filtering, always True
      - Otherwise: True iff platform is in the frontmatter `platforms` list
    """
    platforms = frontmatter.get("platforms")
    if not platforms or platform is None:
        return True
    return platform in platforms


def _parse_platform_disabled(raw: str) -> dict[str, set[str]]:
    """
    Spec 026: Parse AGENT_PLATFORM_DISABLED_SKILLS into a dict.

    Format: "platform:skill-a,skill-b;platform2:skill-c"
    Malformed entries (no colon) are silently skipped.

    Returns: {"chat": {"skill-a", "skill-b"}, "copilot": {"skill-c"}}
    """
    result: dict[str, set[str]] = {}
    if not raw:
        return result
    for entry in raw.split(";"):
        entry = entry.strip()
        if ":" not in entry:
            continue
        platform, _, skills_str = entry.partition(":")
        platform = platform.strip()
        skills = {s.strip() for s in skills_str.split(",") if s.strip()}
        if platform and skills:
            result[platform] = skills
    return result


def get_disabled_skills(platform: str | None = None) -> set[str]:
    """
    Spec 026: Return the set of skill names disabled for the given platform.

    Resolution order:
      1. If AGENT_PLATFORM_DISABLED_SKILLS has an entry for `platform`, use it
         (platform-specific list completely replaces global list for that platform).
      2. Otherwise fall back to AGENT_DISABLED_SKILLS (global).
      3. If platform=None, always return the global list.
    """
    global_disabled = set(getattr(settings, "AGENT_DISABLED_SKILLS", []) or [])
    if platform is None:
        return global_disabled
    raw = getattr(settings, "AGENT_PLATFORM_DISABLED_SKILLS", "") or ""
    platform_map = _parse_platform_disabled(raw)
    return platform_map.get(platform, global_disabled)


def collect_all_skills(
    check_db_trust: bool = True,
    platform: str | None = None,
) -> list[dict]:
    """
    Collect all skills across all source directories, respecting precedence.

    Spec 026: Accepts optional `platform` to filter by frontmatter `platforms:`
    field and by platform-specific disabled skill settings.

    Returns a list of dicts:
        {
            "name": str,
            "skill_dir": Path,
            "source_dir": Path,
            "trusted": bool,
            "category": str | None,   # Spec 026
        }

    Name collisions: first occurrence (highest precedence) wins; subsequent
    entries for the same name are shadowed (logged, not returned).
    """
    import yaml

    sources = all_skill_dirs(check_db_trust=check_db_trust)
    disabled = get_disabled_skills(platform=platform)
    seen_names: dict[str, Path] = {}   # name → source_dir that owns it
    result: list[dict] = []

    for src in sources:
        for skill_dir in iter_skill_dirs(src.path):
            # Determine name from frontmatter or directory name
            skill_md = skill_dir / "SKILL.md"
            name = _read_skill_name(skill_md)

            if name in seen_names:
                logger.warning(
                    "Skill '%s' from %s is shadowed by higher-precedence %s",
                    name, skill_dir, seen_names[name],
                )
                continue

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
                    pass  # if unreadable, include it (fail open)

            # Spec 026: disabled skill filtering
            if name in disabled:
                logger.debug("Skill '%s' is disabled for platform=%s — skipping", name, platform)
                continue

            seen_names[name] = src.path
            result.append({
                "name": name,
                "skill_dir": skill_dir,
                "source_dir": src.path,
                "trusted": src.trusted,
                "category": _get_category_from_path(skill_md, src.path),
            })

    return result


def _read_skill_name(skill_md: Path) -> str:
    """Fast read of the skill name from SKILL.md frontmatter."""
    try:
        import yaml
        text = skill_md.read_text(encoding="utf-8-sig")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                meta = yaml.safe_load(parts[1]) or {}
                return meta.get("name") or skill_md.parent.name
    except Exception:
        pass
    return skill_md.parent.name
