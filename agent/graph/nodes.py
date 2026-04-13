from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from agent.graph.state import AgentState

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────


def _read_workspace_file(relative: str) -> str:
    path = Path(settings.AGENT_WORKSPACE_DIR) / relative
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _count_tokens(messages: list[dict], model: str) -> int:
    try:
        import litellm
        return litellm.token_counter(model=model, messages=messages)
    except Exception:
        return sum(len(m.get("content", "") or "") for m in messages) // 4


def _truncate_history(history: list[dict], budget_tokens: int, model: str) -> list[dict]:
    """Drop oldest messages until within budget."""
    while history and _count_tokens(history, model) > budget_tokens:
        history = history[1:]
    return history


def _append_rules(skill_dir: Path, body: str) -> str:
    """Spec 023: append rules/*.md content to the skill body at injection time.

    Embedding serves routing (uses only SKILL.md frontmatter + first 500 chars).
    Body injection serves execution — the full rules/ content is appended here
    so the LLM receives complete instructions without polluting routing signal.
    """
    rules_dir = skill_dir / "rules"
    if not rules_dir.is_dir():
        return body
    parts: list[str] = []
    for rules_file in sorted(rules_dir.glob("*.md")):
        try:
            content = rules_file.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"### {rules_file.stem}\n\n{content}")
        except Exception:
            pass
    if not parts:
        return body
    return body + "\n\n---\n\n" + "\n\n---\n\n".join(parts)


def _build_skills_section(query: str) -> tuple[str, list[str], list[dict]]:
    """Discover skills across all trusted source directories, match against query
    using embeddings (with keyword fallback), inject body for matched skills.

    Spec 023: uses collect_all_skills() for multi-source discovery.
    rules/*.md content is appended to skill body at injection time.

    Returns (section_text, triggered_skill_names, skill_entries, skill_dir_map).
    skill_entries: list of {name, status, match} for context_trace.
    skill_dir_map: {name: skill_dir} for all trusted skills (reused by call_llm).
    """
    import re
    import yaml
    from agent.skills.embeddings import find_relevant_skills
    from agent.skills.discovery import collect_all_skills

    query_lower = query.lower()

    # Embedding-based routing (primary): returns list[tuple[str, float]]
    _embedding_results = find_relevant_skills(query)
    embedding_matches: dict[str, float] = {name: score for name, score in _embedding_results}

    index_rows: list[str] = []
    body_sections: list[str] = []
    triggered: list[str] = []
    skill_entries: list[dict] = []

    # Load set of disabled skill names from DB
    from agent.models import Skill
    disabled_skills = set(
        Skill.objects.filter(enabled=False).values_list("name", flat=True)
    )

    # Spec 023: iterate over all trusted skill sources
    all_skills = collect_all_skills(check_db_trust=True)
    if not all_skills:
        return "", [], []

    for skill_info in all_skills:
        skill_dir = skill_info["skill_dir"]
        if not skill_info["trusted"]:
            continue  # untrusted skills are invisible to the LLM

        skill_md = skill_dir / "SKILL.md"
        # Skip disabled skills
        if skill_dir.name in disabled_skills:
            continue
        text = skill_md.read_text(encoding="utf-8")
        meta: dict = {}
        body = text
        if text.startswith("---"):
            raw_parts = text.split("---", 2)
            if len(raw_parts) >= 3:
                meta = yaml.safe_load(raw_parts[1]) or {}
                body = raw_parts[2].strip()

        name = meta.get("name", skill_dir.name)
        description = meta.get("description", "")

        # Spec 021: GavinAgent extension fields live in metadata (pipe-separated strings).
        # Legacy top-level lists are also accepted for backwards compatibility.
        from agent.skills.embeddings import _parse_metadata_list
        nested_meta = meta.get("metadata", {}) or {}
        triggers: list[str] = (
            _parse_metadata_list(nested_meta, "triggers")
            or _parse_metadata_list(meta, "triggers")
        )
        trigger_patterns: list[str] = (
            _parse_metadata_list(nested_meta, "trigger_patterns")
            or _parse_metadata_list(meta, "trigger_patterns")
        )

        # Embedding match (primary)
        match_reason: str | None = None
        if name in embedding_matches:
            matched = True
            match_reason = "embedding"
        elif not embedding_matches and not triggers and not trigger_patterns:
            # No embeddings at all in DB yet and no keywords — fall back to always-inject for short skills
            matched = len(body.splitlines()) < 50
            match_reason = None
        else:
            # Keyword/regex fallback — only fires if the embedding did NOT match
            # and the skill has explicit triggers or patterns configured
            matched = False
            if triggers:
                if any(t.lower() in query_lower for t in triggers):
                    matched = True
                    match_reason = "keyword"
            if not matched and trigger_patterns:
                if any(re.search(p, query_lower) for p in trigger_patterns):
                    matched = True
                    match_reason = "regex"

        skill_status = "active" if matched else "available"
        skill_entries.append({"name": name, "status": skill_status, "match": match_reason})
        display_status = "**Active**" if matched else "Available"
        index_rows.append(f"| {name} | {description} | {display_status} |")

        if matched:
            triggered.append(name)
            heading = f"### {name}"
            if description:
                heading += f"\n{description}"
            # Spec 023: append rules/*.md at injection time (not embed time)
            injected_body = _append_rules(skill_dir, body)
            body_sections.append(f"{heading}\n\n{injected_body}")

    if not index_rows:
        return "", [], [], {}

    index = (
        "## Skills\n\n"
        "Full instructions are injected for skills relevant to this task.\n\n"
        "| Skill | Description | Status |\n"
        "|-------|-------------|--------|\n"
        + "\n".join(index_rows)
    )

    if body_sections:
        section = index + "\n\n---\n\n" + "\n\n---\n\n".join(body_sections)
    else:
        section = index

    # Build skill_dir_map for reuse (avoids second collect_all_skills call in call_llm)
    skill_dir_map: dict[str, Path] = {
        s["name"]: s["skill_dir"]
        for s in all_skills
        if s["trusted"]
    }
    return section, triggered, skill_entries, skill_dir_map


def _build_knowledge_section(query: str) -> tuple[str, list[dict]]:
    """Retrieve relevant knowledge chunks and format as a system prompt section.

    Returns (section_text, matched_documents) where matched_documents is a list
    of {title, similarity} dicts. section_text is empty if nothing matched.
    """
    from agent.rag.retriever import retrieve_knowledge

    results = retrieve_knowledge(query)
    if not results:
        return "", []

    parts = [
        "## Reference Knowledge\n\n"
        "The following excerpts from your knowledge base are relevant to this query.\n"
        "Use them to ground your answer. Cite the source when appropriate."
    ]
    # Deduplicate document titles for the summary
    seen_titles: dict[str, float] = {}
    for r in results:
        header = f"### From: {r['document_title']}"
        if r["source_url"]:
            header += f" ({r['source_url']})"
        parts.append(f"{header}\n\n{r['content']}")
        if r["document_title"] not in seen_titles:
            seen_titles[r["document_title"]] = r["similarity"]

    matched_docs = [
        {"title": title, "similarity": sim}
        for title, sim in seen_titles.items()
    ]
    return "\n\n".join(parts), matched_docs


