# 020 — Claude Code as Skill Author for GavinAgent

## Goal

Allow GavinAgent's skills to be created, reviewed, and improved by Claude Code
(Anthropic's agentic CLI tool) acting as a skill author, so that human operators
can delegate skill authoring and quality review to an AI assistant that has
direct access to the filesystem and can reason about the skill corpus.

## Background

GavinAgent skills live in `agent/workspace/skills/<name>/SKILL.md` as Markdown
files with YAML frontmatter.  Today, skills are authored entirely by hand.
This is slow and error-prone — as seen with the `edwm-wip-movement` skill where
column names, lot_type values, and date cast conventions required multiple
correction cycles driven by production query failures.

Claude Code is an agentic tool that can:
- Read and write files on the local filesystem
- Run shell commands (e.g. execute Trino queries to verify SQL patterns)
- Reason over a corpus of documents and produce structured output
- Accept structured instructions via a CLAUDE.md or skill spec file

The goal is to wire these two systems together so that:
1. A human operator describes a skill need in natural language
2. Claude Code drafts a `SKILL.md` following GavinAgent conventions
3. GavinAgent's sync mechanism (`embed_all_skills`) picks it up automatically
4. Optionally, Claude Code can query live data to verify SQL patterns before
   committing the skill

## Proposed Solution

### Architecture

```
Operator (human)
    │  "create a skill for EDWM lot hold queries"
    ▼
GavinAgent UI  ──→  POST /agent/skills/author/
    │               (passes task + context to Claude Code subprocess)
    ▼
Claude Code (subprocess / MCP server)
    │  reads SKILL.md corpus, AGENTS.md, existing skills
    │  optionally calls execute_trino_query via EDWM MCP
    │  writes agent/workspace/skills/<name>/SKILL.md
    ▼
embed_all_skills()  ──→  SkillEmbedding updated in pgvector
    ▼
GavinAgent uses new skill immediately
```

### Two integration modes

#### Mode A — Subprocess (simpler, local dev)

GavinAgent spawns `claude` CLI as a subprocess with a structured prompt,
passing:
- The operator's task description
- The `AGENTS.md` skill authoring conventions
- Existing skill names + descriptions as context
- Optional: table schema from `get_logical_table_detail` for data skills

Claude Code writes the `SKILL.md` file directly and exits.
GavinAgent detects the new/changed file and re-embeds.

```python
# agent/skills/author.py
import subprocess, json
from pathlib import Path

def author_skill(task: str, skill_name: str) -> dict:
    prompt = _build_prompt(task, skill_name)
    result = subprocess.run(
        ["claude", "--print", "--no-conversation"],
        input=prompt, capture_output=True, text=True, timeout=120
    )
    # Claude writes the file; we just re-embed
    from agent.skills.embeddings import embed_all_skills
    updated = embed_all_skills()
    return {"updated": updated, "output": result.stdout}
```

#### Mode B — Claude Code as MCP server (richer, spec 019 extension)

Claude Code exposes itself as an MCP server (`claude mcp serve`).
GavinAgent's MCPConnectionPool connects to it as a stdio MCP server.
This gives GavinAgent a `create_skill` / `review_skill` tool that can be
called from within the LangGraph agent loop — allowing an agent run to
self-improve its own skill corpus mid-session.

MCP tool definitions:
- `create_skill(name, task_description)` → writes SKILL.md, returns path
- `review_skill(name)` → reads existing SKILL.md, suggests improvements,
  optionally verifies SQL by calling EDWM MCP tools
- `list_skills()` → returns current skill names + descriptions

### AGENTS.md conventions (skill authoring section)

A new `## Skill Authoring` section will be added to `agent/workspace/AGENTS.md`
describing:
- SKILL.md frontmatter schema (name, description, triggers, examples, version)
- Required sections: Key conventions, Standard query patterns, Do NOT use, Search strategy
- How to verify SQL patterns before writing them (use `execute_trino_query`)
- Naming conventions for skill directories

### Django UI integration

New view: `POST /agent/skills/author/`
- Accepts `task` (text) and optional `skill_name`
- Spawns Claude Code subprocess (Mode A) or calls MCP tool (Mode B)
- Streams output back via SSE or returns JSON
- On completion, calls `embed_all_skills()` and redirects to `/agent/skills/`

New button on `/agent/skills/` page: **"Author with Claude"**
- Opens a modal with a textarea for the task description
- Posts to `/agent/skills/author/`
- Shows progress and final skill name on completion

### Review workflow

Claude Code can review an existing skill by:
1. Reading the SKILL.md
2. Identifying gaps: missing columns, unverified SQL, outdated conventions
3. Optionally running verification queries via EDWM MCP
4. Producing a diff or rewriting the file in-place

Triggered from the skill row in `/agent/skills/` via a **"Review"** button.

## Out of Scope

- Claude Code modifying Python handler files (`handler.py`) — text SKILL.md only
- Automatic deployment to production without human review
- Multi-turn conversation UI for skill authoring (single-shot prompt only in v1)
- Claude Code initiating skill authoring unprompted (always operator-triggered)

## Open Questions

1. **Mode A vs Mode B**: Mode A (subprocess) is simpler and works today if
   `claude` CLI is installed. Mode B (MCP server) is more powerful but requires
   Claude Code to support `claude mcp serve` stably. Which to implement first?

2. **Verification gate**: Should the skill be auto-embedded immediately after
   Claude writes it, or should the operator review the diff first?

3. **CLAUDE.md conventions**: Should skill authoring conventions live in the
   existing `CLAUDE.md` at repo root, or in a separate
   `agent/workspace/AGENTS.md` section?  Currently `CLAUDE.md` is for Copilot
   instructions; keeping skill authoring conventions in `AGENTS.md` is cleaner.

4. **Security**: Claude Code subprocess has filesystem write access.  Should
   writes be sandboxed to `agent/workspace/skills/` only, or is the full repo
   acceptable given it's a local dev tool?

## Implementation Plan

### Phase 1 — Mode A subprocess (local dev)
1. Add `## Skill Authoring` section to `AGENTS.md` with full SKILL.md schema
2. Implement `agent/skills/author.py` with `author_skill()` and `review_skill()`
3. Add `SkillAuthorView` at `POST /agent/skills/author/`
4. Add "Author with Claude" button + modal to `/agent/skills/` page
5. Wire re-embed on completion

### Phase 2 — Mode B MCP server (optional upgrade)
1. Add `claude-code` as a stdio MCP server in `MCPServer` DB record
2. Register `create_skill`, `review_skill`, `list_skills` tools in registry
3. Allow agent runs to call these tools from within the LangGraph loop
