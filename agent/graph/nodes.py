from __future__ import annotations

import json
import logging
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


def _build_skills_section(query: str) -> tuple[str, list[str]]:
    """Scan workspace/skills/, match against query using embeddings (with keyword fallback).

    Returns (system_prompt_section, triggered_skill_names).
    """
    import re
    import yaml
    from agent.skills.embeddings import find_relevant_skills

    skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
    if not skills_dir.exists():
        return "", []

    query_lower = query.lower()

    # Embedding-based routing (primary)
    embedding_matches: set[str] = set(find_relevant_skills(query))

    index_rows: list[str] = []
    body_sections: list[str] = []
    triggered: list[str] = []

    # Load set of disabled skill names from DB
    from agent.models import Skill
    disabled_skills = set(
        Skill.objects.filter(enabled=False).values_list("name", flat=True)
    )

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
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
        triggers: list[str] = meta.get("triggers", [])
        trigger_patterns: list[str] = meta.get("trigger_patterns", [])

        # Embedding match (primary)
        if name in embedding_matches:
            matched = True
        elif not embedding_matches and not triggers and not trigger_patterns:
            # No embeddings at all in DB yet and no keywords — fall back to always-inject for short skills
            matched = len(body.splitlines()) < 50
        else:
            # Keyword/regex fallback — only fires if the embedding did NOT match
            # and the skill has explicit triggers or patterns configured
            matched = False
            if triggers:
                matched = any(t.lower() in query_lower for t in triggers)
            if not matched and trigger_patterns:
                matched = any(re.search(p, query_lower) for p in trigger_patterns)

        status = "**Active**" if matched else "Available"
        index_rows.append(f"| {name} | {description} | {status} |")

        if matched:
            triggered.append(name)
            heading = f"### {name}"
            if description:
                heading += f"\n{description}"
            body_sections.append(f"{heading}\n\n{body}")

    if not index_rows:
        return "", []

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

    return section, triggered


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


def _build_system_context(query: str) -> tuple[str, list[str], list[dict]]:
    """Assemble system prompt from workspace files, memories, and MCP resources.

    Returns (system_prompt, triggered_skill_names, rag_matches).
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

    skills_section, triggered = _build_skills_section(query)
    if skills_section:
        parts.append(skills_section)

    try:
        from agent.memory.long_term import search_long_term
        excerpts = search_long_term(query, limit=5)
        if excerpts:
            parts.append("## Relevant memories\n\n" + "\n\n".join(excerpts))
    except Exception:
        pass

    try:
        from agent.mcp.pool import MCPConnectionPool
        resources = MCPConnectionPool.get().fetch_always_include_resources()
        if resources:
            parts.append("## MCP Resources\n\n" + "\n\n".join(resources))
    except Exception:
        pass

    # Knowledge base context (auto-injected via RAG)
    rag_matches: list[dict] = []
    try:
        knowledge_section, rag_matches = _build_knowledge_section(query)
        if knowledge_section:
            parts.append(knowledge_section)
    except Exception:
        pass

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
    )

    return content, triggered, rag_matches


def _get_agent_model(agent_id: str) -> str:
    from agent.models import Agent
    try:
        agent = Agent.objects.get(pk=agent_id)
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

    model = _get_agent_model(state["agent_id"])
    system_content, triggered_skills, rag_matches = _build_system_context(state.get("input", ""))

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
        history = _truncate_history(history, settings.AGENT_CONTEXT_BUDGET_TOKENS, model)
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
        if triggered_skills:
            import yaml as _yaml
            skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
            auto_inject: set[str] = set()
            for skill_name in triggered_skills:
                skill_md = skills_dir / skill_name / "SKILL.md"
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
                if (skills_dir / skill_name / "handler.py").exists():
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

    # Fetch run object for LLMUsage tracking + triggered_skills
    _run_obj = None
    try:
        from agent.models import AgentRun
        _run_obj = AgentRun.objects.get(pk=state["run_id"])
        update_fields = {}
        if triggered_skills:
            update_fields["triggered_skills"] = triggered_skills
        if rag_matches or tools_schema:
            # Store RAG document matches and active MCP servers in graph_state for UI display
            run_obj = AgentRun.objects.get(pk=state["run_id"])
            gs = run_obj.graph_state or {}
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
            update_fields["graph_state"] = gs
        if update_fields:
            AgentRun.objects.filter(pk=state["run_id"]).update(**update_fields)
    except Exception:
        pass

    try:
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

    choice = response.choices[0]
    message = choice.message

    # ── Loop trace: record this round's decision ──────────────────────────
    current_round = state.get("tool_call_rounds", 0) + 1
    loop_trace = list(state.get("loop_trace") or [])

    if message.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments or "{}"),
            }
            for tc in message.tool_calls
        ]

        trace_entry = {
            "round": current_round,
            "decision": "tool_call",
            "tools": [tc.function.name for tc in message.tool_calls],
            "reasoning": (message.content or "").strip() or None,
            # Count of parallel calls in this round — >1 means they run concurrently
            "parallel_count": len(message.tool_calls),
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
    trace_entry = {
        "round": current_round,
        "decision": "answer",
        "tools": [],
        "reasoning": None,
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
        if workflow_run:
            requires = False  # all tools auto-approved in workflow context
        elif tool is not None:
            # Allow per-call approval logic (e.g. file_write only requires approval for workflows/)
            if hasattr(tool, "requires_approval_for"):
                requires = tool.requires_approval_for(tc.get("arguments", {}))
            else:
                requires = tool.approval_policy == ApprovalPolicy.REQUIRES_APPROVAL
        else:
            # Check MCP registry
            try:
                from agent.mcp.registry import get_registry as get_mcp_registry
                from agent.models import MCPServer
                mcp_entry = get_mcp_registry().get(tool_name)
                if mcp_entry:
                    server = MCPServer.objects.filter(name=mcp_entry.server_name).first()
                    requires = server is None or mcp_entry.tool_name not in (server.auto_approve_tools or [])
                elif "__" in tool_name:
                    # Looks like an MCP tool (Server__tool format) but registry is
                    # temporarily empty. Auto-execute — execute_tools will handle
                    # the "Unknown tool" error gracefully rather than blocking on approval.
                    requires = False
                else:
                    requires = True  # truly unknown tool → require approval
            except Exception:
                requires = False if "__" in tool_name else True
        if requires:
            te = ToolExecution.objects.create(
                run=run,
                tool_name=tool_name,
                input=tc.get("arguments", {}),
                status=ToolExecution.Status.PENDING,
                requires_approval=True,
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
                    if te:
                        te.status = ToolExecution.Status.RUNNING
                        te.save(update_fields=["status"])
                    start = _time.monotonic()
                    try:
                        mcp_result = MCPConnectionPool.get().call_tool(
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
                else:
                    result = {"error": "MCP server tools unavailable (server not connected). Do not retry — report the error to the user."}
                    if te:
                        te.status = ToolExecution.Status.ERROR
                        te.output = result
                        te.save(update_fields=["status", "output"])
            except Exception as exc:
                result = {"error": f"Tool execution error: {exc}"}
                if te:
                    te.status = ToolExecution.Status.ERROR
                    te.output = result
                    te.save(update_fields=["status", "output"])

        return {"tc": tc, "result": result, "sig": sig}

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

    model = _get_agent_model(state["agent_id"])
    system_content, _, _ = _build_system_context(state.get("input", ""))

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