def _build_system_context(query: str) -> tuple[str, list[str], list[dict], dict]:
    """Assemble system prompt from workspace files, memories, and MCP resources.

    Returns (system_prompt, triggered_skill_names, rag_matches, context_trace).
    """
    from datetime import datetime
    import zoneinfo
    agent_tz = zoneinfo.ZoneInfo(getattr(settings, "AGENT_TIMEZONE", "UTC"))
    now = datetime.now(agent_tz)
    temporal_context = (
        f"Current date and time: {now.strftime('%Y-%m-%d %H:%M:%S')} {settings.AGENT_TIMEZONE} "
        f"({now.strftime('%A')})\n"
        f"When writing workflow cron expressions, use timezone: {settings.AGENT_TIMEZONE}"
    )

    agents_md = _read_workspace_file("AGENTS.md")
    soul_md = _read_workspace_file("SOUL.md")
    parts = [temporal_context]
    if agents_md:
        parts.append(agents_md)
    if soul_md:
        parts.append(soul_md)

    # ── Parallelise the three independent vector-DB lookups ────────────────
    # skills (pgvector), long-term memory (pgvector), knowledge RAG (pgvector)
    # and MCP resources (network) run concurrently; total wall time ≈ slowest one.
    skills_section = triggered = skill_entries = None
    excerpts: list[str] = []
    rag_matches: list[dict] = []
    knowledge_section = ""
    mcp_resources: list[str] = []

    def _fetch_skills():
        return _build_skills_section(query)

    def _fetch_memory():
        try:
            from agent.memory.long_term import search_long_term
            return search_long_term(query, limit=5) or []
        except Exception:
            return []

    def _fetch_knowledge():
        try:
            return _build_knowledge_section(query)
        except Exception:
            return "", []

    def _fetch_mcp_resources():
        try:
            from agent.mcp.pool import MCPConnectionPool
            return MCPConnectionPool.get().fetch_always_include_resources() or []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ctx") as pool:
        f_skills = pool.submit(_fetch_skills)
        f_memory = pool.submit(_fetch_memory)
        f_knowledge = pool.submit(_fetch_knowledge)
        f_mcp = pool.submit(_fetch_mcp_resources)

        skills_section, triggered, skill_entries, skill_dir_map = f_skills.result()
        excerpts = f_memory.result()
        knowledge_section, rag_matches = f_knowledge.result()
        mcp_resources = f_mcp.result()
    # ── End parallel section ───────────────────────────────────────────────

    # Spec 022: inject skill catalog
    try:
        from agent.skills.embeddings import build_skill_catalog
        catalog = build_skill_catalog()
        if catalog:
            parts.append(catalog)
    except Exception:
        pass

    if skills_section:
        parts.append(skills_section)

    memory_excerpts = 0
    if excerpts:
        memory_excerpts = len(excerpts)
        parts.append("## Relevant memories\n\n" + "\n\n".join(excerpts))

    mcp_resources_chars = 0
    if mcp_resources:
        mcp_resources_chars = sum(len(r) for r in mcp_resources)
        parts.append("## MCP Resources\n\n" + "\n\n".join(mcp_resources))

    # Inject MCP connectivity status so the agent can distinguish
    # "server configured but tools not yet loaded" from "server not configured".
    try:
        from agent.models import MCPServer
        from agent.mcp.pool import MCPConnectionPool
        from agent.mcp.registry import get_registry as get_mcp_registry

        pool = MCPConnectionPool.get()
        reg = get_mcp_registry()
        mcp_servers = list(MCPServer.objects.filter(enabled=True))
        if mcp_servers:
            mcp_lines: list[str] = []
            for srv in mcp_servers:
                tool_count = sum(
                    1 for e in reg.all().values() if e.server_name == srv.name
                )
                pool_status = pool.get_status(srv.name)
                if tool_count > 0:
                    status_str = f"connected — {tool_count} tools available"
                elif pool_status == "connected":
                    status_str = "connected — tools loading (retry in a few seconds)"
                else:
                    status_str = "disconnected — tools unavailable"
                mcp_lines.append(f"- **{srv.name}**: {status_str}")
            parts.append(
                "## MCP Server Status\n\n"
                + "\n".join(mcp_lines)
                + "\n\nIf a required server shows 'tools loading', check your tool list first — "
                "the tools may already be available even before this status updates. "
                "Only tell the user to wait if the tools are genuinely absent from your tool list. "
                "If it shows 'disconnected', ask the user to reconnect it via GavinAgent MCP settings."
            )
    except Exception:
        pass

    # Knowledge base context (auto-injected via RAG) — already fetched in parallel above
    if knowledge_section:
        parts.append(knowledge_section)

    content = "\n\n---\n\n".join(parts) if parts else "You are a helpful AI assistant."

    # Always append the tool-output formatting rule so the LLM never paraphrases
    # markdown fields (e.g. chart image syntax) returned by tools.
    content += (
        "\n\n---\n\n"
        "## Tool output formatting rule\n\n"
        "When a tool result contains a `markdown` field, please copy that value "
        "verbatim into your reply. Do not describe or paraphrase it — reproduce it "
        "exactly as-is so that images and links render correctly."
        "\n\n---\n\n"
        "## Parallel tool calls\n\n"
        "Always call independent tools simultaneously in a single response — "
        "never one at a time. For example, if you need to run three SQL queries "
        "that do not depend on each other, emit all three tool calls at once "
        "rather than waiting for each result before issuing the next. "
        "The executor runs them concurrently so there is zero benefit to "
        "sequential calls. Only call tools sequentially when a later call "
        "genuinely requires the output of an earlier one."
        "\n\n---\n\n"
        "## Reasoning transparency\n\n"
        "When you decide to call one or more tools, write one sentence on its own line "
        "starting with \"Reason:\" before the tool calls. State specifically what "
        "information you are missing and what you expect the tools to return. "
        "Example: \"Reason: I need the table name for wafer starts before I can write the SQL query.\"\n\n"
        "After receiving tool results, begin your response with one of:\n"
        "  \"Continue: <one sentence — what is still missing and why you need more tools>\"\n"
        "  \"Answer: <one sentence — what the tools confirmed and what you will now say>\"\n"
        "Do not omit this prefix. It helps the user understand your process."
    )

    context_trace: dict = {
        "agents_md_chars": len(agents_md),
        "soul_md_chars": len(soul_md),
        "skills": skill_entries,
        "memory_excerpts": memory_excerpts,
        "mcp_resources_chars": mcp_resources_chars,
        "rag": rag_matches,
        "tools_count": 0,       # filled in by call_llm after tool schema construction
        "mcp_servers": [],      # filled in by call_llm
        "history_messages": 0,  # filled in by call_llm
        "history_dropped": 0,   # filled in by call_llm
        "total_prompt_chars": len(content),
    }

    return content, triggered, rag_matches, context_trace, skill_dir_map


