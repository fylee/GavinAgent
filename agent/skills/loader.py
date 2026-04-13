from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Callable

import yaml

logger = logging.getLogger(__name__)


def _parse_skill_md(path: Path) -> dict:
    """Parse SKILL.md frontmatter and description body.

    Spec 023: appends rules/*.md content to 'instructions' at load time so the
    LLM receives complete execution instructions when a skill is injected.
    Spec 021 regression fix: reads approval_required from metadata.approval_required
    with top-level fallback; normalises "true"/"false" strings to bool.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()

    # Spec 023: append rules/*.md content (one level deep, sorted alphabetically)
    rules_dir = path.parent / "rules"
    if rules_dir.is_dir():
        rules_parts: list[str] = []
        for rules_file in sorted(rules_dir.glob("*.md")):
            try:
                content = rules_file.read_text(encoding="utf-8").strip()
                if content:
                    rules_parts.append(f"### {rules_file.stem}\n\n{content}")
            except Exception as exc:
                logger.warning("Could not read rules file %s: %s", rules_file, exc)
        if rules_parts:
            body = body + "\n\n---\n\n" + "\n\n---\n\n".join(rules_parts)

    meta["instructions"] = body

    # Spec 021 regression fix: approval_required migrated to metadata sub-key
    nested_meta = meta.get("metadata", {}) or {}
    raw_approval = (
        nested_meta.get("approval_required")
        or meta.get("approval_required")
        or False
    )
    if isinstance(raw_approval, str):
        raw_approval = raw_approval.lower() == "true"
    meta["_approval_required_resolved"] = bool(raw_approval)

    return meta


class SkillLoader:
    """Scans one or more skill source directories and loads skills into the registry."""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def load_all(self, registry: "SkillRegistry") -> list[str]:
        """Load/reload all skills from this loader's directory.
        Returns list of skill names loaded.
        """
        return self._load_dir(self.skills_dir, registry)

    def _load_dir(self, directory: Path, registry: "SkillRegistry") -> list[str]:
        from agent.skills.registry import SkillEntry

        loaded = []
        if not directory.exists():
            return loaded

        for skill_dir in sorted(directory.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            meta = _parse_skill_md(skill_md)
            if not meta.get("name"):
                continue

            handler: Callable | None = None
            handler_path = skill_dir / "handler.py"
            if handler_path.exists():
                handler = _load_handler(handler_path, meta["name"])

            entry = SkillEntry(
                name=meta["name"],
                description=meta.get("description", ""),
                instructions=meta.get("instructions", ""),
                approval_required=meta.get("_approval_required_resolved", False),
                path=str(skill_dir),
                handler=handler,
            )
            registry.register(entry)
            loaded.append(meta["name"])

        return loaded


def _load_handler(path: Path, skill_name: str) -> Callable | None:
    """Dynamically import handler.py and return its `handle` function."""
    module_name = f"_skill_{skill_name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    # Prefer handle() (matches RunSkillTool), fall back to run() for compat
    return getattr(module, "handle", None) or getattr(module, "run", None)

