# 010 — Embedding-Based Skill Routing

## Goal

Replace keyword/regex trigger matching for skills with semantic similarity search,
so skills are injected into the system prompt based on meaning rather than exact
phrase enumeration.

## Background

Skills currently define `triggers: [list of keywords]` and optionally
`trigger_patterns: [regexes]` in their `SKILL.md` frontmatter. This requires skill
authors to exhaustively enumerate trigger phrases and still misses semantically
equivalent phrasings.

**Example failure:** "tell me a joke at 14:25 today" should trigger
`workflow-management` but matched nothing without explicit enumeration of every
possible time format.

Anthropic's guidance: use embedding retrieval, not keyword matching. Claude handles
long contexts well but retrieval failures are hard to debug — semantic search is
more reliable than regex.

## Proposed Solution

### 1. `SkillEmbedding` model (new)

A new DB table storing one embedding per skill:

```python
class SkillEmbedding(TimeStampedModel):
    skill_name = models.CharField(max_length=100, unique=True)
    embedding   = VectorField(dimensions=1536)
    content_hash = models.CharField(max_length=64)  # SHA-256 of embedded text
```

`content_hash` is used to skip re-embedding when the skill hasn't changed.

### 2. Embedded text per skill

Each skill is embedded as:
```
{name}: {description}

{first 500 chars of body instructions}
```

This captures both *what* the skill does and *when* to use it.

### 3. Skill routing at query time

In `_build_skills_section(query)`:

1. Embed the user query (`core.memory.embed_text`)
2. Cosine similarity search against `SkillEmbedding` (pgvector)
3. Skills above threshold (default `0.35`) → **Active** (full instructions injected)
4. Skills below threshold → **Available** (listed in index only)
5. Fall back to keyword/regex triggers for skills with no embedding yet

### 4. Re-embedding on startup and skill reload

- `AgentConfig.ready()` calls `embed_all_skills()` (async-safe, in a thread)
- `WorkflowLoader.load_all()` (called by `reload_workflows` tool) also triggers re-embed
- Only skills whose `content_hash` has changed are re-embedded (cheap check)

### 5. Skill authoring simplification

- `triggers` and `trigger_patterns` fields become **optional**
- `description` field is the primary routing signal — write it to describe
  *when* the skill should be used, not just *what* it does
- Example good description:
  > "Scheduling recurring or one-shot tasks, creating workflows, setting reminders,
  >  running things automatically at a specific time or interval"

## Out of Scope

- Per-agent skill filtering
- Real-time embedding updates without `reload_workflows` call
- Embedding skills stored outside `workspace/skills/`

## Open Questions

- **Threshold tuning**: 0.35 is a starting point. May need adjustment once tested
  with real queries. Could be made configurable via `AGENT_SKILL_SIMILARITY_THRESHOLD`.
- **Fallback behaviour**: Skills with no embedding fall back to keyword triggers.
  If neither exists, short skills (<50 lines) are always injected, long ones are not.