def _get_agent_model(state: dict) -> str:
    """Return model name for this agent. Cached in state after first lookup."""
    if state.get("_agent_model"):
        return state["_agent_model"]
    from agent.models import Agent
    try:
        agent = Agent.objects.get(pk=state["agent_id"])
        return agent.model or settings.LITELLM_DEFAULT_MODEL
    except Agent.DoesNotExist:
        return settings.LITELLM_DEFAULT_MODEL


# Tools whose identity should be based on name + stable key arg only, not full args.
# The LLM often rephrases free-text input between rounds, which produces a different
# hash even though it's logically the same call.
_NAME_ONLY_DEDUP_TOOLS: frozenset[str] = frozenset({
    "run_skill",  # deduplicate by skill_name only (ignore the free-text input)
    "chart",      # deduplicate by title only (ignore cosmetic differences in labels/values)
})


def _tool_sig(tool_name: str, args: dict) -> str:
    """Return a stable dedup signature for a tool call.

    For tools in _NAME_ONLY_DEDUP_TOOLS we use a reduced key so that minor
    argument rephrasing by the LLM doesn't produce a different signature.
    """
    import hashlib as _h
    import json as _j
    if tool_name == "run_skill":
        # Dedup on skill_name only — ignore the free-text input the LLM passes in.
        key = {"skill_name": args.get("skill_name", "")}
    elif tool_name == "chart":
        # Dedup on title only — enough to prevent the same chart being re-generated.
        key = {"title": args.get("title", "")}
    else:
        key = args
    return f"{tool_name}|{_h.md5(_j.dumps(key, sort_keys=True).encode()).hexdigest()}"


# ── nodes ──────────────────────────────────────────────────────────────────


def assemble_context(state: AgentState) -> dict:
    """No-op pass-through. Context assembly is done inside call_llm."""
    return {}


