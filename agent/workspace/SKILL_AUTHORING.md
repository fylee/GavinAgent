# Skill Authoring Guide

Skills live in `agent/workspace/skills/<name>/SKILL.md`.
Each skill is a Markdown file with YAML frontmatter followed by a Markdown body.

> **This file is NOT injected into agent runs.** It is reference documentation for
> humans and for agents explicitly working on skill authoring tasks.

## SKILL.md frontmatter schema (agentskills.io spec-compliant)

```yaml
---
name: skill-name          # required; must match the directory name exactly; lowercase, hyphens only
description: >            # required; 80–300 chars; describe BOTH what it does AND when to use it
  Fetch historical stock prices and generate a line chart. Supports any ticker
  on Yahoo Finance. Use this skill instead of web_read for stock price queries.
allowed-tools: Bash       # optional; space-separated tools pre-approved for this skill
compatibility: Requires internet access to Yahoo Finance   # optional; environment notes
metadata:                 # GavinAgent extension fields — all values must be strings
  triggers: "keyword | phrase | another phrase"   # pipe-separated; keyword fallback matching
  trigger_patterns: "regex1 ;; regex2"             # DOUBLE-SEMICOLON separated regex patterns (NOT pipe — regex uses | internally)
  examples: "example request 1 | example request 2"  # pipe-separated user examples
  version: "1"            # increment on each significant update
  approval_required: "false"
---
```

**❌ Do NOT write these as top-level keys — they are non-compliant:**
```yaml
triggers: [...]        # wrong — must be inside metadata
examples: [...]        # wrong — must be inside metadata
version: 1             # wrong — must be inside metadata as a string
approval_required: false  # wrong — must be inside metadata as a string
tools: [run_skill]     # wrong — use allowed-tools at top level instead
```

## Description quality

The `description` field is the most important field. It must:
- Describe what the skill does (capabilities, outputs)
- State **when** to use it (trigger conditions, user intent signals)
- Include domain-specific keywords an agent would recognise
- Be 80–300 characters

Good: `"Analyse tabular data, compute statistics, rank and filter datasets. Use when the user asks to calculate, compare, aggregate, or summarise numerical data."`
Poor: `"Helps with data."`

## Required body sections

Every SKILL.md body must contain these sections in order:

1. **Overview / Key conventions** — bullet list of the most important rules,
   data types, filters, and gotchas. Be explicit; do not assume the LLM knows.

2. **Standard query patterns** — copy-paste ready code blocks (SQL, API calls,
   shell commands) verified against live data before writing.

3. **Do NOT use** — explicit list of wrong approaches, wrong column names,
   wrong table names. This prevents the most common errors.

4. **Search strategy** — numbered steps telling the agent exactly how to start,
   what tools to call first, and how to avoid scatter-searching.

## Verification rules before writing SQL patterns

- Always run `execute_trino_query` with a `LIMIT 5` or `COUNT(*)` to confirm
  columns exist and return data before writing them into the skill.
- Check column types: timestamp columns need `DATE()` cast in WHERE clauses.
- Confirm filter values (e.g. `lot_type IN ('P','PE')` not `'PROD'`) from real data.

## Naming conventions

- Directory name: `kebab-case`, e.g. `edwm-wip-movement`, `stock-chart`
- `name` in frontmatter must match the directory name exactly
- `metadata.version` starts at `"1"`; increment when SQL patterns or conventions change significantly

## Encoding

- SKILL.md must be saved as **UTF-8 without BOM**
- Do NOT include Chinese or other multi-byte characters in the YAML frontmatter
  `metadata.triggers` or `metadata.examples` strings
- Chinese text is safe in the Markdown body only
