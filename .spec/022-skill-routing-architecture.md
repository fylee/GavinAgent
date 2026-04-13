# 022 — Skill Routing Architecture

## Goal

Implement the hybrid skill routing model decided in Spec 021: add a skill catalog to the agent system prompt (Model B / catalog) while retaining embedding-based routing (Model A), and update `embeddings.py` to read GavinAgent extension fields from `metadata` instead of top-level frontmatter.

## Dependency

**Must be implemented after Spec 021** (frontmatter migration complete) and **after Spec 021-1** (authoring guidance updated).

Both must be complete before 022 ships, because:
- 022's `embeddings.py` adapter reads `metadata.triggers` — requires 021's frontmatter migration to be done
- 022's catalog relies on `description` quality — requires 021-1's authoring guidance to produce good descriptions

## Background

Spec 021 decided on a hybrid routing model:

1. **Embedding routing (existing)** — `embeddings.py` embeds skill text into pgvector; cosine similarity selects skills above threshold
2. **Catalog routing (new)** — all skill `name + description` pairs injected into the system prompt so the LLM can self-activate or understand what skills exist

Currently GavinAgent has only embedding routing, and the LLM has no visibility into what skills are available. Adding a catalog gives the LLM skill awareness without removing the precision of embedding routing.

## Proposed Solution

### Component 1: `embeddings.py` metadata adapter

Update `_skill_embed_text` and `embed_all_skills` to read GavinAgent extension fields from `metadata` after Spec 021's frontmatter migration:

```python
# agent/skills/embeddings.py

def _parse_metadata_list(metadata: dict, key: str) -> list[str]:
    """Read a pipe-separated string from metadata as a list."""
    raw = metadata.get(key, "")
    if not raw:
        return []
    return [item.strip() for item in str(raw).split("|") if item.strip()]

# In embed_all_skills(), replace:
#   examples = meta.get("examples", [])
# With:
    nested_meta = meta.get("metadata", {}) or {}
    examples = _parse_metadata_list(nested_meta, "examples")
    triggers = _parse_metadata_list(nested_meta, "triggers")
```

Update `_skill_embed_text` to also embed `triggers` as routing signal:

```python
def _skill_embed_text(
    name: str,
    description: str,
    body: str,
    examples: list[str] | None = None,
    triggers: list[str] | None = None,
) -> str:
    parts = [f"{name}: {description}"]
    if triggers:
        parts.append("Keywords: " + ", ".join(triggers[:20]))
    if examples:
        parts.append("Example requests: " + "; ".join(examples[:10]))
    excerpt = body[:EMBED_TEXT_MAX_CHARS].strip()
    if excerpt:
        parts.append(excerpt)
    return "\n\n".join(parts)
```

### Component 2: Skill catalog in system prompt

#### 2a. Build the catalog

Add `build_skill_catalog()` to `agent/skills/embeddings.py`:

```python
def build_skill_catalog() -> str:
    """
    Return a compact skill catalog for injection into the system prompt.
    Format: one line per skill — "name: description"
    Excludes disabled skills.
    """
    from agent.models import Skill

    disabled = set(Skill.objects.filter(enabled=False).values_list("name", flat=True))
    skills_dir = _skills_dir()
    lines: list[str] = []

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            meta: dict = {}
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
            name = meta.get("name", skill_dir.name)
            if name in disabled:
                continue
            description = meta.get("description", "").strip()
            if description:
                lines.append(f"- **{name}**: {description}")
        except Exception:
            continue

    if not lines:
        return ""
    return "## Available Skills\n\n" + "\n".join(lines)
```

#### 2b. Inject catalog into system prompt

In `agent/apps.py` (or wherever the system prompt is assembled), add the catalog section:

```python
from agent.skills.embeddings import build_skill_catalog

def build_system_prompt(conversation, ...) -> str:
    parts = [BASE_SYSTEM_PROMPT]

    catalog = build_skill_catalog()
    if catalog:
        parts.append(catalog)
        parts.append(
            "When a user request matches a skill above, "
            "the skill's full instructions will be loaded automatically."
        )

    # ... rest of system prompt assembly
    return "\n\n".join(parts)
```