def call_llm(state: AgentState) -> dict:
    """Assemble context and call the LLM. Returns tool calls or final reply."""
    from core.llm import get_completion
    from agent.tools import all_tools
    from agent.skills import registry as skill_registry

    # Cancellation check — if the run was marked FAILED (e.g. by Cancel Run button)
    # while the Celery task was in-flight, abort here before calling the LLM.
    try:
        from agent.models import AgentRun as _AgentRun
        _current = _AgentRun.objects.filter(pk=state["run_id"]).values_list("status", flat=True).first()
        if _current == _AgentRun.Status.FAILED:
            logger.info("AgentRun %s cancelled — aborting call_llm", state["run_id"])
            return {"output": "", "pending_tool_calls": []}
    except Exception:
        pass

    model = _get_agent_model(state)
    system_content, triggered_skills, rag_matches, context_trace, skill_dir_map = _build_system_context(state.get("input", ""))

    # Append conversation_id to system prompt so agent can reference it in workflows
    conversation_id = state.get("conversation_id")
    if conversation_id:
        system_content += f"\n\n---\n\nCurrent conversation ID: `{conversation_id}`"

    # Build message list
    messages: list[dict] = [{"role": "system", "content": system_content}]

    if state.get("conversation_id"):
        from chat.models import Message as ChatMessage
        chat_msgs = list(
            ChatMessage.objects.filter(conversation_id=state["conversation_id"])
            .order_by("created_at")
            .values("role", "content")
        )
        # Strip assistant messages that are capability disclaimers or LLM errors —
        # they cause the agent to re-attempt already-completed tasks on every round.
        _error_prefixes = (
            "llm error:",
            "i currently cannot",
            "i can't execute",
            "i can't create",
            "i am unable",
            "i'm unable",
            "since i can't",
            "currently, i'm unable",
            "i cannot execute any further",
            "unfortunately, i'm unable",
            "unfortunately, i cannot",
            "i apologize",
        )
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in chat_msgs
            if not (
                m["role"] == "assistant"
                and any((m["content"] or "").lower().strip().startswith(p) for p in _error_prefixes)
            )
        ]
        # Only keep the last AGENT_HISTORY_WINDOW turns (default 10 messages = ~5 exchanges)
        # to prevent poisoned or irrelevant old history from confusing the agent.
        history_window = getattr(settings, "AGENT_HISTORY_WINDOW", 10)
        if len(history) > history_window:
            history = history[-history_window:]
        _history_before = len(history)
        history = _truncate_history(history, settings.AGENT_CONTEXT_BUDGET_TOKENS, model)
        context_trace["history_messages"] = len(history)
        context_trace["history_dropped"] = _history_before - len(history)
        messages.extend(history)
    else:
        messages.append({"role": "user", "content": state["input"]})

    # When tool results exist, the preceding assistant message with tool_calls must
    # appear first — otherwise the API rejects the request.
    # Only inject when EVERY tool_call_id in the assistant message has a matching
    # result — a partial match would cause an API error ("tool_call_ids did not have
    # response messages").  Stale state from a prior round is silently skipped.
    tool_results = state.get("tool_results", [])
    assistant_tool_msg = state.get("assistant_tool_call_message")
    if tool_results and assistant_tool_msg:
        required_ids = {tc["id"] for tc in assistant_tool_msg.get("tool_calls", [])}
        result_ids = {tr["tool_call_id"] for tr in tool_results}
        if required_ids and required_ids.issubset(result_ids):
            # All tool calls have results — safe to inject.
            current_results = [tr for tr in tool_results if tr["tool_call_id"] in required_ids]
            messages.append(assistant_tool_msg)
            for tr in current_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": json.dumps(tr["result"]),
                })
        else:
            logger.warning(
                "assemble_context: skipping stale assistant_tool_call_message — "
                "missing results for ids: %s",
                required_ids - result_ids,
            )

    # If tool outputs produced markdown (e.g. chart images), remind the LLM
    # to include them verbatim and — crucially — to stop calling more tools.
    collected_markdown = list(state.get("collected_markdown") or [])
    if collected_markdown:
        verbatim = "\n".join(collected_markdown)
        if not tool_results:
            # Concluding round (no new tool results to process)
            messages.append({
                "role": "user",
                "content": (
                    f"Please include the following markdown verbatim in your response "
                    f"(copy it exactly — do not describe or paraphrase it):\n\n{verbatim}"
                ),
            })
        else:
            # Tool results are present but a chart/image was already generated
            # in a prior round — tell the LLM the task is essentially complete.
            messages.append({
                "role": "user",
                "content": (
                    "A chart or visual output has already been generated successfully. "
                    "You can now compose your final answer using the results you already "
                    "have. Please include the following markdown verbatim in your "
                    f"response:\n\n{verbatim}"
                ),
            })

    # Build tool schemas — filtered to only tools enabled on this agent
    from agent.models import Agent as AgentModel
    try:
        agent_obj = AgentModel.objects.get(pk=state["agent_id"])
        enabled_tools: list[str] = agent_obj.tools or []
    except AgentModel.DoesNotExist:
        enabled_tools = []

    all_builtin = all_tools()
    if enabled_tools:
        tools_schema = [
            t.to_llm_schema()
            for name, t in all_builtin.items()
            if name in enabled_tools
        ]
        # Auto-inject tools declared as required by triggered skills but absent
        # from the agent's enabled_tools list.
        # Reuse skill_dir_map returned by _build_system_context (no second collect_all_skills call).
        if triggered_skills:
            import yaml as _yaml
            auto_inject: set[str] = set()
            for skill_name in triggered_skills:
                skill_dir = skill_dir_map.get(skill_name)
                if skill_dir is None:
                    continue
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    try:
                        raw = skill_md.read_text(encoding="utf-8")
                        if raw.startswith("---"):
                            parts = raw.split("---", 2)
                            if len(parts) >= 3:
                                meta = _yaml.safe_load(parts[1]) or {}
                                for t in meta.get("tools", []):
                                    if t not in enabled_tools:
                                        auto_inject.add(t)
                    except Exception:
                        pass
                # run_skill is always needed when a handler exists
                if (skill_dir / "handler.py").exists():
                    if "run_skill" not in enabled_tools:
                        auto_inject.add("run_skill")
            for tool_name in auto_inject:
                if tool_name in all_builtin:
                    tools_schema.append(all_builtin[tool_name].to_llm_schema())
    else:
        tools_schema = []

    # Note: we intentionally do NOT call skill_registry.to_llm_tools() here.
    # Handler-based skills are invoked via the `run_skill` built-in tool, which
    # is auto-injected when a triggered skill has a handler.py.  Exposing them
    # as separate `skill_<name>` functions would create duplicate tool paths
    # that confuse the LLM and lack a matching executor in execute_tools.
    try:
        from agent.mcp.registry import get_registry as get_mcp_registry
        tools_schema.extend(get_mcp_registry().to_llm_schemas())
    except Exception:
        pass

    # Fetch run object for LLMUsage tracking + triggered_skills — single DB read
    _run_obj = None
    try:
        from agent.models import AgentRun
        _run_obj = AgentRun.objects.get(pk=state["run_id"])
        update_fields = {}
        if triggered_skills:
            update_fields["triggered_skills"] = triggered_skills
        gs = _run_obj.graph_state or {}
        if rag_matches:
            gs["rag_matches"] = rag_matches
        # Store names of connected MCP servers included in this call's tool list
        try:
            from agent.mcp.registry import get_registry as get_mcp_registry
            mcp_server_names = sorted({
                entry.server_name
                for entry in get_mcp_registry().all().values()
            }) if tools_schema else []
        except Exception:
            mcp_server_names = []
        if mcp_server_names:
            gs["mcp_servers_active"] = mcp_server_names
        # Persist context_trace on the first round only (it describes the initial context load)
        if state.get("tool_call_rounds", 0) == 0:
            context_trace["tools_count"] = len(tools_schema)
            context_trace["mcp_servers"] = mcp_server_names
            gs["context_trace"] = context_trace
        update_fields["graph_state"] = gs
        if update_fields:
            AgentRun.objects.filter(pk=state["run_id"]).update(**update_fields)
    except Exception:
        pass

    try:
        _round_start_ts = timezone.now().timestamp()
        response = get_completion(
            messages,
            model=model,
            source="agent",
            run=_run_obj,
            tools=tools_schema if tools_schema else None,
        )
    except Exception as exc:
        logger.exception("LLM call failed in AgentRun %s: %s", state.get("run_id"), exc)
        return {"output": f"LLM error: {exc}", "pending_tool_calls": []}

    _llm_ms = round((timezone.now().timestamp() - _round_start_ts) * 1000)

    choice = response.choices[0]
    message = choice.message

    # ── Loop trace: record this round's decision ──────────────────────────
    current_round = state.get("tool_call_rounds", 0) + 1
    loop_trace = list(state.get("loop_trace") or [])

    # When tool_results were present in state (subsequent round), the LLM should
    # have started its response with "Continue: ..." or "Answer: ...".
    # Extract and write back to the previous round's trace entry as continue_reason.
    _content_raw = (message.content or "").strip()
    if state.get("tool_results") and loop_trace:
        if _content_raw.startswith("Continue: ") or _content_raw.startswith("Answer: "):
            _continue_reason = _content_raw.split("\n", 1)[0]
        elif _content_raw:
            _continue_reason = _content_raw[:100]
        else:
            _continue_reason = None
        if _continue_reason:
            loop_trace[-1] = {**loop_trace[-1], "continue_reason": _continue_reason}

    if message.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments or "{}"),
            }
            for tc in message.tool_calls
        ]

        # Strip "Reason:" prefix from reasoning content (the Continue:/Answer: prefix
        # was already consumed above for the previous round's continue_reason).
        _reasoning = _content_raw
        if _reasoning.lower().startswith("reason:"):
            _reasoning = _reasoning[len("reason:"):].strip()
        elif _reasoning.lower().startswith("continue: ") or _reasoning.lower().startswith("answer: "):
            # Already captured as continue_reason; strip it from reasoning
            _after_prefix = _reasoning.split("\n", 1)
            _reasoning = _after_prefix[1].strip() if len(_after_prefix) > 1 else ""
            if _reasoning.lower().startswith("reason:"):
                _reasoning = _reasoning[len("reason:"):].strip()

        trace_entry = {
            "round": current_round,
            "decision": "tool_call",
            "tools": [tc.function.name for tc in message.tool_calls],
            "reasoning": _reasoning or None,
            # Count of parallel calls in this round — >1 means they run concurrently
            "parallel_count": len(message.tool_calls),
            # tool_wall_ms and tool_count are filled in by execute_tools after the batch completes
            "tool_wall_ms": None,
            "tool_count": 0,
            "forced": False,
            # ts = Unix timestamp when this round's LLM call started (before get_completion)
            "ts": _round_start_ts,
            # llm_ms = time the LLM took to respond for this round
            "llm_ms": _llm_ms,
        }
        loop_trace.append(trace_entry)

        # Persist loop_trace to graph_state for UI display
        try:
            from agent.models import AgentRun as _AR
            _ar = _AR.objects.get(pk=state["run_id"])
            gs = _ar.graph_state or {}
            gs["loop_trace"] = loop_trace
            _AR.objects.filter(pk=state["run_id"]).update(graph_state=gs)
        except Exception:
            pass

        # Preserve the full assistant message so it can precede tool results next round.
        assistant_tool_call_message = {
            "role": "assistant",
            "content": message.content if message.content else None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in message.tool_calls
            ],
        }
        return {
            "pending_tool_calls": tool_calls,
            "assistant_tool_call_message": assistant_tool_call_message,
            # Clear stale results from the previous round so they don't cause an
            # ID mismatch when call_llm is invoked again after execute_tools.
            "tool_results": [],
            "loop_trace": loop_trace,
        }

    # Final answer — no tool calls
    # Capture the Answer:/Continue: prefix as reasoning for this round.
    # If this follows tool results, the continue_reason was already written to
    # the previous round above; here we store just the prefix line itself.
    _final_reasoning: str | None = None
    if _content_raw.lower().startswith("answer: ") or _content_raw.lower().startswith("continue: "):
        _final_reasoning = _content_raw.split("\n", 1)[0]
    elif _content_raw:
        # No structured prefix — store the first sentence (up to 120 chars)
        _first_sentence = _content_raw.split(".")[0].strip()
        _final_reasoning = (_first_sentence[:120] + "…") if len(_first_sentence) > 120 else (_first_sentence or None)

    trace_entry = {
        "round": current_round,
        "decision": "answer",
        "tools": [],
        "reasoning": _final_reasoning,
        "tool_wall_ms": None,
        "tool_count": 0,
        "forced": False,
        "ts": _round_start_ts,
        "llm_ms": _llm_ms,
    }
    loop_trace.append(trace_entry)

    # Persist loop_trace to graph_state for UI display
    try:
        from agent.models import AgentRun as _AR
        _ar = _AR.objects.get(pk=state["run_id"])
        gs = _ar.graph_state or {}
        gs["loop_trace"] = loop_trace
        _AR.objects.filter(pk=state["run_id"]).update(graph_state=gs)
    except Exception:
        pass

    return {"output": message.content or "", "pending_tool_calls": [], "assistant_tool_call_message": None, "tool_results": [], "loop_trace": loop_trace}


