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
Do NOT ask clarifying questions. Write the skill file immediately based on the task below.

## Your task

{task}

## Skill to write

Write a SKILL.md file at this EXACT path (create it now):
{skill_path}

## Conventions (from AGENTS.md)

{agents_md}

## Existing skills (for context — do not duplicate)

{existing_skills}

## Instructions

1. Read the conventions above carefully.
2. If this is a data skill (SQL/API), derive correct column names, filter values,
   and query patterns from the task description and your knowledge.
3. Write the complete SKILL.md to {skill_path} right now.
4. Follow the YAML frontmatter schema exactly.
5. Do NOT put Chinese or non-ASCII text in the YAML frontmatter triggers/examples lists.
6. Save the file as UTF-8 without BOM.
7. Output a single line when done: SKILL_WRITTEN: <skill_name>

Start writing now.
"""

_REVIEW_PROMPT = """\
You are a skill reviewer for GavinAgent. Your job is to review ONE specific skill RIGHT NOW.
Do NOT ask clarifying questions. Do NOT list other skills. Act immediately.

## Skill to review

Skill name: {skill_name}
File path: {skill_path}

Current SKILL.md content:
```
{current_content}
```

## Conventions (from AGENTS.md)

{agents_md}

## Review checklist

1. YAML frontmatter: valid schema, no non-ASCII in flow sequences, version is an integer
2. SQL/API patterns: are column names, table names, filter values correct and specific?
3. Missing guards: is there a "Do NOT use" or "WARNING" section for known wrong approaches?
4. Search strategy: is it specific enough for the agent to use without scatter-searching?
5. Examples: do the trigger examples match real user requests?

## Instructions

1. Review the SKILL.md content above against the checklist.
2. If improvements are needed, overwrite the file at exactly: {skill_path}
   Write the complete improved SKILL.md to that path.
3. Output your result on a SINGLE line at the end:
   - If you rewrote the file: SKILL_UPDATED: {skill_name}
   - If no changes needed: SKILL_OK: {skill_name}
4. After the status line, write 2-4 sentences summarising what you changed or confirmed.

Start reviewing now.
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
            cmd + ["--print", "--no-session-persistence", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),  # repo root
        )
        output = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            logger.error("Claude author_skill failed (rc=%d): %s", result.returncode, stderr)
            return {"status": "error", "output": stderr or output or f"exit code {result.returncode}", "updated": []}

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
        skill_name=skill_name,
        skill_path=str(skill_path),
        current_content=current_content,
        agents_md=_read_agents_md(),
    )

    cmd = _claude_cmd()
    logger.info("review_skill using cmd: %s", cmd)
    try:
        result = subprocess.run(
            cmd + ["--print", "--no-session-persistence", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),
        )
        output = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            return {"status": "error", "output": stderr or output or f"exit code {result.returncode}", "updated": []}

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