The catalog section adds ~50–100 tokens per skill. For GavinAgent's current 7 skills this is ~400–700 tokens — negligible.

#### 2c. Catalog format example

```
## Available Skills

- **weather**: Get current weather and today's forecast for one or more cities. Returns temperature, conditions, wind, humidity, and UV index. Use this skill instead of web_read for all weather queries.
- **stock-chart**: Fetch historical stock prices and generate a line chart. Supports any ticker symbol on Yahoo Finance. Use this skill instead of web_read or web_search for stock price queries that need a chart.
- **data-analysis**: Analyse tabular data, compute statistics, rank and filter datasets. Use when the user asks to calculate, compare, aggregate, or summarise numerical data.
- **charts**: Generate bar, line, pie, and scatter charts. Use when visualising data would help the user understand results better than a text table.
- **web-research**: Search the web and fetch URLs for current information. Use when the user asks about recent events, prices, statistics, or anything that changes over time.
- **workflow-management**: Create scheduled and recurring workflows. Use when the user wants something done at a specific time, on a schedule, or repeated automatically.
- **edwm-wip-movement**: Query EDWM WIP and movement data for CT (Taichung) and KH (Kaohsiung) FAB. Use for fab production move counts, lot movement queries, and daily move summaries.
```

### Component 3: Catalog cache (optional optimisation)

`build_skill_catalog()` reads disk on every request. For production, cache the result in Django's cache framework and invalidate on `embed_all_skills()`:

```python
from django.core.cache import cache

CATALOG_CACHE_KEY = "agent:skill_catalog"

def build_skill_catalog() -> str:
    cached = cache.get(CATALOG_CACHE_KEY)
    if cached is not None:
        return cached
    result = _build_skill_catalog_uncached()
    cache.set(CATALOG_CACHE_KEY, result, timeout=300)  # 5 min TTL
    return result

def embed_all_skills() -> list[str]:
    # ... existing logic ...
    cache.delete(CATALOG_CACHE_KEY)  # invalidate catalog on skill change
    return processed
```

## Routing flow after implementation

```
User message
    │
    ├─► Embedding router (existing)
    │     embed query → cosine similarity → skills above 0.55 threshold
    │     → inject matching skill bodies into context
    │
    └─► Catalog (new)
          injected in system prompt at session start
          → LLM knows all skills exist
          → LLM understands routing decision made by embedding router
          → LLM can reference skill names explicitly in reasoning
```

## Out of Scope

- Removing embedding routing (deferred; requires description quality validation first)
- User-explicit skill activation via slash command (future spec)
- Per-request catalog filtering by conversation topic
- Subagent delegation pattern

## Acceptance Criteria

- [ ] `embeddings.py` reads `triggers` and `examples` from `metadata` (not top-level frontmatter)
- [ ] `_skill_embed_text` includes `triggers` as a routing signal in the embedded text
- [ ] `build_skill_catalog()` returns a compact catalog of enabled skills
- [ ] Catalog is injected into the system prompt on every agent run
- [ ] Catalog excludes disabled skills (consistent with embedding router)
- [ ] Catalog is invalidated from cache when `embed_all_skills()` runs
- [ ] Existing embedding routing behaviour is unchanged (same threshold, same logic)
- [ ] No regression in skill selection for existing test queries

## Open Questions

1. **Catalog placement in system prompt**: Should the catalog appear at the top (before all other context) or near the bottom (just before conversation history)? Top placement gives it more weight; bottom placement keeps the opening instructions clean.

2. **Catalog when no skills are installed**: Should the catalog section be omitted entirely (cleaner) or shown as empty with a note? The spec says omit it entirely.

3. **Threshold tuning**: Now that the LLM has catalog visibility, should the embedding threshold (0.55) be reviewed? The catalog may reduce false negatives, allowing a slightly higher threshold for precision.

## Implementation Notes

<!-- Filled in during or after implementation. -->