def check_approval(state: AgentState) -> dict:
    """Check each pending tool call against its approval policy."""
    from agent.tools import get_tool
    from agent.tools.base import ApprovalPolicy
    from agent.models import AgentRun, ToolExecution

    pending = state.get("pending_tool_calls", [])
    run = AgentRun.objects.get(pk=state["run_id"])
    current_round = state.get("tool_call_rounds", 0) + 1

    failed_sigs = list(state.get("failed_tool_signatures") or [])
    succeeded_sigs = list(state.get("succeeded_tool_signatures") or [])

    # Pre-filter: drop any tool calls that already succeeded or failed this run.
    # This prevents ToolExecution rows being created for calls we'll never execute.
    filtered_pending = []
    dropped_results = []
    for tc in pending:
        sig = _tool_sig(tc["name"], tc.get("arguments", {}))
        if sig in succeeded_sigs:
            dropped_results.append({
                "tool_call_id": tc["id"],
                "result": {"error": f"Tool '{tc['name']}' already ran successfully with these arguments this session. Do not call it again — use the previous result to compose your final answer."},
            })
        elif sig in failed_sigs:
            dropped_results.append({
                "tool_call_id": tc["id"],
                "result": {"error": f"Tool '{tc['name']}' already failed with these arguments. Do not retry — use a different approach or report the error."},
            })
        else:
            filtered_pending.append(tc)

    # If ALL calls were dropped, force conclusion — do not give the LLM another
    # chance to issue tool calls, it will just loop. Route to force_conclude instead.
    if dropped_results and not filtered_pending:
        return {
            "pending_tool_calls": [],
            "tool_results": dropped_results,
            "tool_call_rounds": state.get("tool_call_rounds", 0) + 100,  # trigger force_conclude
            "waiting_for_approval": False,
        }

    # Replace pending with the filtered list for the rest of approval logic.
    # dropped_results will be returned alongside pending_tool_calls so that
    # execute_tools can merge them with the results it actually runs.
    pending = filtered_pending

    needs_approval = []
    auto_execute = []

    # Workflow runs are unattended — auto-approve everything.
    workflow_run = run.trigger_source == AgentRun.TriggerSource.WORKFLOW

    for tc in pending:
        tool_name = tc["name"]
        tool = get_tool(tool_name)
        approval_reason = ""
        if workflow_run:
            requires = False
            approval_reason = "auto_workflow"
        elif tool is not None:
            if hasattr(tool, "requires_approval_for"):
                requires = tool.requires_approval_for(tc.get("arguments", {}))
            else:
                requires = tool.approval_policy == ApprovalPolicy.REQUIRES_APPROVAL
            approval_reason = "requires_human" if requires else "policy_allow"
        else:
            # Check MCP registry
            try:
                from agent.mcp.registry import get_registry as get_mcp_registry
                from agent.models import MCPServer
                mcp_entry = get_mcp_registry().get(tool_name)
                if mcp_entry:
                    server = MCPServer.objects.filter(name=mcp_entry.server_name).first()
                    in_auto_list = server is not None and mcp_entry.tool_name in (server.auto_approve_tools or [])
                    requires = not in_auto_list
                    approval_reason = "auto_approve_list" if in_auto_list else "requires_human"
                elif "__" in tool_name:
                    requires = False
                    approval_reason = "policy_allow"
                else:
                    requires = True
                    approval_reason = "requires_human"
            except Exception:
                requires = False if "__" in tool_name else True
                approval_reason = "policy_allow" if not requires else "requires_human"
        if requires:
            te = ToolExecution.objects.create(
                run=run,
                tool_name=tool_name,
                input=tc.get("arguments", {}),
                status=ToolExecution.Status.PENDING,
                requires_approval=True,
                approval_reason=approval_reason,
                round=current_round,
            )
            needs_approval.append({**tc, "tool_execution_id": str(te.id)})
        else:
            # Create an audit record for auto-approved tools too so they appear in the run trace.
            te = ToolExecution.objects.create(
                run=run,
                tool_name=tool_name,
                input=tc.get("arguments", {}),
                status=ToolExecution.Status.RUNNING,
                requires_approval=False,
                approval_reason=approval_reason,
                round=current_round,
            )
            auto_execute.append({**tc, "tool_execution_id": str(te.id)})

    if needs_approval:
        run.status = AgentRun.Status.WAITING
        run.graph_state = {
            "pending_tool_calls": needs_approval,
            "tool_results": [],
            "assistant_tool_call_message": state.get("assistant_tool_call_message"),
            "failed_tool_signatures": state.get("failed_tool_signatures") or [],
            "succeeded_tool_signatures": state.get("succeeded_tool_signatures") or [],
            "collected_markdown": state.get("collected_markdown") or [],
        }
        run.save(update_fields=["status", "graph_state"])
        return {
            "pending_tool_calls": needs_approval,
            "waiting_for_approval": True,
        }

    return {
        "pending_tool_calls": auto_execute,
        "waiting_for_approval": False,
        # Pre-populate tool_results with responses for any calls that were dropped
        # (already succeeded/failed); execute_tools will merge its own results in.
        "tool_results": dropped_results,
    }


