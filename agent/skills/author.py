"""
Skill authoring via Claude Code subprocess (spec 020, Mode A).

Spawns the `claude` CLI with a structured prompt and waits for it to write
the SKILL.md file.  After completion, reloads the registry and re-embeds.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def _claude_cmd() -> list[str]:
    """
    Return the command list to invoke Claude Code.

    On Windows, the npm-installed 'claude' shim is a .cmd file that Python's
    subprocess can't find via bare 'claude'.  We try (in order):
      1. settings.CLAUDE_CMD / CLAUDE_CMD env var (explicit override)
      2. claude.cmd in npm global prefix (auto-detected once via 'npm prefix -g')
      3. Plain 'claude' (works on Linux/macOS or if added to PATH as .exe)
    """
    # 1. Explicit override from settings/env
    override = getattr(settings, "CLAUDE_CMD", "") or os.environ.get("CLAUDE_CMD", "")
    if override:
        return [override] if not override.endswith(".cmd") else ["cmd", "/c", override]

    # 2. Auto-detect npm global bin on Windows
    try:
        npm_result = subprocess.run(
            ["cmd", "/c", "npm", "prefix", "-g"],
            capture_output=True, text=True, timeout=10,
        )
        npm_prefix = npm_result.stdout.strip()
        if npm_prefix:
            candidate = Path(npm_prefix) / "claude.cmd"
            if candidate.exists():
                return ["cmd", "/c", str(candidate)]
    except Exception:
        pass

    return ["claude"]

_AUTHOR_PROMPT = """\
You are a skill author for GavinAgent, an AI agent platform built with Django and LangGraph.

## Your task

{task}

## Skill to write

Write a SKILL.md file at this exact path:
{skill_path}

## Conventions (from AGENTS.md)

{agents_md}

## Existing skills (for context)

{existing_skills}

## Instructions

1. Read the conventions above carefully.
2. If this is a data skill (SQL/API), derive the correct column names, filter values,
   and query patterns from the task description and your knowledge.
3. Write the SKILL.md file directly to the path specified above.
4. Follow the YAML frontmatter schema exactly.
5. Do NOT put Chinese or non-ASCII text in the YAML frontmatter triggers/examples lists.
6. Save the file as UTF-8 without BOM.
7. When done, output a single line: SKILL_WRITTEN: <skill_name>
"""

_REVIEW_PROMPT = """\
You are a skill reviewer for GavinAgent, an AI agent platform built with Django and LangGraph.

## Your task

Review and improve the following SKILL.md file.

Skill path: {skill_path}

Current content:
```
{current_content}
```

## Conventions (from AGENTS.md)

{agents_md}

## Review checklist

1. Are the SQL/API patterns correct and verified?
2. Are column names, filter values, and data types accurate?
3. Is there a "Do NOT use" section listing known wrong approaches?
4. Is the search strategy clear and specific (no scatter-searching)?
5. Is the YAML frontmatter valid? (no non-ASCII in flow sequences)
6. Is the version number appropriate?

## Instructions

- If improvements are needed, rewrite the file at the same path with fixes.
- If the file is already correct, output: SKILL_OK: <skill_name>
- If you rewrote it, output: SKILL_UPDATED: <skill_name>
- Always explain your changes (or lack thereof) in 2-3 sentences after the status line.
"""


def _read_agents_md() -> str:
    path = Path(settings.AGENT_WORKSPACE_DIR) / "AGENTS.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _list_existing_skills() -> str:
    from agent.skills import registry
    lines = []
    for entry in registry.all().values():
        lines.append(f"- {entry.name}: {entry.description}")
    return "\n".join(lines) if lines else "(none)"


def _reload_and_embed(skills_dir: Path) -> list[str]:
    from agent.skills.loader import SkillLoader
    from agent.skills import registry as skill_registry
    from agent.skills.embeddings import embed_all_skills
    loader = SkillLoader(skills_dir)
    loader.load_all(skill_registry)
    return embed_all_skills()


def author_skill(task: str, skill_name: str) -> dict:
    """
    Invoke Claude Code to author a new SKILL.md for the given skill_name.
    Returns {"status": "ok"|"error", "output": str, "updated": list[str]}.
    """
    skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
    skill_dir = skills_dir / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"

    prompt = _AUTHOR_PROMPT.format(
        task=task,
        skill_path=str(skill_path),
        agents_md=_read_agents_md(),
        existing_skills=_list_existing_skills(),
    )

    cmd = _claude_cmd()
    logger.info("author_skill using cmd: %s", cmd)
    try:
        result = subprocess.run(
            cmd + ["--print", "--no-session-persistence"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),  # repo root
        )
        output = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            logger.error("Claude author_skill failed (rc=%d): %s", result.returncode, stderr)
            return {"status": "error", "output": stderr or output, "updated": []}

        updated = _reload_and_embed(skills_dir)
        logger.info("author_skill %s: updated=%s", skill_name, updated)
        return {"status": "ok", "output": output, "updated": updated}

    except FileNotFoundError:
        return {
            "status": "error",
            "output": (
                f"Claude CLI not found (tried: {cmd}). "
                "Install Claude Code: https://docs.anthropic.com/claude-code "
                "or set CLAUDE_CMD env var to the full path of claude.cmd."
            ),
            "updated": [],
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": "Claude Code timed out after 180s.", "updated": []}
    except Exception as exc:
        logger.exception("author_skill unexpected error")
        return {"status": "error", "output": str(exc), "updated": []}


def review_skill(skill_name: str) -> dict:
    """
    Invoke Claude Code to review and optionally improve an existing SKILL.md.
    Returns {"status": "ok"|"updated"|"error", "output": str, "updated": list[str]}.
    """
    skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
    skill_path = skills_dir / skill_name / "SKILL.md"

    if not skill_path.exists():
        return {"status": "error", "output": f"SKILL.md not found for '{skill_name}'", "updated": []}

    current_content = skill_path.read_text(encoding="utf-8")

    prompt = _REVIEW_PROMPT.format(
        skill_path=str(skill_path),
        current_content=current_content,
        agents_md=_read_agents_md(),
    )

    cmd = _claude_cmd()
    logger.info("review_skill using cmd: %s", cmd)
    try:
        result = subprocess.run(
            cmd + ["--print", "--no-session-persistence"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),
        )
        output = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            return {"status": "error", "output": stderr or output, "updated": []}

        updated = _reload_and_embed(skills_dir)
        status = "updated" if updated else "ok"
        return {"status": status, "output": output, "updated": updated}

    except FileNotFoundError:
        return {
            "status": "error",
            "output": (
                f"Claude CLI not found (tried: {cmd}). "
                "Install Claude Code: https://docs.anthropic.com/claude-code "
                "or set CLAUDE_CMD env var to the full path of claude.cmd."
            ),
            "updated": [],
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": "Claude Code timed out after 180s.", "updated": []}
    except Exception as exc:
        logger.exception("review_skill unexpected error")
        return {"status": "error", "output": str(exc), "updated": []}
