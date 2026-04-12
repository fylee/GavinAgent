"""
Skill authoring via Claude Code subprocess (spec 020, Mode A).

Spawns the `claude` CLI with a structured prompt and waits for it to write
the SKILL.md file.  After completion, reloads the registry and re-embeds.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def _claude_cmd() -> tuple[list[str], bool]:
    """
    Return (cmd, use_stdin) for invoking Claude Code.

    Windows problem: cmd.exe has an 8191-char command-line limit, so passing a
    long prompt via -p "..." gets silently truncated.  Instead we invoke the
    Node.js entry-point directly and pipe the prompt through stdin, which has
    no size limit.

    Returns:
        cmd        — the base command list (without --print / -p flags)
        use_stdin  — True means send prompt via stdin with --print flag,
                     False means append as -p argument (legacy / Linux path)
    """
    # 1. Explicit override
    override = getattr(settings, "CLAUDE_CMD", "") or os.environ.get("CLAUDE_CMD", "")

    # 2. Try node + cli.js directly (bypasses cmd /c and length limit)
    node_exe = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    npm_appdata = os.environ.get("APPDATA", "")
    cli_js = Path(npm_appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    if Path(node_exe).exists() and cli_js.exists() and not override:
        return [node_exe, str(cli_js)], True

    # 3. Explicit override path
    if override:
        if override.endswith(".cmd"):
            # cmd /c can't forward stdin — fall back to -p arg (may truncate for very long prompts)
            return ["cmd", "/c", override], False
        return [override], True

    # 4. Bare 'claude' (Linux/macOS or if on PATH as executable)
    return ["claude"], True

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

_REVIEW_SUGGEST_PROMPT = """\
You are a skill reviewer for GavinAgent. Review ONE specific skill and output a corrected version.
Do NOT ask clarifying questions. Do NOT end with "Would you like me to...". Just do it.
Your reply must contain exactly two sections:

=== Recommand ===
<list the issues found in the original skill with specific recommendations to fix each one>

=== Suggest Version ===
<the complete revised SKILL.md content, no fenced code block, no annotations, no comments>

## Skill to review

Skill name: {skill_name}
File path: {skill_path}

Current SKILL.md content:
{current_content}

## Conventions (from AGENTS.md)

{agents_md}

## Review checklist

1. YAML frontmatter: valid schema, no non-ASCII in flow sequences, version is an integer
2. SQL/API patterns: column names, table names, filter values correct and specific?
3. Missing guards: is there a "Do NOT use" or "WARNING" section for known wrong approaches?
4. Search strategy: specific enough for the agent to use without scatter-searching?
5. Examples: do the trigger examples match real user requests?

Output the two sections now. Nothing before === Recommand === and nothing after the suggested version content.
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

    cmd, use_stdin = _claude_cmd()
    logger.info("author_skill using cmd: %s (stdin=%s)", cmd, use_stdin)
    try:
        if use_stdin:
            result = subprocess.run(
                cmd + ["--print", "--no-session-persistence"],
                input=prompt,
                capture_output=True,
                encoding="utf-8",
                timeout=180,
                cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),
            )
        else:
            result = subprocess.run(
                cmd + ["--print", "--no-session-persistence", "-p", prompt],
                capture_output=True,
                encoding="utf-8",
                timeout=180,
                cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),
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

    cmd, use_stdin = _claude_cmd()
    logger.info("review_skill using cmd: %s (stdin=%s)", cmd, use_stdin)
    try:
        if use_stdin:
            result = subprocess.run(
                cmd + ["--print", "--no-session-persistence"],
                input=prompt,
                capture_output=True,
                encoding="utf-8",
                timeout=180,
                cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),
            )
        else:
            result = subprocess.run(
                cmd + ["--print", "--no-session-persistence", "-p", prompt],
                capture_output=True,
                encoding="utf-8",
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


def review_skill_suggest(skill_name: str) -> dict:
    """
    Ask Claude Code to analyse a SKILL.md and return suggestions + a proposed rewrite,
    WITHOUT writing anything to disk.
    Returns {"status": "ok"|"error", "output": str, "suggested_content": str|None}.
    The suggested_content is the first fenced code block found in Claude's output.
    """
    import re
    skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
    skill_path = skills_dir / skill_name / "SKILL.md"

    if not skill_path.exists():
        return {"status": "error", "output": f"SKILL.md not found for '{skill_name}'", "suggested_content": None}

    current_content = skill_path.read_text(encoding="utf-8")

    prompt = _REVIEW_SUGGEST_PROMPT.format(
        skill_name=skill_name,
        skill_path=str(skill_path),
        current_content=current_content,
        agents_md=_read_agents_md(),
    )

    cmd, use_stdin = _claude_cmd()
    logger.info("review_skill_suggest using cmd: %s (stdin=%s)", cmd, use_stdin)
    try:
        if use_stdin:
            result = subprocess.run(
                cmd + ["--print", "--no-session-persistence"],
                input=prompt,
                capture_output=True,
                encoding="utf-8",
                timeout=180,
                cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),
            )
        else:
            result = subprocess.run(
                cmd + ["--print", "--no-session-persistence", "-p", prompt],
                capture_output=True,
                encoding="utf-8",
                timeout=180,
                cwd=str(Path(settings.AGENT_WORKSPACE_DIR).parent.parent),
            )
        output = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            return {"status": "error", "output": stderr or output or f"exit code {result.returncode}", "suggested_content": None}

        # Extract === Recommand === and === Suggest Version === sections
        recommand_match = re.search(
            r"===\s*Recommand\s*===\s*\n([\s\S]*?)(?===\s*Suggest Version\s*===|\Z)",
            output, re.IGNORECASE
        )
        suggest_match = re.search(
            r"===\s*Suggest Version\s*===\s*\n([\s\S]*?)\Z",
            output, re.IGNORECASE
        )
        issues = recommand_match.group(1).strip() if recommand_match else output
        suggested_content = suggest_match.group(1).strip() if suggest_match else None
        # Strip any accidental fenced code block wrapper
        if suggested_content:
            fenced = re.match(r"^```(?:\w+)?\n([\s\S]*?)```$", suggested_content)
            if fenced:
                suggested_content = fenced.group(1).strip()

        return {"status": "ok", "output": output, "issues": issues, "suggested_content": suggested_content}

    except FileNotFoundError:
        return {
            "status": "error",
            "output": (
                f"Claude CLI not found (tried: {cmd}). "
                "Install Claude Code: https://docs.anthropic.com/claude-code "
                "or set CLAUDE_CMD env var to the full path of claude.cmd."
            ),
            "suggested_content": None,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": "Claude Code timed out after 180s.", "suggested_content": None}
    except Exception as exc:
        logger.exception("review_skill_suggest unexpected error")
        return {"status": "error", "output": str(exc), "suggested_content": None}