def execute_tools(state: AgentState) -> dict:
    """Execute all pending tool calls and collect results."""
    from agent.tools import get_tool
    from agent.tools.base import ToolTimeoutError
    from agent.models import AgentRun, ToolExecution
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Cancellation check — abort before running any tools if run was cancelled.
    try:
        _status = AgentRun.objects.filter(pk=state["run_id"]).values_list("status", flat=True).first()
        if _status == AgentRun.Status.FAILED:
            logger.info("AgentRun %s cancelled — aborting execute_tools", state["run_id"])
            return {"tool_results": [], "pending_tool_calls": [], "tool_call_rounds": state.get("tool_call_rounds", 0) + 1, "visited_urls": list(state.get("visited_urls") or []), "failed_tool_signatures": list(state.get("failed_tool_signatures") or [])}
    except Exception:
        pass

    import hashlib as _hashlib
    import json as _json

    pending = state.get("pending_tool_calls", [])
    # Pre-populated by check_approval for any calls that were dropped (already succeeded/failed).
    tool_results = list(state.get("tool_results") or [])
    visited_urls = list(state.get("visited_urls") or [])
    failed_sigs = list(state.get("failed_tool_signatures") or [])
    succeeded_sigs = list(state.get("succeeded_tool_signatures") or [])
    collected_markdown = list(state.get("collected_markdown") or [])
    search_result_urls = list(state.get("search_result_urls") or [])
    blocked_mcp_servers = list(state.get("blocked_mcp_servers") or [])

    # ── Pre-filter: dedup / blocked-server checks (must be sequential, mutates lists) ──

    # Serial tools run one-at-a-time (stateful side-effects).
    _SERIAL_TOOLS = {"file_write", "shell_exec"}

    parallel_tcs = []  # safe to run concurrently
    serial_tcs = []    # must run sequentially after parallel batch

    for tc in pending:
        tool_name = tc["name"]
        args = tc.get("arguments", {})

        # Block duplicate URL fetches across ALL url-based tools.
        if tool_name in ("web_read", "api_get", "api_post"):
            url = args.get("url", "")
            if url and url in visited_urls:
                tc_id = tc["id"]
                tool_results.append({
                    "tool_call_id": tc_id,
                    "result": {"error": f"Already fetched {url} — use the content from the previous call. Do not retry this URL with any tool."},
                })
                continue
            if url:
                visited_urls.append(url)

        sig = _tool_sig(tool_name, args)
        if sig in failed_sigs:
            tc_id = tc["id"]
            tool_results.append({
                "tool_call_id": tc_id,
                "result": {"error": f"Tool '{tool_name}' already failed with these arguments. Do not retry — use a different approach or report the error."},
            })
            continue
        if sig in succeeded_sigs:
            tc_id = tc["id"]
            tool_results.append({
                "tool_call_id": tc_id,
                "result": {"error": f"Tool '{tool_name}' already ran successfully with these arguments this session. Do not call it again — use the previous result to complete your response."},
            })
            continue
        # Block all tools from an MCP server that failed to resolve in a previous round.
        if "__" in tool_name:
            server_prefix = tool_name.split("__")[0]
            if server_prefix in blocked_mcp_servers:
                tc_id = tc["id"]
                tool_results.append({
                    "tool_call_id": tc_id,
                    "result": {"error": f"MCP server '{server_prefix}' is unavailable in this session. Do not call any of its tools — report that you could not access it."},
                })
                failed_sigs.append(sig)
                continue

        if tool_name in _SERIAL_TOOLS:
            serial_tcs.append(tc)
        else:
            parallel_tcs.append(tc)

    # ── Per-call executor ──────────────────────────────────────────────────────────

    def _run_one(tc: dict) -> dict:
        """Execute a single tool call. Returns a result dict (thread-safe)."""
        import time as _time
        tool_name = tc["name"]
        args = tc.get("arguments", {})
        tc_id = tc["id"]
        sig = _tool_sig(tool_name, args)

        te_id = tc.get("tool_execution_id")
        te = None
        if te_id:
            try:
                te = ToolExecution.objects.get(pk=te_id)
            except ToolExecution.DoesNotExist:
                pass

        tool = get_tool(tool_name)
        if tool is not None:
            if te:
                te.status = ToolExecution.Status.RUNNING
                te.save(update_fields=["status"])
            try:
                tool_result = tool.execute(**args)
                result = tool_result.as_dict()
                if te:
                    te.status = (
                        ToolExecution.Status.SUCCESS
                        if tool_result.success
                        else ToolExecution.Status.ERROR
                    )
                    te.output = result
                    te.duration_ms = tool_result.duration_ms
                    te.save(update_fields=["status", "output", "duration_ms"])
            except ToolTimeoutError as exc:
                result = {"error": str(exc)}
                if te:
                    te.status = ToolExecution.Status.ERROR
                    te.output = result
                    te.save(update_fields=["status", "output"])
        else:
            # Try MCP registry
            try:
                from agent.mcp.registry import get_registry as get_mcp_registry
                from agent.mcp.pool import MCPConnectionPool
                from agent.mcp.client import MCPTimeoutError as MCPTimeout
                mcp_entry = get_mcp_registry().get(tool_name)
                if mcp_entry:
                    # Check connection before attempting the call
                    pool = MCPConnectionPool.get()
                    if pool.get_status(mcp_entry.server_name) != "connected":
                        result = {"error": f"MCP server '{mcp_entry.server_name}' is not connected in this worker. The server may need to be reconnected — please check the MCP management page."}
                        if te:
                            te.status = ToolExecution.Status.ERROR
                            te.output = result
                            te.save(update_fields=["status", "output"])
                    else:
                        if te:
                            te.status = ToolExecution.Status.RUNNING
                            te.save(update_fields=["status"])
                        start = _time.monotonic()
                        try:
                            mcp_result = pool.call_tool(
                                mcp_entry.server_name, mcp_entry.tool_name, args
                            )
                            result = {"output": mcp_result}
                            duration = int((_time.monotonic() - start) * 1000)
                            if te:
                                te.status = ToolExecution.Status.SUCCESS
                                te.output = result
                                te.duration_ms = duration
                                te.save(update_fields=["status", "output", "duration_ms"])
                        except MCPTimeout as exc:
                            result = {"error": str(exc)}
                            if te:
                                te.status = ToolExecution.Status.ERROR
                                te.output = result
                                te.save(update_fields=["status", "output"])
                        except Exception as exc:
                            err_msg = str(exc) or f"{type(exc).__name__} (no message)"
                            result = {"error": f"MCP tool call failed: {err_msg}"}
                            if te:
                                te.status = ToolExecution.Status.ERROR
                                te.output = result
                                te.save(update_fields=["status", "output"])
                else:
                    result = {"error": f"Tool '{tool_name}' not found in MCP registry — server may not be connected. Do not retry."}
                    if te:
                        te.status = ToolExecution.Status.ERROR
                        te.output = result
                        te.save(update_fields=["status", "output"])
            except Exception as exc:
                err_msg = str(exc) or f"{type(exc).__name__} (no message)"
                result = {"error": f"Tool execution error: {err_msg}"}
                if te:
                    te.status = ToolExecution.Status.ERROR
                    te.output = result
                    te.save(update_fields=["status", "output"])

        duration_ms = te.duration_ms if te else None
        return {"tc": tc, "result": result, "sig": sig, "duration_ms": duration_ms}

    # ── Stamp parallel / serial group IDs on ToolExecutions ─────────────────────
    # All TEs dispatched in the same concurrent batch share a parallel_group ID
    # (8-char hex) so the UI can bracket them together and mark the critical path.
    import uuid as _uuid

    if parallel_tcs:
        _parallel_group = _uuid.uuid4().hex[:8]
        _is_parallel = len(parallel_tcs) > 1
        for _tc in parallel_tcs:
            _te_id = _tc.get("tool_execution_id")
            if _te_id:
                ToolExecution.objects.filter(pk=_te_id).update(
                    parallel_group=_parallel_group,
                    is_serial=False,
                )

    if serial_tcs:
        _serial_group = _uuid.uuid4().hex[:8]
        for _tc in serial_tcs:
            _te_id = _tc.get("tool_execution_id")
            if _te_id:
                ToolExecution.objects.filter(pk=_te_id).update(
                    parallel_group=_serial_group,
                    is_serial=True,
                )

    # ── Run parallel batch ────────────────────────────────────────────────────────

    max_workers = getattr(settings, "AGENT_TOOL_PARALLELISM", 8)
    parallel_outcomes: list[dict] = []

    if parallel_tcs:
        if len(parallel_tcs) == 1:
            # No need for thread overhead when there's only one call.
            parallel_outcomes.append(_run_one(parallel_tcs[0]))
        else:
            workers = min(len(parallel_tcs), max_workers)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_run_one, tc): tc for tc in parallel_tcs}
                for future in as_completed(futures):
                    try:
                        parallel_outcomes.append(future.result())
                    except Exception as exc:
                        tc = futures[future]
                        parallel_outcomes.append({
                            "tc": tc,
                            "result": {"error": f"Tool execution error: {exc}"},
                            "sig": _tool_sig(tc["name"], tc.get("arguments", {})),
                        })

    # ── Run serial batch (file_write, shell_exec) ────────────────────────────────

    serial_outcomes: list[dict] = [_run_one(tc) for tc in serial_tcs]

    # ── Post-process all outcomes ─────────────────────────────────────────────────

    for outcome in parallel_outcomes + serial_outcomes:
        tc = outcome["tc"]
        result = outcome["result"]
        sig = outcome["sig"]
        tool_name = tc["name"]
        tc_id = tc["id"]

        tool_results.append({"tool_call_id": tc_id, "result": result})

        if result.get("error"):
            failed_sigs.append(sig)

            # Block MCP server if registry miss
            if not get_tool(tool_name) and "__" in tool_name:
                server_prefix = tool_name.split("__")[0]
                if server_prefix not in blocked_mcp_servers:
                    if "unavailable" in result.get("error", ""):
                        blocked_mcp_servers.append(server_prefix)
                        logger.warning(
                            "MCP server '%s' blocked for this run — tools not in registry",
                            server_prefix,
                        )

            # ── Auto-fallback for web_read ────────────────────────────────────
            if tool_name == "web_read":
                max_fallback = getattr(settings, "AGENT_WEB_READ_FALLBACK_LIMIT", 3)
                fallback_tried = 0
                for fallback_url in list(search_result_urls):
                    if fallback_tried >= max_fallback:
                        break
                    if fallback_url in visited_urls:
                        continue
                    visited_urls.append(fallback_url)
                    fallback_tried += 1
                    logger.info("web_read auto-fallback: trying %s", fallback_url)
                    fallback_tool = get_tool("web_read")
                    try:
                        fallback_result = fallback_tool.execute(url=fallback_url)
                        fb_dict = fallback_result.as_dict()
                        fb_sig = _tool_sig("web_read", {"url": fallback_url})
                        if fallback_result.success:
                            succeeded_sigs.append(fb_sig)
                            tool_results[-1] = {"tool_call_id": tc_id, "result": fb_dict}
                            run_obj = AgentRun.objects.filter(pk=state["run_id"]).first()
                            if run_obj:
                                ToolExecution.objects.create(
                                    run=run_obj,
                                    tool_name="web_read",
                                    input={"url": fallback_url},
                                    output=fb_dict,
                                    status=ToolExecution.Status.SUCCESS,
                                    duration_ms=fallback_result.duration_ms,
                                    requires_approval=False,
                                )
                            logger.info("web_read auto-fallback succeeded: %s", fallback_url)
                            break
                        else:
                            failed_sigs.append(fb_sig)
                            run_obj = AgentRun.objects.filter(pk=state["run_id"]).first()
                            if run_obj:
                                ToolExecution.objects.create(
                                    run=run_obj,
                                    tool_name="web_read",
                                    input={"url": fallback_url},
                                    output=fb_dict,
                                    status=ToolExecution.Status.ERROR,
                                    duration_ms=fallback_result.duration_ms,
                                    requires_approval=False,
                                )
                    except Exception as fb_exc:
                        failed_sigs.append(_tool_sig("web_read", {"url": fallback_url}))
                        logger.warning("web_read auto-fallback error for %s: %s", fallback_url, fb_exc)
        else:
            succeeded_sigs.append(sig)
            output = result.get("output", {})
            if isinstance(output, dict):
                if output.get("markdown"):
                    collected_markdown.append(output["markdown"])
                elif isinstance(output.get("result"), str):
                    import re as _re
                    _md_imgs = _re.findall(r"!\[[^\]]*\]\([^)]+\)", output["result"])
                    if _md_imgs:
                        collected_markdown.extend(_md_imgs)

            if tool_name == "web_search":
                search_output = result.get("output", {})
                if isinstance(search_output, dict):
                    for sr in search_output.get("results", []):
                        url = sr.get("url", "")
                        if url and url not in search_result_urls:
                            search_result_urls.append(url)

    rounds = state.get("tool_call_rounds", 0) + 1
    # Count consecutive rounds where every tool call failed — used by the router
    # to force-conclude early instead of letting the LLM retry indefinitely.
    all_failed = all(r["result"].get("error") for r in tool_results) if tool_results else False
    consecutive_failed = state.get("consecutive_failed_rounds", 0)
    consecutive_failed = consecutive_failed + 1 if all_failed else 0

    # ── Write tool_wall_ms and tool_count back to the current round's loop_trace entry ──
    _dispatched_count = len(parallel_tcs) + len(serial_tcs)
    _parallel_durations = [o.get("duration_ms") for o in parallel_outcomes if o.get("duration_ms")]
    _tool_wall_ms: int | None = max(_parallel_durations) if _parallel_durations else None
    try:
        from agent.models import AgentRun as _AR2
        _ar2 = _AR2.objects.get(pk=state["run_id"])
        _gs2 = _ar2.graph_state or {}
        _trace2 = _gs2.get("loop_trace", [])
        if _trace2:
            _trace2[-1]["tool_wall_ms"] = _tool_wall_ms
            _trace2[-1]["tool_count"] = _dispatched_count
            _gs2["loop_trace"] = _trace2
            _AR2.objects.filter(pk=state["run_id"]).update(graph_state=_gs2)
    except Exception:
        pass

    return {
        "tool_results": tool_results,
        "pending_tool_calls": [],
        "tool_call_rounds": rounds,
        "visited_urls": visited_urls,
        "failed_tool_signatures": failed_sigs,
        "succeeded_tool_signatures": succeeded_sigs,
        "collected_markdown": collected_markdown,
        "search_result_urls": search_result_urls,
        "blocked_mcp_servers": blocked_mcp_servers,
        "consecutive_failed_rounds": consecutive_failed,
    }


