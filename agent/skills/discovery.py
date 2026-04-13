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
    """Return all skill directories (subdirs with a SKILL.md) inside base."""
    if not base.exists():
        return []
    return sorted(
        d for d in base.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )


def collect_all_skills(check_db_trust: bool = True) -> list[dict]:
    """
    Collect all skills across all source directories, respecting precedence.

    Returns a list of dicts:
        {
            "name": str,
            "skill_dir": Path,
            "source_dir": Path,
            "trusted": bool,
        }

    Name collisions: first occurrence (highest precedence) wins; subsequent
    entries for the same name are shadowed (logged, not returned).
    """
    sources = all_skill_dirs(check_db_trust=check_db_trust)
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

            seen_names[name] = src.path
            result.append({
                "name": name,
                "skill_dir": skill_dir,
                "source_dir": src.path,
                "trusted": src.trusted,
            })

    return result


def _read_skill_name(skill_md: Path) -> str:
    """Fast read of the skill name from SKILL.md frontmatter."""
    try:
        import yaml
        text = skill_md.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                meta = yaml.safe_load(parts[1]) or {}
                return meta.get("name") or skill_md.parent.name
    except Exception:
        pass
    return skill_md.parent.name
