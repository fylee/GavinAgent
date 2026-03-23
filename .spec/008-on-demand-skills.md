# 008 — Skills System (Anthropic-aligned)

## Goal

Replace the monolithic `AGENTS.md` approach with a modular skill system where domain-specific instructions are stored in individual `SKILL.md` files, selectively injected based on the task query, and surfaced in the Agent UI so the user can see which skills were triggered.

## Background

The current agent system injects the entire `AGENTS.md` file into every system prompt, regardless of whether the task requires charts, web research, or any other domain-specific capability. This causes:

1. **Bloat** — every call carries instructions for capabilities irrelevant to the task
2. **Over-specificity** — `AGENTS.md` accumulated task-specific hints that mislead the agent on unrelated tasks

This spec went through two design iterations:

**v1 (OpenClaw-style):** Inject a compact index of skill paths; LLM calls `file_read` to load instructions when needed. Gap: unreliable — the LLM may skip loading the skill.

**v2 (always-inject):** Inject all skill bodies into every prompt. Gap: not selective — chart instructions appear even for "what time is it?".

**v3 (this revision):** Keyword-based selective injection. The system always shows a compact index of all skills, and injects the full body only for skills whose triggers match the query. Triggered skills are recorded on `AgentRun` and displayed in the run detail UI.

## Proposed Solution

### Skill frontmatter

Add a `triggers` field: a list of keywords/phrases checked (case-insensitive substring) against the user query.

```yaml
---
name: charts
description: Generating charts and data visualisations
triggers: [chart, graph, plot, visuali, bar chart, pie chart, line chart, scatter]
version: 1
---
```

If `triggers` is absent, the skill is never auto-injected (only shown in the index).

### System prompt structure

```
[AGENTS.md — universal rules]

---

[SOUL.md — persona, if present]

---

## Skills

Full instructions are injected for skills relevant to this task.

| Skill | Description | Status |
|-------|-------------|--------|
| charts | Generating charts... | **Active** |
| web-research | Searching the web... | Available |
| data-analysis | Processing tabular data... | Available |

---

### charts  ← only triggered skills get full body
<full body>

---

[Relevant memories]

---

[MCP Resources]
```

### `_build_skills_section(query)` signature change

Returns `tuple[str, list[str]]` — the section text and list of triggered skill names.

### `AgentRun.triggered_skills` field

New `JSONField(default=list, blank=True)` on `AgentRun`. Populated in `call_llm` node from the return value of `_build_system_context`.

### `_build_system_context(query)` return type change

Returns `tuple[str, list[str]]` — context string and triggered skill names. Callers (`call_llm`, `force_conclude`) updated accordingly.

### Agent UI

In `_run_status.html` and `run_detail.html`, display triggered skill badges when `run.triggered_skills` is non-empty:

```
Skills  [charts]  [web-research]
```

### Skills with handlers

Unchanged — `handler.py` skills continue to be registered as LLM-callable tools.

## Out of Scope

- Per-agent skill enablement — future spec
- Skills UI for managing skill files — future spec
- Embedding/semantic matching — overkill at current scale
- External skill registry

## Acceptance Criteria

- [ ] Skills with matching triggers are injected; non-matching skills appear only in the index table
- [ ] Skills without a `triggers` field are listed in the index but never auto-injected
- [ ] `AgentRun.triggered_skills` stores the list of triggered skill names
- [ ] Run detail UI shows triggered skill badges
- [ ] Adding a new skill with triggers requires no code changes

## Implementation Notes

### v1
- `_build_skills_index()` injected a compact path table; LLM used `file_read` to load skills.

### v2
- Replaced with `_build_skills_section()` — always injected all skill bodies.

### v3 (this revision)
- Selective injection via `triggers` keyword matching.
- `_build_system_context` returns `tuple[str, list[str]]`.
- `AgentRun.triggered_skills` added; migration 0006.
- UI badges in `_run_status.html`.