def force_conclude(state: AgentState) -> dict:
    """Called when tool_call_rounds hits the limit, or when all tool calls were
    already-completed duplicates. Ask the LLM to conclude with what it has."""
    from core.llm import get_completion
    from agent.models import AgentRun

    model = _get_agent_model(state)
    system_content, _, _, _, _ = _build_system_context(state.get("input", ""))

    # Collect any markdown-bearing results (e.g. chart images) so they are
    # always preserved verbatim regardless of what the LLM does with them.
    # Use collected_markdown from state (persisted by execute_tools across rounds)
    # since tool_results at this point may only contain dedup error strings.
    tool_results = state.get("tool_results", [])
    markdown_snippets: list[str] = list(state.get("collected_markdown") or [])

    messages: list[dict] = [{"role": "system", "content": system_content}]
    if state.get("conversation_id"):
        from chat.models import Message as ChatMessage
        chat_msgs = list(
            ChatMessage.objects.filter(conversation_id=state["conversation_id"])
            .order_by("created_at")
            .values("role", "content")
        )
        history = [{"role": m["role"], "content": m["content"]} for m in chat_msgs]
        history = _truncate_history(history, settings.AGENT_CONTEXT_BUDGET_TOKENS, model)
        messages.extend(history)
    else:
        messages.append({"role": "user", "content": state["input"]})

    # Inject the last round's tool exchange so the LLM knows what was accomplished.
    assistant_tool_msg = state.get("assistant_tool_call_message")
    if tool_results and assistant_tool_msg:
        required_ids = {tc["id"] for tc in assistant_tool_msg.get("tool_calls", [])}
        result_ids = {tr["tool_call_id"] for tr in tool_results}
        if required_ids and required_ids.issubset(result_ids):
            current_results = [tr for tr in tool_results if tr["tool_call_id"] in required_ids]
            messages.append(assistant_tool_msg)
            for tr in current_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": json.dumps(tr["result"]),
                })

    # Tell the LLM to include any markdown verbatim (e.g. chart image syntax).
    markdown_instruction = ""
    if markdown_snippets:
        verbatim = "\n".join(markdown_snippets)
        markdown_instruction = (
            f"\n\nPlease include the following markdown verbatim in your response "
            f"(do not paraphrase or describe it — copy it exactly):\n\n{verbatim}"
        )

    messages.append({
        "role": "user",
        "content": (
            "All required tools have already been used successfully. "
            "Using the results above, please compose your final answer now."
            + markdown_instruction
        ),
    })

    _run_obj = None
    try:
        from agent.models import AgentRun
        _run_obj = AgentRun.objects.get(pk=state["run_id"])
    except Exception:
        pass

    try:
        response = get_completion(messages, model=model, source="agent", run=_run_obj)
        output = response.choices[0].message.content or ""
    except Exception as exc:
        output = f"Reached tool-use limit. Error generating summary: {exc}"

    # Mark the current round's loop_trace entry (or append one) as forced=True
    try:
        from agent.models import AgentRun as _AR3
        _ar3 = _AR3.objects.get(pk=state["run_id"])
        _gs3 = _ar3.graph_state or {}
        _trace3 = list(_gs3.get("loop_trace") or [])
        _current_round = state.get("tool_call_rounds", 0) + 1
        if _trace3 and _trace3[-1].get("round") == _current_round:
            _trace3[-1]["forced"] = True
        else:
            _fc_reasoning = output.split(".")[0].strip()[:120] if output else None
            _trace3.append({
                "round": _current_round,
                "decision": "answer",
                "tools": [],
                "reasoning": _fc_reasoning or None,
                "tool_wall_ms": None,
                "tool_count": 0,
                "forced": True,
                "ts": timezone.now().timestamp(),
            })
        _gs3["loop_trace"] = _trace3
        _AR3.objects.filter(pk=state["run_id"]).update(graph_state=_gs3)
    except Exception:
        pass

    return {"output": output, "pending_tool_calls": []}


