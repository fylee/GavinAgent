from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

import yaml


def _parse_skill_md(path: Path) -> dict:
    """Parse SKILL.md frontmatter and description body."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta = yaml.safe_load(parts[1]) or {}
    meta["instructions"] = parts[2].strip()
    return meta


class SkillLoader:
    """Scans workspace/skills/ and loads skills into the registry."""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def load_all(self, registry: "SkillRegistry") -> list[str]:
        """Load/reload all skills. Returns list of skill names loaded."""
        from agent.skills.registry import SkillEntry

        loaded = []
        if not self.skills_dir.exists():
            return loaded

        for skill_dir in sorted(self.skills_dir.iterdir()):
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
                approval_required=meta.get("approval_required", False),
                path=str(skill_dir),
                handler=handler,
            )
            registry.register(entry)
            loaded.append(meta["name"])

        return loaded


def _load_handler(path: Path, skill_name: str) -> Callable | None:
    """Dynamically import handler.py and return its `run` function."""
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
    return getattr(module, "run", None)
