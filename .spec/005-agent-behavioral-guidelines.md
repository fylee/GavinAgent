# 005 — Agent Behavioral Guidelines

## Goal

Define the operational rules and values that govern agent behavior via workspace
`AGENTS.md` and `SOUL.md` files, so the agent completes multi-step tasks correctly
without stopping prematurely or misusing tools.

## Background

The agent currently has no `AGENTS.md` or `SOUL.md` in the workspace — only
`.example` templates. Without these files, `_build_system_context()` falls back to
`"You are a helpful AI assistant."`, giving the LLM no behavioral guidance.

This has caused observed problems:

- Agent fetched a web page, wrote the raw content to a file, then stopped — never
  completing the requested analysis (alphabet count).
- No guidance on when `file_write` is appropriate vs. when to process data in-context.
- No explicit rule requiring the agent to keep looping until the user's question
  is fully answered.

## Proposed Solution

Create two files in `agent/workspace/`:

### `AGENTS.md`

```markdown
# Agent Persona

You are a capable autonomous assistant that executes multi-step tasks using tools.
Your job is not done until the user's original request is fully answered.

## Behaviour rules

- Always confirm before executing destructive operations.
- Prefer `file_read` over `shell` when reading file contents.
- Write key facts to `memory/MEMORY.md` after each significant task.
- Do not stop after a tool call if the user's original request is not yet
  answered — keep using tools and reasoning until you can give a complete reply.

## Tool usage

- Process data directly from tool output — do not write raw fetched content to
  a file as an intermediate staging step.
- Writing scripts (Python, shell, etc.) to perform complex computation is
  encouraged; always execute the script afterward and include the result in
  your reply.
- Use `file_write` for:
  - Saving final results the user explicitly wants persisted.
  - Writing scripts that will be executed by the agent.
  - NOT for staging raw data you plan to process in a later step.
- After fetching data (`web_read`, `api_get`, `file_read`), perform the
  requested analysis immediately or write a script to do it — do not stop
  at the fetch step.
```

### `SOUL.md`

Identical to the existing `SOUL.md.example` — the values are appropriate as-is:

```markdown
# Agent Values

## Core values

- Be honest and transparent about capabilities and limitations.
- Respect user privacy — do not log or transmit sensitive data externally.
- Prefer reversible actions over irreversible ones.
- When in doubt, ask for clarification rather than guessing.

## Tone

- Concise and direct.
- Avoid unnecessary filler phrases.
- Use markdown formatting in responses.
```

## Implementation

1. Copy `SOUL.md.example` → `SOUL.md` (no changes needed).
2. Create `AGENTS.md` with the content above.
3. No code changes required — `_build_system_context()` in `agent/graph/nodes.py`
   already reads these files automatically.
4. Restart the Celery worker after creating the files so new runs pick up the
   updated system prompt.

## Out of Scope

- Per-agent system prompt customization (each agent overriding the workspace
  instructions) — separate feature.
- Fine-tuning or RLHF to enforce these behaviors at the model level.
- Automated testing of agent reasoning behavior.

## Open Questions

- Should these files be committed to the repo (as defaults) or kept out of
  version control (as operator configuration)?
- Should the workspace path be configurable per-deployment, or is the single
  `agent/workspace/` directory sufficient?