def save_result(state: AgentState) -> dict:
    """Save the final output as a chat.Message and mark AgentRun completed."""
    from chat.models import Message as ChatMessage
    from agent.models import AgentRun

    output = state.get("output", "")
    run = AgentRun.objects.select_related("workflow").get(pk=state["run_id"])

    # Don't overwrite a cancellation — if already FAILED, leave it as-is.
    if run.status == AgentRun.Status.FAILED:
        return {}

    if output and state.get("conversation_id"):
        ChatMessage.objects.create(
            conversation_id=state["conversation_id"],
            role=ChatMessage.Role.ASSISTANT,
            content=output,
            metadata={"run_id": str(state["run_id"])},
        )

    run.output = output
    run.status = AgentRun.Status.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["output", "status", "finished_at"])

    # For workflow runs: deliver the output via the workflow's delivery config,
    # but only for the LAST step. WorkflowRunner sets workflow_step as 0-based index.
    if output and run.workflow_id and run.trigger_source == AgentRun.TriggerSource.WORKFLOW:
        try:
            workflow = run.workflow
            total_steps = len(workflow.definition.get("steps", []))
            is_last_step = (run.workflow_step == total_steps - 1) if total_steps else True
            if is_last_step:
                from agent.workflows.runner import _deliver
                _deliver(workflow, output)
        except Exception as exc:
            logger.warning("save_result: workflow delivery failed for run %s: %s", run.pk, exc)

    return {}
