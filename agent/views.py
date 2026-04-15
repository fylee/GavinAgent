from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_skill_bodies(skill_names: list[str]) -> dict[str, str]:
    """Return {skill_name: markdown_body} for each triggered skill."""
    import yaml
    from agent.skills.discovery import collect_all_skills
    all_skills = {s["name"]: s["skill_dir"] for s in collect_all_skills(check_db_trust=True) if s["trusted"]}
    result = {}
    for name in skill_names:
        skill_dir = all_skills.get(name)
        if not skill_dir:
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        text = skill_md.read_text(encoding="utf-8")
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].strip()
        result[name] = body
    return result


def _annotate_tool_executions(tool_executions) -> list:
    """
    Annotate each ToolExecution with parallel-group metadata for template rendering:
      - group_size:        total TEs sharing the same parallel_group
      - is_first_in_group: True for the first TE in each group
      - is_parallel_group: True when group_size > 1 (actually ran concurrently)
      - is_serial:         already on the model, True for file_write / shell_exec
    Returns a plain list (materialises the queryset).
    """
    from collections import Counter
    tes = list(tool_executions)
    # Count per group
    group_counts: Counter = Counter(te.parallel_group for te in tes if te.parallel_group)
    seen_groups: set = set()
    for te in tes:
        g = te.parallel_group
        if g:
            te.group_size = group_counts[g]
            te.is_parallel_group = group_counts[g] > 1 and not te.is_serial
            te.is_first_in_group = g not in seen_groups
            seen_groups.add(g)
        else:
            te.group_size = 1
            te.is_parallel_group = False
            te.is_first_in_group = True
        # Resource type classification
        if "__" in te.tool_name:
            te.tool_type = "mcp"
            parts = te.tool_name.split("__", 1)
            te.mcp_server = parts[0].replace("_", " ")
            te.display_name = parts[1] if len(parts) > 1 else te.tool_name
        elif te.tool_name == "run_skill":
            te.tool_type = "skill"
            te.mcp_server = ""
            te.display_name = te.input.get("skill_name", "run_skill") if isinstance(te.input, dict) else te.tool_name
        else:
            te.tool_type = "builtin"
            te.mcp_server = ""
            te.display_name = te.tool_name
    return tes


def _compute_agent_phase(run, gs: dict) -> str:
    """Derive a descriptive phase label from the run's current state."""
    if run.status == "completed":
        return "completed"
    if run.status == "failed":
        return "failed"
    if run.status == "waiting" or gs.get("waiting_for_approval"):
        return "waiting_approval"
    if run.status == "pending":
        return "pending"
    # status == running — determine sub-phase from graph_state
    loop_trace = gs.get("loop_trace", [])
    if not loop_trace:
        return "assembling"
    last_entry = loop_trace[-1]
    if last_entry.get("decision") == "answer":
        return "concluding"
    # Check if any tool executions are currently running
    if run.tool_executions.filter(status="running").exists():
        return "executing"
    return "thinking"


def _build_run_context(run, request=None) -> dict:
    """Build the shared template context dict for run status views."""
    from django.conf import settings as _settings
    tool_executions = _annotate_tool_executions(run.tool_executions.order_by("created_at"))
    gs = run.graph_state or {}
    loop_trace = gs.get("loop_trace", [])

    # Group tool_executions by round for the unified timeline
    te_by_round: dict = {}
    for te in tool_executions:
        r = te.round or 0
        te_by_round.setdefault(r, []).append(te)

    # Embed tool executions inside each loop_trace entry
    loop_trace_with_tes = []
    run_started_ts = run.started_at.timestamp() if run.started_at else None
    for entry in loop_trace:
        round_num = entry.get("round", 0)
        elapsed_s = None
        if run_started_ts and entry.get("ts"):
            elapsed_s = round(entry["ts"] - run_started_ts, 1)
        loop_trace_with_tes.append({**entry, "tool_executions": te_by_round.get(round_num, []), "elapsed_s": elapsed_s})

    max_rounds = getattr(_settings, "AGENT_MAX_TOOL_CALL_ROUNDS", 20)
    max_consec = getattr(_settings, "AGENT_MAX_CONSECUTIVE_FAILED_ROUNDS", 2)

    return {
        "run": run,
        "tool_executions": tool_executions,
        "skill_bodies": _load_skill_bodies(run.triggered_skills or []),
        "rag_matches": gs.get("rag_matches", []),
        "loop_trace": loop_trace_with_tes,
        "context_trace": gs.get("context_trace", {}),
        "agent_phase": _compute_agent_phase(run, gs),
        "max_tool_call_rounds": max_rounds,
        "max_consecutive_failed_rounds": max_consec,
        "tool_call_rounds": gs.get("tool_call_rounds", 0),
        "consecutive_failed_rounds": gs.get("consecutive_failed_rounds", 0),
        "near_round_limit": gs.get("tool_call_rounds", 0) >= int(max_rounds * 0.75),
        "blocked_mcp_servers": gs.get("blocked_mcp_servers", []),
        "mcp_servers_active": gs.get("mcp_servers_active", []),
    }

from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView
from django_htmx.http import HttpResponseClientRedirect

from .models import Agent, AgentRun, HeartbeatLog, LLMUsage, ReembedLog, Skill, ToolExecution, Workflow, KnowledgeDocument
from .tasks import execute_agent_run


# ── Helpers ──────────────────────────────────────────────────────────────────


def _memory_path() -> Path:
    return Path(settings.AGENT_WORKSPACE_DIR) / "memory" / "MEMORY.md"


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _status_badge_class(status: str) -> str:
    mapping = {
        "completed": "bg-green-900 text-green-300",
        "failed": "bg-red-900 text-red-300",
        "running": "bg-blue-900 text-blue-300",
        "waiting": "bg-yellow-900 text-yellow-300",
        "pending": "bg-gray-700 text-gray-300",
        "success": "bg-green-900 text-green-300",
        "error": "bg-red-900 text-red-300",
        "rejected": "bg-orange-900 text-orange-300",
    }
    return mapping.get(status, "bg-gray-700 text-gray-300")


# ── Run views ─────────────────────────────────────────────────────────────────


class RunListView(View):
    template_name = "agent/run_list.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        qs = AgentRun.objects.select_related("agent", "workflow").order_by("-created_at")
        status_filter = request.GET.get("status", "")
        source_filter = request.GET.get("source", "")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if source_filter:
            qs = qs.filter(trigger_source=source_filter)

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        ctx = {
            "runs": page_obj,
            "page_obj": page_obj,
            "agents": Agent.objects.filter(is_active=True),
            "status_choices": AgentRun.Status.choices,
            "source_choices": AgentRun.TriggerSource.choices,
            "status_filter": status_filter,
            "source_filter": source_filter,
        }
        return render(request, self.template_name, ctx)


class RunCreateView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        agent_id = request.POST.get("agent_id", "").strip()
        input_text = request.POST.get("input", "").strip()

        if not agent_id or not input_text:
            return HttpResponse("agent_id and input are required", status=400)

        agent = get_object_or_404(Agent, pk=agent_id, is_active=True)

        # Create a Chat conversation so the result is visible in the Chat sidebar.
        from chat.models import Conversation, Message as ChatMessage
        conversation = Conversation.objects.create(
            interface=Conversation.Interface.WEB,
            active_agent=agent,
            title=input_text[:60],
        )
        ChatMessage.objects.create(
            conversation=conversation,
            role=ChatMessage.Role.USER,
            content=input_text,
        )

        run = AgentRun.objects.create(
            agent=agent,
            conversation=conversation,
            trigger_source=AgentRun.TriggerSource.WEB,
            input=input_text,
        )
        execute_agent_run.delay(str(run.id))

        if request.htmx:
            return HttpResponseClientRedirect(f"/agent/runs/{run.id}/")
        return redirect("agent:detail", pk=run.id)


class RunDetailView(View):
    template_name = "agent/run_detail.html"

    def get(self, request: HttpRequest, pk) -> HttpResponse:
        run = get_object_or_404(AgentRun.objects.select_related("agent"), pk=pk)
        ctx = _build_run_context(run, request)
        return render(request, self.template_name, ctx)


class RunStatusView(View):
    def get(self, request: HttpRequest, pk) -> HttpResponse:
        run = get_object_or_404(AgentRun, pk=pk)
        html = render_to_string(
            "agent/_run_status.html",
            _build_run_context(run, request),
            request=request,
        )
        return HttpResponse(html)


class RunRespondView(View):
    def post(self, request: HttpRequest, pk) -> HttpResponse:
        run = get_object_or_404(AgentRun, pk=pk)
        if run.status != AgentRun.Status.WAITING:
            return HttpResponse("Run is not waiting for input", status=400)

        human_response = request.POST.get("response", "").strip()
        if not human_response:
            return HttpResponse("response is required", status=400)

        run.input = human_response
        run.status = AgentRun.Status.PENDING
        run.save(update_fields=["input", "status"])
        execute_agent_run.delay(str(run.id))

        if request.htmx:
            tool_executions = _annotate_tool_executions(run.tool_executions.order_by("created_at"))
            gs = run.graph_state or {}
            html = render_to_string(
                "agent/_run_status.html",
                {"run": run, "tool_executions": tool_executions, "skill_bodies": _load_skill_bodies(run.triggered_skills or []), "rag_matches": gs.get("rag_matches", []), "loop_trace": gs.get("loop_trace", [])},
                request=request,
            )
            return HttpResponse(html)
        return redirect("agent:detail", pk=run.id)


class RunCancelView(View):
    def post(self, request: HttpRequest, pk) -> HttpResponse:
        run = get_object_or_404(AgentRun, pk=pk)
        if run.status in (AgentRun.Status.PENDING, AgentRun.Status.RUNNING):
            run.status = AgentRun.Status.FAILED
            run.error = "Cancelled by user"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at"])

        if request.htmx:
            tool_executions = _annotate_tool_executions(run.tool_executions.order_by("created_at"))
            gs = run.graph_state or {}
            html = render_to_string(
                "agent/_run_status.html",
                {"run": run, "tool_executions": tool_executions, "skill_bodies": _load_skill_bodies(run.triggered_skills or []), "rag_matches": gs.get("rag_matches", []), "loop_trace": gs.get("loop_trace", [])},
                request=request,
            )
            return HttpResponse(html)
        return redirect("agent:detail", pk=run.id)


# ── Dashboard ─────────────────────────────────────────────────────────────────


class DashboardView(TemplateView):
    template_name = "agent/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_runs"] = AgentRun.objects.filter(
            status__in=[AgentRun.Status.PENDING, AgentRun.Status.RUNNING, AgentRun.Status.WAITING]
        ).select_related("agent")[:20]
        ctx["last_heartbeat"] = HeartbeatLog.objects.order_by("-triggered_at").first()
        ctx["default_agent"] = Agent.objects.filter(is_default=True).first()
        ctx["recent_runs"] = AgentRun.objects.select_related("agent").order_by("-created_at")[:10]
        return ctx


# ── Logs ──────────────────────────────────────────────────────────────────────


class LogsView(TemplateView):
    template_name = "agent/logs.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["heartbeat_logs"] = HeartbeatLog.objects.order_by("-triggered_at")[:50]
        ctx["tool_executions"] = ToolExecution.objects.select_related("run", "run__agent").order_by("-created_at")[:50]
        return ctx


# ── Memory ────────────────────────────────────────────────────────────────────


class MemoryView(View):
    template_name = "agent/memory.html"

    def _get_context(self) -> dict:
        path = _memory_path()
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        paragraphs = _split_paragraphs(content)
        paragraphs_with_hashes = [{"content": p, "hash": _hash(p)} for p in paragraphs]

        from agent.models import Memory
        memory_count = Memory.objects.filter(source="memory_md").count()
        last_reembed = ReembedLog.objects.order_by("-created_at").first()

        out_of_sync = False
        if path.exists() and last_reembed:
            file_mtime = timezone.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)
            out_of_sync = file_mtime > last_reembed.created_at
        elif path.exists() and not last_reembed:
            out_of_sync = True

        return {
            "memory_content": content,
            "paragraphs": paragraphs_with_hashes,
            "memory_count": memory_count,
            "last_reembed": last_reembed,
            "out_of_sync": out_of_sync,
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self._get_context())

    def post(self, request: HttpRequest) -> HttpResponse:
        content = request.POST.get("content", "")
        if not content.strip():
            return HttpResponse("Content cannot be empty.", status=400)
        content = content[:50000]

        path = _memory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        if request.htmx:
            return HttpResponse('<p class="text-green-400 text-sm">Saved.</p>')
        return render(request, self.template_name, {**self._get_context(), "saved": True})


class MemoryReembedView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        from agent.tasks import reembed_memory_task
        reembed_memory_task.delay()
        if request.htmx:
            return HttpResponse(
                '<p class="text-green-400 text-sm">Re-embed task queued.</p>'
            )
        return redirect("agent:memory")


class MemorySearchView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        query = request.GET.get("q", "").strip()
        results = []
        if query:
            try:
                from agent.memory.long_term import search_long_term
                snippets = search_long_term(query, limit=5)
                results = [{"content": s, "hash": _hash(s)} for s in snippets]
            except Exception:
                pass
        return render(request, "agent/_memory_search_results.html", {"results": results, "query": query})


class MemoryParagraphDeleteView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        para_hash = request.POST.get("hash", "").strip()
        if not para_hash:
            return HttpResponse("hash is required", status=400)

        path = _memory_path()
        if not path.exists():
            return HttpResponse("MEMORY.md not found", status=404)

        content = path.read_text(encoding="utf-8")
        paragraphs = _split_paragraphs(content)
        new_paragraphs = [p for p in paragraphs if _hash(p) != para_hash]

        if len(new_paragraphs) == len(paragraphs):
            return HttpResponse("Paragraph not found", status=404)

        path.write_text("\n\n".join(new_paragraphs), encoding="utf-8")

        # Trigger async reembed
        from agent.tasks import reembed_memory_task
        reembed_memory_task.delay()

        return HttpResponse("")  # HTMX outerHTML swap removes the card


class MemoryParagraphEditView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        para_hash = request.POST.get("hash", "").strip()
        new_content = request.POST.get("content", "").strip()
        if not para_hash or not new_content:
            return HttpResponse("hash and content are required", status=400)

        path = _memory_path()
        if not path.exists():
            return HttpResponse("MEMORY.md not found", status=404)

        content = path.read_text(encoding="utf-8")
        paragraphs = _split_paragraphs(content)
        new_paragraphs = [new_content if _hash(p) == para_hash else p for p in paragraphs]

        if new_paragraphs == paragraphs and not any(_hash(p) == para_hash for p in paragraphs):
            return HttpResponse("Paragraph not found", status=404)

        path.write_text("\n\n".join(new_paragraphs), encoding="utf-8")

        # Trigger async reembed
        from agent.tasks import reembed_memory_task
        reembed_memory_task.delay()

        new_hash = _hash(new_content)
        return render(
            request,
            "agent/_paragraph_card.html",
            {"para": {"content": new_content, "hash": new_hash}},
        )


# ── Tools ──────────────────────────────────────────────────────────────────────


class ToolsView(View):
    template_name = "agent/tools.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        from agent.tools import all_tools
        agent_id = request.GET.get("agent", "")
        if agent_id:
            agent = Agent.objects.filter(pk=agent_id).first()
        else:
            agent = Agent.objects.filter(is_default=True).first()

        agent_tools = list(agent.tools) if agent else []
        tool_policies = (agent.metadata or {}).get("tool_policies", {}) if agent else {}

        pending_approvals = ToolExecution.objects.filter(
            status=ToolExecution.Status.PENDING
        ).select_related("run", "run__agent").order_by("created_at")

        all_agents = Agent.objects.filter(is_active=True)

        tools_with_policy = [
            (t, tool_policies.get(t.name, "default"))
            for t in all_tools().values()
        ]
        ctx = {
            "tools_with_policy": tools_with_policy,
            "agent": agent,
            "agent_tools": agent_tools,
            "tool_policies": tool_policies,
            "all_agents": all_agents,
            "pending_approvals": pending_approvals,
        }
        return render(request, self.template_name, ctx)


class ToolToggleView(View):
    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        agent_id = request.POST.get("agent", "")
        if agent_id:
            agent = Agent.objects.filter(pk=agent_id).first()
        else:
            agent = Agent.objects.filter(is_default=True).first()

        if agent is None:
            return HttpResponse("No agent found.", status=400)

        tools = list(agent.tools)
        if name in tools:
            tools.remove(name)
        else:
            tools.append(name)
        agent.tools = tools
        agent.save(update_fields=["tools"])

        if request.htmx:
            from agent.tools import all_tools
            tool_policies = (agent.metadata or {}).get("tool_policies", {})
            tool = all_tools().get(name)
            html = render_to_string(
                "agent/_tool_row.html",
                {
                    "tool": tool,
                    "agent_tools": tools,
                    "tool_policies": tool_policies,
                    "current_policy": tool_policies.get(name, "default") if tool else "default",
                    "agent": agent,
                },
                request=request,
            )
            return HttpResponse(html)
        return redirect("agent:tools")


class ToolPolicyView(View):
    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        agent_id = request.POST.get("agent", "")
        policy = request.POST.get("policy", "default")

        if agent_id:
            agent = Agent.objects.filter(pk=agent_id).first()
        else:
            agent = Agent.objects.filter(is_default=True).first()

        if agent is None:
            return HttpResponse("No agent found.", status=400)

        metadata = dict(agent.metadata or {})
        tool_policies = dict(metadata.get("tool_policies", {}))

        if policy == "default":
            tool_policies.pop(name, None)
        else:
            tool_policies[name] = policy

        metadata["tool_policies"] = tool_policies
        agent.metadata = metadata
        agent.save(update_fields=["metadata"])

        return HttpResponse(status=204)


# ── Skills ────────────────────────────────────────────────────────────────────


class SkillsView(TemplateView):
    template_name = "agent/skills.html"

    def get_context_data(self, **kwargs):
        from collections import Counter
        from pathlib import Path

        from agent.skills import registry
        from agent.skills.discovery import collect_all_skills, all_skill_dirs
        from agent.models import SkillEmbedding, TrustedSkillSource

        ctx = super().get_context_data(**kwargs)
        db_skills_map = {s.name: s for s in Skill.objects.all()}

        # Build set of trusted source dir paths for quick lookup
        sources = all_skill_dirs(check_db_trust=True)
        trusted_paths = {str(src.path) for src in sources if src.trusted}

        # Build embedding status lookup
        embedded_names = set(SkillEmbedding.objects.values_list("skill_name", flat=True))

        # Collect all discovered skills (including untrusted sources for UI visibility)
        discovered = collect_all_skills(check_db_trust=True)
        discovered_map = {s["name"]: s for s in discovered}

        def _source_label(source_dir_str: str) -> str:
            if not source_dir_str:
                return "Other"
            p = Path(source_dir_str)
            try:
                workspace_skills = (Path(settings.AGENT_WORKSPACE_DIR) / "skills").resolve()
                if p.resolve() == workspace_skills:
                    return "Workspace"
            except Exception:
                pass
            try:
                claude_skills = (Path.home() / ".claude" / "skills").resolve()
                if p.resolve() == claude_skills:
                    return "Claude Code"
            except Exception:
                pass
            return p.name or "Other"

        skills_with_db = []
        shown_names: set[str] = set()
        for entry in registry.all().values():
            info = discovered_map.get(entry.name, {})
            trusted = str(info.get("source_dir", "")) in trusted_paths or not info
            embed_status = (
                "untrusted" if not trusted
                else ("embedded" if entry.name in embedded_names else "not_embedded")
            )
            source_dir = str(info.get("source_dir", ""))
            skills_with_db.append({
                "entry": entry,
                "db": db_skills_map.get(entry.name),
                "embed_status": embed_status,
                "trusted": trusted,
                "source_dir": source_dir,
                "source_label": _source_label(source_dir),
            })
            shown_names.add(entry.name)

        # Also show discovered skills not yet in the registry (from extra dirs)
        for info in discovered:
            if info["name"] in shown_names:
                continue
            trusted = str(info.get("source_dir", "")) in trusted_paths
            embed_status = "untrusted" if not trusted else "not_embedded"
            source_dir = str(info.get("source_dir", ""))
            skills_with_db.append({
                "entry": None,
                "db": db_skills_map.get(info["name"]),
                "embed_status": embed_status,
                "trusted": trusted,
                "source_dir": source_dir,
                "source_label": _source_label(source_dir),
                "discovered_name": info["name"],
                "discovered_description": (db_skills_map.get(info["name"]) and db_skills_map[info["name"]].description) or "",
            })

        # Build tab list sorted: Workspace first, Claude Code second, rest alphabetically
        label_counts = Counter(item["source_label"] for item in skills_with_db)
        tab_order = {"Workspace": 0, "Claude Code": 1}
        skill_tabs = sorted(
            [{"name": label, "count": count} for label, count in label_counts.items()],
            key=lambda t: (tab_order.get(t["name"], 2), t["name"]),
        )

        ctx["skills_with_db"] = skills_with_db
        ctx["skill_tabs"] = skill_tabs
        return ctx


class SkillInstallView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        from agent.skills.loader import SkillLoader
        from agent.skills import registry
        from agent.skills.discovery import all_skill_dirs

        loaded: list[str] = []
        for src in all_skill_dirs(check_db_trust=False):
            loader = SkillLoader(src.path)
            loaded.extend(loader.load_all(registry))

        # Sync to DB
        for name in loaded:
            entry = registry.get(name)
            if entry:
                Skill.objects.update_or_create(
                    name=name,
                    defaults={
                        "description": entry.description,
                        "path": entry.path,
                        "enabled": True,
                    },
                )

        if request.htmx:
            return HttpResponse(
                f'<p class="text-green-400 text-sm">Loaded {len(loaded)} skill(s): {", ".join(loaded) or "none"}.</p>'
            )
        return redirect("agent:skills")


class SkillImportFromProjectView(View):
    """POST /agent/skills/import-from-project/ — copy skills from ../skills/.agents/skills/ into agent/workspace/skills/."""

    def post(self, request: HttpRequest) -> HttpResponse:
        import shutil
        from pathlib import Path

        source_dir = (Path(settings.BASE_DIR).parent / "skills" / ".agents" / "skills").resolve()
        dest_dir = (Path(settings.AGENT_WORKSPACE_DIR) / "skills").resolve()

        if not source_dir.exists():
            msg = f"Source not found: {source_dir}"
            if request.htmx:
                return HttpResponse(f'<span class="text-xs text-red-400">{msg}</span>')
            return redirect("agent:skills")

        imported: list[str] = []
        for skill_dir in sorted(source_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            if not (skill_dir / "SKILL.md").exists():
                continue
            dest_skill = dest_dir / skill_dir.name
            shutil.copytree(skill_dir, dest_skill, dirs_exist_ok=True)
            imported.append(skill_dir.name)

        msg = f"Imported {len(imported)} skill(s): {', '.join(imported) or 'none'}."
        if request.htmx:
            return HttpResponse(f'<span class="text-xs text-green-400">{msg}</span>')
        return redirect("agent:skills")


class SkillAuthorView(View):
    """POST /agent/skills/author/ — invoke Claude Code to author a new skill."""

    def post(self, request: HttpRequest) -> HttpResponse:
        task = request.POST.get("task", "").strip()
        skill_name = request.POST.get("skill_name", "").strip().lower().replace(" ", "-")
        if not task or not skill_name:
            return JsonResponse({"status": "error", "output": "task and skill_name are required."}, status=400)

        from agent.skills.author import author_skill
        result = author_skill(task=task, skill_name=skill_name)

        if request.htmx:
            status_class = "text-green-400" if result["status"] == "ok" else "text-red-400"
            updated_msg = f" Embedded: {', '.join(result['updated'])}." if result.get("updated") else ""
            html = (
                f'<div class="{status_class} text-sm space-y-2">'
                f'<p>{result["status"].upper()}{updated_msg}</p>'
                f'<pre class="text-xs text-gray-400 whitespace-pre-wrap mt-1">{result["output"]}</pre>'
                f'</div>'
            )
            return HttpResponse(html)
        return JsonResponse(result)


class SkillReviewSuggestView(View):
    """POST /agent/skills/<name>/review/suggest/ — ask Claude to analyse and suggest, without writing."""

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.skills.author import review_skill_suggest
        result = review_skill_suggest(skill_name=name)

        if request.htmx:
            if result["status"] == "error":
                return render(request, "agent/_skill_review_result.html", {
                    "status": "error",
                    "output": result["output"],
                })

            return render(request, "agent/_skill_review_result.html", {
                "status": "ok",
                "issues": result.get("issues", ""),
                "suggested_content": result.get("suggested_content") or "",
                "skill_name": name,
            })
        return JsonResponse(result)


class SkillReviewApplyView(View):
    """POST /agent/skills/<name>/review/apply/ — write suggested content to SKILL.md and re-embed."""

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        content = request.POST.get("suggested-content", "").strip()
        if not content:
            return HttpResponse('<p class="text-red-400 text-sm">No content to apply.</p>')

        from pathlib import Path
        skill_path = Path(settings.AGENT_WORKSPACE_DIR) / "skills" / name / "SKILL.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content + "\n", encoding="utf-8")

        from agent.skills.loader import SkillLoader
        from agent.skills import registry as skill_registry
        from agent.skills.embeddings import embed_all_skills
        from agent.skills.author import _validate_skill
        skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
        SkillLoader(skills_dir).load_all(skill_registry)
        updated = embed_all_skills()
        valid, val_output = _validate_skill(skill_path.parent)

        if request.htmx:
            from django.utils.html import escape
            embed_msg = f", re-embedded: {', '.join(updated)}" if updated else ""
            val_msg = f" | skills-ref: {escape(val_output)}" if val_output else ""
            status_class = "text-green-400" if valid else "text-yellow-400"
            html = (
                f'<div class="{status_class} text-sm space-y-1">'
                f'<p>✓ Applied and saved{escape(embed_msg)}.{val_msg}</p>'
                f'<p class="text-xs text-gray-500">Reload the editor page to see the updated content.</p>'
                f'</div>'
            )
            return HttpResponse(html)
        return redirect("agent:skill-edit", name=name)


class SkillReviewView(View):
    """POST /agent/skills/<name>/review/ — invoke Claude Code to review and directly apply."""

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.skills.author import review_skill
        result = review_skill(skill_name=name)

        if request.htmx:
            status_class = "text-green-400" if result["status"] in ("ok", "updated") else "text-red-400"
            updated_msg = f" (re-embedded)" if result.get("updated") else ""
            html = (
                f'<div id="review-result" class="{status_class} text-sm space-y-2">'
                f'<p>{result["status"].upper()}{updated_msg}</p>'
                f'<pre class="text-xs text-gray-400 whitespace-pre-wrap mt-1">{result["output"]}</pre>'
                f'</div>'
            )
            return HttpResponse(html)
        return JsonResponse(result)


class SkillToggleView(View):
    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        skill_db = get_object_or_404(Skill, name=name)
        skill_db.enabled = not skill_db.enabled
        skill_db.save(update_fields=["enabled"])

        from agent.skills import registry
        from agent.skills.discovery import all_skill_dirs
        from agent.models import SkillEmbedding
        entry = registry.get(name)

        if request.htmx:
            trusted_paths = {
                str(src.path)
                for src in all_skill_dirs(check_db_trust=True)
                if src.trusted
            }
            embedded_names = set(SkillEmbedding.objects.values_list("skill_name", flat=True))
            trusted = entry is not None  # registered entries are always from trusted sources
            embed_status = (
                "embedded" if name in embedded_names
                else "not_embedded"
            ) if trusted else "untrusted"
            return render(
                request,
                "agent/_skill_row.html",
                {
                    "entry": entry,
                    "db": skill_db,
                    "embed_status": embed_status,
                    "trusted": trusted,
                    "source_dir": str(entry.path) if entry else "",
                },
            )
        return redirect("agent:skills")


class SkillEditView(View):
    """GET /agent/skills/<name>/edit/ — show SKILL.md editor.
    POST — save content, reload registry, re-embed."""

    def _skill_dir(self, name: str) -> Path:
        return Path(settings.AGENT_WORKSPACE_DIR) / "skills" / name

    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        skill_dir = self._skill_dir(name)
        skill_md = skill_dir / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""
        from agent.skills import registry
        entry = registry.get(name)
        db = Skill.objects.filter(name=name).first()
        return render(request, "agent/skill_form.html", {
            "name": name,
            "content": content,
            "entry": entry,
            "db": db,
            "is_new": False,
        })

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        content = request.POST.get("content", "")
        skill_dir = self._skill_dir(name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(content, encoding="utf-8")
        # Reload registry + re-embed
        from agent.skills.loader import SkillLoader
        from agent.skills import registry as skill_registry
        from agent.skills.embeddings import embed_all_skills
        loader = SkillLoader(Path(settings.AGENT_WORKSPACE_DIR) / "skills")
        loader.load_all(skill_registry)
        embed_all_skills()
        # Sync DB
        entry = skill_registry.get(name)
        if entry:
            Skill.objects.update_or_create(
                name=name,
                defaults={"description": entry.description, "path": entry.path, "enabled": True},
            )
        return redirect("agent:skill-edit", name=name)


class SkillCreateView(View):
    """GET — blank skill form. POST — create directory + SKILL.md, load, redirect to edit."""

    _TEMPLATE = """\
---
name: {name}
description: 
triggers: []
examples: []
version: 1
---

## Overview

Describe the skill purpose here.

### Key conventions

1. Convention one.

### Standard patterns

```
# example pattern
```

### Do NOT use

- Item one.

### Search strategy

1. Step one.
"""

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, "agent/skill_form.html", {"is_new": True, "content": "", "name": ""})

    def post(self, request: HttpRequest) -> HttpResponse:
        name = request.POST.get("name", "").strip().lower().replace(" ", "-")
        if not name:
            return render(request, "agent/skill_form.html", {
                "is_new": True, "content": "", "name": "",
                "error": "Skill name is required.",
            })
        skill_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills" / name
        if skill_dir.exists():
            return redirect("agent:skill-edit", name=name)
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(self._TEMPLATE.format(name=name), encoding="utf-8")
        # Load into registry
        from agent.skills.loader import SkillLoader
        from agent.skills import registry as skill_registry
        loader = SkillLoader(Path(settings.AGENT_WORKSPACE_DIR) / "skills")
        loader.load_all(skill_registry)
        return redirect("agent:skill-edit", name=name)


class SkillDeleteView(View):
    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        skill_db = get_object_or_404(Skill, name=name)
        from agent.skills import registry
        entry = registry.get(name)
        return render(
            request,
            "agent/skill_confirm_delete.html",
            {"skill": skill_db, "entry": entry},
        )

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        skill_db = get_object_or_404(Skill, name=name)
        from agent.skills import registry
        registry._skills.pop(name, None)
        skill_db.delete()

        if request.htmx:
            return HttpResponseClientRedirect("/agent/skills/")
        return redirect("agent:skills")


class SkillEmbedView(View):
    """POST /agent/skills/<name>/embed/ — embed a single skill and update status badge."""

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.skills.discovery import collect_all_skills
        from agent.skills.embeddings import _embed_skill_dir

        all_skills = collect_all_skills(check_db_trust=True)
        skill_info = next((s for s in all_skills if s["name"] == name), None)

        if not skill_info:
            if request.htmx:
                return HttpResponse(
                    f'<span class="text-xs text-red-400">Skill "{name}" not found.</span>'
                )
            return redirect("agent:skills")

        if not skill_info["trusted"]:
            if request.htmx:
                return HttpResponse(
                    '<span class="text-xs text-yellow-400">Cannot embed: source not trusted. Approve source first.</span>'
                )
            return redirect("agent:skills")

        try:
            result = _embed_skill_dir(skill_info["skill_dir"])
            if request.htmx:
                msg = f'<span class="text-xs text-green-400">✓ Embedded "{name}"</span>' if result \
                    else f'<span class="text-xs text-gray-400">Unchanged "{name}"</span>'
                return HttpResponse(msg)
        except Exception as exc:
            if request.htmx:
                return HttpResponse(
                    f'<span class="text-xs text-red-400">Embed failed: {exc}</span>'
                )

        return redirect("agent:skills")


class SkillApproveSourceView(View):
    """POST /agent/skills/<name>/approve-source/ — trust the skill's source dir and embed all its skills."""

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.models import TrustedSkillSource
        from agent.skills.discovery import collect_all_skills, iter_skill_dirs
        from agent.skills.embeddings import _embed_skill_dir

        all_skills = collect_all_skills(check_db_trust=False)
        skill_info = next((s for s in all_skills if s["name"] == name), None)

        if not skill_info:
            if request.htmx:
                return HttpResponse(
                    f'<span class="text-xs text-red-400">Skill "{name}" not found.</span>'
                )
            return redirect("agent:skills")

        source_dir = str(skill_info["source_dir"])
        username = request.user.username if request.user.is_authenticated else "system"
        TrustedSkillSource.objects.get_or_create(
            path=source_dir,
            defaults={"approved_by": username},
        )

        # Embed all skills from the newly trusted directory
        embedded: list[str] = []
        for skill_dir in iter_skill_dirs(skill_info["source_dir"]):
            try:
                result = _embed_skill_dir(skill_dir)
                if result:
                    embedded.append(result)
            except Exception as exc:
                logger.warning("Failed to embed skill %s after source approval: %s", skill_dir.name, exc)

        if request.htmx:
            n = len(embedded)
            return HttpResponse(
                f'<span class="text-xs text-green-400">✓ Source approved. Embedded {n} skill(s).</span>'
            )
        return redirect("agent:skills")


class SkillEmbedAllView(View):
    """POST /agent/skills/embed-all/ — re-embed all skills from all trusted dirs."""

    def post(self, request: HttpRequest) -> HttpResponse:
        from agent.skills.embeddings import embed_all_skills

        try:
            processed = embed_all_skills(native_only=False, force=False)
            msg = f"Re-embedded {len(processed)} skill(s)."
        except Exception as exc:
            msg = f"Error during re-embed: {exc}"

        if request.htmx:
            return HttpResponse(
                f'<span class="text-xs text-green-400">{msg}</span>'
            )
        return redirect("agent:skills")


class SkillListApiView(View):
    """GET /agent/api/skills/ — return enabled skills as JSON for UI autocomplete."""

    def get(self, request: HttpRequest) -> JsonResponse:
        from agent.skills import registry

        skills = sorted(
            [{"name": e.name, "description": e.description} for e in registry.all().values()],
            key=lambda s: s["name"],
        )
        return JsonResponse({"skills": skills})


class SkillSyncClaudeCodeView(View):
    """POST /agent/skills/sync-claude/ — sync workspace skills to ~/.claude/skills/."""

    def post(self, request: HttpRequest) -> HttpResponse:
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        try:
            call_command("sync_claude_code", skills_only=True, stdout=out, stderr=out)
            output = out.getvalue()
            lines = [l.strip() for l in output.splitlines() if l.strip()]
            written = next((l for l in lines if l.startswith("Written")), None)
            msg = written or f"Synced. ({len(lines)} line(s) of output)"
        except Exception as exc:
            msg = f"Error: {exc}"

        if request.htmx:
            return HttpResponse(
                f'<span class="text-xs text-green-400">{msg}</span>'
            )
        return redirect("agent:skills")


# ── Tool approval ─────────────────────────────────────────────────────────────


class ToolApproveView(View):
    def post(self, request: HttpRequest, tool_id: str) -> HttpResponse:
        te = get_object_or_404(ToolExecution, pk=tool_id)

        if te.status != ToolExecution.Status.PENDING:
            return HttpResponse("Tool execution is not pending approval.", status=400)
        if te.run.status != AgentRun.Status.WAITING:
            return HttpResponse("AgentRun is not waiting.", status=400)

        action = request.POST.get("action", "approve")
        if action == "reject":
            te.status = ToolExecution.Status.REJECTED
            te.output = {"error": "Tool execution was rejected by the user."}
            te.save(update_fields=["status", "output"])
        else:
            te.status = ToolExecution.Status.RUNNING
            te.approved_at = timezone.now()
            te.save(update_fields=["status", "approved_at"])

        # Resume the run
        te.run.status = AgentRun.Status.PENDING
        te.run.save(update_fields=["status"])
        from agent.runner import AgentRunner
        AgentRunner.enqueue(te.run)

        if request.htmx:
            # If called from the chat UI, return a typing indicator so polling
            # continues and picks up the next approval card or final reply.
            user_msg_id = request.POST.get("user_msg_id")
            conversation = te.run.conversation
            if user_msg_id and conversation:
                from django.template.loader import render_to_string
                html = render_to_string(
                    "chat/_typing_indicator.html",
                    {"conversation": conversation, "user_msg_id": user_msg_id},
                    request=request,
                )
                return HttpResponse(html)
            # Fallback for Agent Run UI approvals
            label = "approved" if action == "approve" else "rejected"
            return HttpResponse(
                f'<p class="text-green-400 text-sm px-4 py-2">'
                f'{te.tool_name} {label} · {timezone.now().strftime("%H:%M")}</p>'
            )
        return redirect("agent:dashboard")


# ── Agent CRUD ────────────────────────────────────────────────────────────────


class AgentForm(forms.Form):
    name = forms.CharField(max_length=100, required=True)
    description = forms.CharField(widget=forms.Textarea, required=False)
    model = forms.ChoiceField(choices=[])
    system_prompt = forms.CharField(widget=forms.Textarea, required=True)
    is_active = forms.BooleanField(required=False, initial=True)
    is_default = forms.BooleanField(required=False)
    tools = forms.MultipleChoiceField(choices=[], required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["model"].choices = settings.AVAILABLE_MODELS

        from agent.tools import all_tools
        from agent.skills import registry
        tool_names = list(all_tools().keys())
        skill_names = [f"skill_{n}" for n in registry.names()]
        all_names = tool_names + skill_names
        self.fields["tools"].choices = [(n, n) for n in all_names]


class AgentListView(ListView):
    model = Agent
    template_name = "agent/agents.html"
    context_object_name = "agents"
    ordering = ["name"]

    def get_queryset(self):
        return Agent.objects.annotate(run_count=Count("runs")).order_by("name")


class AgentCreateView(View):
    template_name = "agent/agent_form.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        form = AgentForm(initial={"is_active": True})
        return render(request, self.template_name, {"form": form, "action": "Create"})

    def post(self, request: HttpRequest) -> HttpResponse:
        form = AgentForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "action": "Create"})

        data = form.cleaned_data
        # Check uniqueness
        if Agent.objects.filter(name=data["name"]).exists():
            form.add_error("name", "An agent with this name already exists.")
            return render(request, self.template_name, {"form": form, "action": "Create"})

        agent = Agent.objects.create(
            name=data["name"],
            description=data["description"] or "",
            model=data["model"],
            system_prompt=data["system_prompt"],
            is_active=data["is_active"],
            is_default=data["is_default"],
            tools=data["tools"],
        )
        return redirect("agent:agent-list")


class AgentEditView(View):
    template_name = "agent/agent_form.html"

    def get(self, request: HttpRequest, pk) -> HttpResponse:
        agent = get_object_or_404(Agent, pk=pk)
        form = AgentForm(initial={
            "name": agent.name,
            "description": agent.description,
            "model": agent.model,
            "system_prompt": agent.system_prompt,
            "is_active": agent.is_active,
            "is_default": agent.is_default,
            "tools": agent.tools,
        })
        return render(request, self.template_name, {"form": form, "action": "Save", "agent": agent})

    def post(self, request: HttpRequest, pk) -> HttpResponse:
        agent = get_object_or_404(Agent, pk=pk)
        form = AgentForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "action": "Save", "agent": agent})

        data = form.cleaned_data
        # Check uniqueness excluding self
        if Agent.objects.filter(name=data["name"]).exclude(pk=pk).exists():
            form.add_error("name", "An agent with this name already exists.")
            return render(request, self.template_name, {"form": form, "action": "Save", "agent": agent})

        agent.name = data["name"]
        agent.description = data["description"] or ""
        agent.model = data["model"]
        agent.system_prompt = data["system_prompt"]
        agent.is_active = data["is_active"]
        agent.is_default = data["is_default"]
        agent.tools = data["tools"]
        agent.save()
        return redirect("agent:agent-list")


class AgentDeleteView(View):
    template_name = "agent/agent_confirm_delete.html"

    def get(self, request: HttpRequest, pk) -> HttpResponse:
        agent = get_object_or_404(Agent, pk=pk)
        total_runs = agent.runs.count()
        active_runs = agent.runs.filter(
            status__in=[AgentRun.Status.PENDING, AgentRun.Status.RUNNING]
        ).count()
        return render(request, self.template_name, {
            "agent": agent,
            "total_runs": total_runs,
            "active_runs": active_runs,
        })

    def post(self, request: HttpRequest, pk) -> HttpResponse:
        agent = get_object_or_404(Agent, pk=pk)

        active_runs = agent.runs.filter(
            status__in=[AgentRun.Status.PENDING, AgentRun.Status.RUNNING]
        )
        if active_runs.exists():
            return HttpResponse(
                "Cannot delete: agent has active (pending/running) runs. Cancel them first.",
                status=400,
            )

        was_default = agent.is_default
        total_runs = agent.runs.count()

        if total_runs > 0:
            # Soft delete
            agent.is_active = False
            agent.is_default = False
            agent.save(update_fields=["is_active", "is_default"])
            # Cancel pending/waiting runs
            agent.runs.filter(
                status__in=[AgentRun.Status.PENDING, AgentRun.Status.WAITING]
            ).update(
                status=AgentRun.Status.FAILED,
                error="Agent deactivated",
            )
            msg = "Agent deactivated (has historical runs)."
            if was_default:
                msg += " Warning: no default agent is now set."
        else:
            agent.delete()
            msg = "Agent deleted."

        if request.htmx:
            return HttpResponseClientRedirect("/agent/agents/")
        return redirect("agent:agent-list")


class AgentSetDefaultView(View):
    def post(self, request: HttpRequest, pk) -> HttpResponse:
        agent = get_object_or_404(Agent, pk=pk)
        agent.is_default = True
        agent.save()  # Agent.save() clears other defaults

        if request.htmx:
            return HttpResponse(
                headers={"HX-Redirect": "/agent/agents/"}
            )
        return redirect("agent:agent-list")


# ── Monitoring ────────────────────────────────────────────────────────────────


class MonitoringView(TemplateView):
    template_name = "agent/monitoring.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_ago = now - timezone.timedelta(days=30)

        cost_today = LLMUsage.objects.filter(
            created_at__gte=today_start
        ).aggregate(
            total_tokens=Sum("total_tokens"),
            total_cost=Sum("estimated_cost_usd"),
            count=Count("id"),
        )
        cost_month = LLMUsage.objects.filter(
            created_at__gte=month_ago
        ).aggregate(
            total_tokens=Sum("total_tokens"),
            total_cost=Sum("estimated_cost_usd"),
            count=Count("id"),
        )

        cost_by_model = (
            LLMUsage.objects.filter(created_at__gte=month_ago)
            .values("model")
            .annotate(
                calls=Count("id"),
                tokens=Sum("total_tokens"),
                cost=Sum("estimated_cost_usd"),
            )
            .order_by("-tokens")
        )

        cost_by_source = (
            LLMUsage.objects.filter(created_at__gte=month_ago)
            .values("source")
            .annotate(
                calls=Count("id"),
                tokens=Sum("total_tokens"),
                cost=Sum("estimated_cost_usd"),
            )
        )

        # Recent activity feed — merge querysets in Python
        from chat.models import Message as ChatMessage

        runs = list(AgentRun.objects.select_related("agent").order_by("-created_at")[:50])
        tool_execs = list(ToolExecution.objects.select_related("run__agent").order_by("-created_at")[:50])
        heartbeats = list(HeartbeatLog.objects.order_by("-triggered_at")[:50])
        reembed_logs = list(ReembedLog.objects.order_by("-created_at")[:50])
        chat_msgs = list(
            ChatMessage.objects.filter(role="user").order_by("-created_at")[:50]
        )

        activity = []
        for r in runs:
            activity.append({"event_type": "run", "obj": r, "created_at": r.created_at})
        for te in tool_execs:
            activity.append({"event_type": "tool_execution", "obj": te, "created_at": te.created_at})
        for hb in heartbeats:
            activity.append({"event_type": "heartbeat", "obj": hb, "created_at": hb.triggered_at})
        for rl in reembed_logs:
            activity.append({"event_type": "reembed", "obj": rl, "created_at": rl.created_at})
        for msg in chat_msgs:
            activity.append({"event_type": "chat_message", "obj": msg, "created_at": msg.created_at})

        activity.sort(key=lambda x: x["created_at"], reverse=True)
        recent_activity = activity[:50]

        # Approval history
        approval_status_filter = self.request.GET.get("approval_status", "")
        approval_qs = ToolExecution.objects.filter(requires_approval=True).select_related("run__agent")
        if approval_status_filter:
            approval_qs = approval_qs.filter(status=approval_status_filter)
        approval_paginator = Paginator(approval_qs.order_by("-created_at"), 25)
        approval_page = approval_paginator.get_page(self.request.GET.get("approval_page", 1))

        ctx.update({
            "cost_today": cost_today,
            "cost_month": cost_month,
            "cost_by_model": list(cost_by_model),
            "cost_by_source": list(cost_by_source),
            "recent_activity": recent_activity,
            "approval_page": approval_page,
            "approval_status_filter": approval_status_filter,
        })
        return ctx


class HealthCheckView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        import django_redis
        from django.core.cache import cache

        cached = cache.get("agent:monitoring:health")
        if cached:
            return HttpResponse(cached, content_type="text/html")

        checks = {}

        # Default agent
        default_agent = Agent.objects.filter(is_default=True, is_active=True).first()
        checks["agent"] = {
            "label": "Default Agent",
            "status": "ok" if default_agent else "error",
            "detail": default_agent.name if default_agent else "None configured",
        }

        # Database
        try:
            from django.db import connection
            connection.ensure_connection()
            checks["database"] = {"label": "Database", "status": "ok", "detail": "Connected"}
        except Exception as e:
            checks["database"] = {"label": "Database", "status": "error", "detail": str(e)}

        # Redis
        try:
            from django.core.cache import cache as redis_cache
            redis_cache.set("_health_ping", "1", timeout=5)
            redis_cache.get("_health_ping")
            checks["redis"] = {"label": "Redis", "status": "ok", "detail": "Connected"}
        except Exception as e:
            checks["redis"] = {"label": "Redis", "status": "error", "detail": str(e)}

        # Celery
        try:
            from config.celery import app as celery_app
            result = celery_app.control.inspect(timeout=2).ping()
            if result:
                checks["celery"] = {"label": "Celery", "status": "ok", "detail": f"{len(result)} worker(s)"}
            else:
                checks["celery"] = {"label": "Celery", "status": "error", "detail": "No workers responding"}
        except Exception as e:
            checks["celery"] = {"label": "Celery", "status": "error", "detail": str(e)}

        # Last heartbeat
        last_hb = HeartbeatLog.objects.order_by("-triggered_at").first()
        interval = settings.AGENT_HEARTBEAT_INTERVAL_MINUTES
        if last_hb:
            age_minutes = (timezone.now() - last_hb.triggered_at).total_seconds() / 60
            if last_hb.status == HeartbeatLog.Status.ERROR:
                hb_status = "error"
            elif age_minutes <= 2 * interval:
                hb_status = "ok"
            elif age_minutes <= 4 * interval:
                hb_status = "warning"
            else:
                hb_status = "error"
            checks["heartbeat"] = {
                "label": "Last Heartbeat",
                "status": hb_status,
                "detail": last_hb.triggered_at.strftime("%Y-%m-%d %H:%M UTC"),
            }
        else:
            checks["heartbeat"] = {"label": "Last Heartbeat", "status": "error", "detail": "No heartbeats recorded"}

        statuses = [c["status"] for c in checks.values()]
        if "error" in statuses:
            overall = "critical"
        elif "warning" in statuses:
            overall = "degraded"
        else:
            overall = "ok"

        html = render_to_string(
            "agent/_health_status.html",
            {"checks": list(checks.values()), "overall": overall},
            request=request,
        )
        cache.set("agent:monitoring:health", html, timeout=10)
        return HttpResponse(html)


# ── Workspace File Editor ─────────────────────────────────────────────────────

ALLOWED_WORKSPACE_FILES = ["AGENTS.md", "SOUL.md"]


class WorkspaceFileListView(View):
    template_name = "agent/workspace.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        workspace_dir = Path(settings.AGENT_WORKSPACE_DIR)
        files = []
        for fname in ALLOWED_WORKSPACE_FILES:
            fpath = workspace_dir / fname
            files.append({
                "name": fname,
                "exists": fpath.exists(),
                "mtime": timezone.datetime.fromtimestamp(fpath.stat().st_mtime, tz=datetime.timezone.utc) if fpath.exists() else None,
                "size": fpath.stat().st_size if fpath.exists() else 0,
            })
        heartbeat_deprecated = (workspace_dir / "HEARTBEAT.md").exists()
        return render(request, self.template_name, {
            "files": files,
            "heartbeat_deprecated": heartbeat_deprecated,
        })


class WorkspaceFileEditView(View):
    template_name = "agent/workspace_file.html"

    def _get_path(self, filename: str) -> Path | None:
        if filename not in ALLOWED_WORKSPACE_FILES:
            return None
        return Path(settings.AGENT_WORKSPACE_DIR) / filename

    def get(self, request: HttpRequest, filename: str) -> HttpResponse:
        path = self._get_path(filename)
        if path is None:
            return HttpResponse("File not allowed.", status=403)

        content = path.read_text(encoding="utf-8") if path.exists() else ""

        # Check for example file
        example_path = Path(settings.AGENT_WORKSPACE_DIR) / f"{filename}.example"
        has_example = example_path.exists()

        return render(request, self.template_name, {
            "filename": filename,
            "content": content,
            "has_example": has_example,
        })

    def post(self, request: HttpRequest, filename: str) -> HttpResponse:
        path = self._get_path(filename)
        if path is None:
            return HttpResponse("File not allowed.", status=403)

        action = request.POST.get("action", "save")
        if action == "restore_example":
            example_path = Path(settings.AGENT_WORKSPACE_DIR) / f"{filename}.example"
            if not example_path.exists():
                return HttpResponse("No example file found.", status=404)
            content = example_path.read_text(encoding="utf-8")
        else:
            content = request.POST.get("content", "")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        if request.htmx:
            return HttpResponse('<p class="text-green-400 text-sm">Saved.</p>')
        return render(request, self.template_name, {
            "filename": filename,
            "content": content,
            "has_example": (Path(settings.AGENT_WORKSPACE_DIR) / f"{filename}.example").exists(),
            "saved": True,
        })


# ── Workspace file serving ────────────────────────────────────────────────────

ALLOWED_WORKSPACE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg"}


class WorkspaceFileServeView(View):
    """Serve generated image files (e.g. charts) from the agent workspace."""

    def get(self, request: HttpRequest, filename: str) -> HttpResponse:
        from django.http import FileResponse
        import mimetypes

        # Reject path traversal
        if "/" in filename or "\\" in filename or ".." in filename:
            return HttpResponse("Invalid filename.", status=400)

        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_WORKSPACE_IMAGE_EXTENSIONS:
            return HttpResponse("File type not allowed.", status=403)

        path = Path(settings.AGENT_WORKSPACE_DIR) / filename
        if not path.exists():
            return HttpResponse("File not found.", status=404)

        content_type, _ = mimetypes.guess_type(filename)
        return FileResponse(path.open("rb"), content_type=content_type or "application/octet-stream")


# ── MCP Server Management ─────────────────────────────────────────────────────


def _validate_mcp_post(post_data: dict) -> str | None:
    """Validate MCP server form data. Returns an error message or None."""
    name = post_data.get("name", "").strip()
    transport = post_data.get("transport", "stdio")
    command = post_data.get("command", "").strip()
    url = post_data.get("url", "").strip()

    if not name:
        return "Name is required."
    if " " in name or not name.replace("-", "").replace("_", "").isalnum():
        return "Name must be slug-like (letters, numbers, hyphens, underscores only)."
    if transport == "stdio" and not command:
        return "Command is required for stdio transport."
    if transport == "sse" and not url:
        return "URL is required for SSE transport."
    return None


def _build_mcp_config_from_post(post_data: dict, name: str | None = None, existing_enabled: bool = True):
    """Build an MCPServerConfig from POST data. Raises ValueError on invalid env_json."""
    from agent.mcp.config import MCPServerConfig

    cfg_name = name or post_data.get("name", "").strip()
    transport = post_data.get("transport", "stdio")
    command = post_data.get("command", "").strip()
    url = post_data.get("url", "").strip()
    enabled = post_data.get("enabled") == "on"

    env_raw = post_data.get("env_json", "").strip()
    try:
        credentials = json.loads(env_raw) if env_raw else {}
    except json.JSONDecodeError:
        raise ValueError("Environment variables must be valid JSON.")

    auto_approve_raw = post_data.get("auto_approve_tools", "").strip()
    auto_approve_tools = [t.strip() for t in auto_approve_raw.split(",") if t.strip()] if auto_approve_raw else []

    always_include_raw = post_data.get("always_include_resources", "").strip()
    always_include_resources = [r.strip() for r in always_include_raw.split(",") if r.strip()] if always_include_raw else []

    dead_codes_raw = post_data.get("session_dead_error_codes", "").strip()
    session_dead_error_codes = [int(c.strip()) for c in dead_codes_raw.split(",") if c.strip().lstrip("-").isdigit()] if dead_codes_raw else []

    health_probe_tool = post_data.get("health_probe_tool", "").strip()

    # Route credentials to the correct field based on transport
    env = credentials if transport == "stdio" else {}
    headers = credentials if transport == "sse" else {}

    return MCPServerConfig(
        name=cfg_name,
        type=transport,
        command=command,
        url=url,
        env=env,
        headers=headers,
        auto_approve_tools=auto_approve_tools,
        always_include_resources=always_include_resources,
        session_dead_error_codes=session_dead_error_codes,
        health_probe_tool=health_probe_tool,
        enabled=enabled,
    )


class MCPServerListView(View):
    template_name = "agent/mcp.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        from agent.mcp.config import load_servers
        from agent.mcp.pool import MCPConnectionPool
        from agent.mcp.registry import get_registry

        servers = load_servers()
        pool = MCPConnectionPool.get()
        registry = get_registry()
        servers_with_tools = []
        for cfg in servers.values():
            tools = [e for e in registry.all().values() if e.server_name == cfg.name]
            cfg.live_status = pool.get_status(cfg.name) if cfg.enabled else "disconnected"
            servers_with_tools.append({"server": cfg, "tools": tools})
        all_disabled = bool(servers) and all(not item["server"].enabled for item in servers_with_tools)
        return render(request, self.template_name, {
            "servers_with_tools": servers_with_tools,
            "all_disabled": all_disabled,
        })


class MCPServerAddView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        html = render_to_string("agent/_mcp_add_form.html", {}, request=request)
        return HttpResponse(html)

    def post(self, request: HttpRequest) -> HttpResponse:
        from agent.mcp.config import load_servers, upsert_server
        from agent.mcp.pool import MCPConnectionPool

        err = _validate_mcp_post(request.POST)
        if err:
            if request.htmx:
                return HttpResponse(f'<p class="text-red-400 text-sm">{err}</p>')
            return HttpResponse(err, status=400)

        # Reject duplicate names
        name = request.POST.get("name", "").strip()
        if name in load_servers():
            msg = f"A server named '{name}' already exists."
            if request.htmx:
                return HttpResponse(f'<p class="text-red-400 text-sm">{msg}</p>')
            return HttpResponse(msg, status=400)

        try:
            cfg = _build_mcp_config_from_post(request.POST)
        except ValueError as e:
            if request.htmx:
                return HttpResponse(f'<p class="text-red-400 text-sm">{e}</p>')
            return HttpResponse(str(e), status=400)

        upsert_server(cfg)
        if cfg.enabled:
            MCPConnectionPool.get().start_server(cfg)

        if request.htmx:
            return HttpResponse(headers={"HX-Redirect": "/agent/mcp/"})
        return redirect("agent:mcp-list")


class MCPServerDetailView(View):
    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.mcp.config import get_server
        cfg = get_server(name)
        if cfg is None:
            raise Http404
        html = render_to_string("agent/_mcp_add_form.html", {"server": cfg}, request=request)
        return HttpResponse(html)

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.mcp.config import get_server, upsert_server
        from agent.mcp.pool import MCPConnectionPool

        cfg = get_server(name)
        if cfg is None:
            raise Http404

        was_enabled = cfg.enabled

        try:
            updated = _build_mcp_config_from_post(request.POST, name=name)
        except ValueError as e:
            if request.htmx:
                return HttpResponse(f'<p class="text-red-400 text-sm">{e}</p>')
            return HttpResponse(str(e), status=400)

        upsert_server(updated)
        pool = MCPConnectionPool.get()
        if updated.enabled and not was_enabled:
            pool.start_server(updated)
        elif not updated.enabled and was_enabled:
            pool.stop_server(name)
        elif updated.enabled:
            pool.refresh_server(name)

        if request.htmx:
            return HttpResponse(headers={"HX-Redirect": "/agent/mcp/"})
        return redirect("agent:mcp-list")


class MCPServerToggleView(View):
    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.mcp.config import get_server, upsert_server
        from agent.mcp.pool import MCPConnectionPool
        from agent.mcp.registry import get_registry

        cfg = get_server(name)
        if cfg is None:
            raise Http404

        pool = MCPConnectionPool.get()
        cfg.enabled = not cfg.enabled
        upsert_server(cfg)

        if cfg.enabled:
            pool.start_server(cfg)
        else:
            pool.stop_server(name)

        tools = [e for e in get_registry().all().values() if e.server_name == name]
        cfg.live_status = pool.get_status(name) if cfg.enabled else "disconnected"
        html = render_to_string(
            "agent/_mcp_server.html",
            {"server": cfg, "tools": tools},
            request=request,
        )
        return HttpResponse(html)


class MCPServerRefreshView(View):
    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.mcp.config import get_server
        from agent.mcp.pool import MCPConnectionPool
        from agent.mcp.registry import get_registry

        cfg = get_server(name)
        if cfg is None:
            raise Http404

        pool = MCPConnectionPool.get()
        pool.refresh_server(name)
        tools = [e for e in get_registry().all().values() if e.server_name == name]
        cfg.live_status = pool.get_status(name) if cfg.enabled else "disconnected"
        html = render_to_string(
            "agent/_mcp_server.html",
            {"server": cfg, "tools": tools},
            request=request,
        )
        return HttpResponse(html)


class MCPServerDeleteView(View):
    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        from agent.mcp.config import remove_server
        from agent.mcp.pool import MCPConnectionPool

        MCPConnectionPool.get().stop_server(name)
        remove_server(name)
        if request.htmx:
            return HttpResponse(headers={"HX-Redirect": "/agent/mcp/"})
        return redirect("agent:mcp-list")


# ── Workflow Views ─────────────────────────────────────────────────────────────


class WorkflowListView(View):
    template_name = "agent/workflow_list.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        workflows = Workflow.objects.select_related("agent").order_by("name")
        return render(request, self.template_name, {"workflows": workflows})


class WorkflowDetailView(View):
    template_name = "agent/workflow_detail.html"

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        import yaml as _yaml
        from chat.models import Message as ChatMessage
        workflow = get_object_or_404(Workflow, pk=pk)
        runs = (
            AgentRun.objects.filter(workflow=workflow)
            .select_related("agent")
            .order_by("-created_at")[:50]
        )
        # Load YAML from file for the editor
        yml_path = Path(settings.AGENT_WORKSPACE_DIR) / "workflows" / Path(workflow.filename).name
        workflow_yaml = ""
        if yml_path.exists():
            workflow_yaml = yml_path.read_text(encoding="utf-8")
        else:
            workflow_yaml = _yaml.dump(workflow.definition, allow_unicode=True, default_flow_style=False)
        # Output messages delivered by this workflow
        output_messages = (
            ChatMessage.objects.filter(metadata__workflow_id=str(workflow.pk))
            .select_related("conversation")
            .order_by("-created_at")[:100]
        )
        return render(request, self.template_name, {
            "workflow": workflow,
            "runs": runs,
            "workflow_yaml": workflow_yaml,
            "output_messages": output_messages,
        })


class WorkflowToggleView(View):
    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        from django_celery_beat.models import PeriodicTask

        workflow = get_object_or_404(Workflow, pk=pk)
        workflow.enabled = not workflow.enabled
        workflow.save(update_fields=["enabled"])

        if workflow.celery_beat_id:
            PeriodicTask.objects.filter(pk=workflow.celery_beat_id).update(
                enabled=workflow.enabled
            )

        if request.htmx:
            return render(
                request,
                "agent/_workflow_toggle.html",
                {"workflow": workflow},
            )
        return redirect("agent:workflow-list")


class WorkflowRunNowView(View):
    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        from agent.tasks import execute_workflow

        workflow = get_object_or_404(Workflow, pk=pk)
        execute_workflow.delay(str(workflow.pk))
        if request.htmx:
            return HttpResponse(
                '<span class="text-green-400 text-sm">Queued ✓</span>',
                headers={"HX-Trigger": "workflowRunQueued"},
            )
        return redirect("agent:workflow-detail", pk=pk)


class WorkflowReloadView(View):
    """POST /agent/workflows/reload/ — re-scan workflow YAML files."""

    def post(self, request: HttpRequest) -> HttpResponse:
        from agent.workflows.loader import WorkflowLoader

        try:
            loaded = WorkflowLoader().load_all()
            msg = f"Reloaded {len(loaded)} workflow(s): {', '.join(loaded) or 'none'}"
            status = 200
        except Exception as exc:
            msg = f"Reload failed: {exc}"
            status = 500

        if request.htmx:
            return HttpResponse(
                f'<span class="text-sm text-green-400">{msg}</span>',
                status=status,
            )
        return redirect("agent:workflow-list")


class WorkflowSaveView(View):
    """POST /agent/workflows/<pk>/save/ — save edited YAML back to filesystem and reload."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        import yaml as _yaml
        from agent.workflows.loader import WorkflowLoader

        workflow = get_object_or_404(Workflow, pk=pk)
        raw_yaml = request.POST.get("yaml_content", "")

        # Validate YAML before writing
        try:
            _yaml.safe_load(raw_yaml)
        except _yaml.YAMLError as exc:
            if request.htmx:
                return HttpResponse(
                    f'<span class="text-red-400 text-sm">YAML error: {exc}</span>',
                    status=400,
                )
            return redirect("agent:workflow-detail", pk=pk)

        yml_path = Path(settings.AGENT_WORKSPACE_DIR) / "workflows" / Path(workflow.filename).name
        yml_path.write_text(raw_yaml, encoding="utf-8")

        try:
            WorkflowLoader().load_all()
        except Exception as exc:
            logger.error("WorkflowSaveView reload error: %s", exc)

        if request.htmx:
            return HttpResponse(
                '<span class="text-green-400 text-sm">Saved ✓</span>',
            )
        return redirect("agent:workflow-detail", pk=pk)


class WorkflowCreateView(View):
    """GET/POST /agent/workflows/create/ — create a new workflow YAML file."""

    TEMPLATE = """name: {name}
description: {description}
agent: default
enabled: true
delivery: announce

trigger:
  cron: "0 9 * * 1"
  timezone: UTC

steps:
  - name: step-1
    prompt: >
      Describe what this step should do.
"""

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, "agent/workflow_create.html", {
            "template": self.TEMPLATE.format(name="my-workflow", description=""),
        })

    def post(self, request: HttpRequest) -> HttpResponse:
        import yaml as _yaml
        from agent.workflows.loader import WorkflowLoader

        raw_yaml = request.POST.get("yaml_content", "")
        try:
            data = _yaml.safe_load(raw_yaml)
        except _yaml.YAMLError as exc:
            return render(request, "agent/workflow_create.html", {
                "template": raw_yaml,
                "error": f"YAML error: {exc}",
            })

        if not data or not isinstance(data, dict):
            return render(request, "agent/workflow_create.html", {
                "template": raw_yaml,
                "error": "YAML must be a mapping.",
            })

        name = data.get("name", "").strip()
        if not name:
            return render(request, "agent/workflow_create.html", {
                "template": raw_yaml,
                "error": "Workflow must have a 'name' field.",
            })

        yml_path = Path(settings.AGENT_WORKSPACE_DIR) / "workflows" / f"{name}.yml"
        if yml_path.exists():
            return render(request, "agent/workflow_create.html", {
                "template": raw_yaml,
                "error": f"A workflow named '{name}' already exists.",
            })

        yml_path.write_text(raw_yaml, encoding="utf-8")

        try:
            WorkflowLoader().load_all()
        except Exception as exc:
            logger.error("WorkflowCreateView reload error: %s", exc)

        workflow = Workflow.objects.filter(name=name).first()
        if workflow:
            return redirect("agent:workflow-detail", pk=workflow.pk)
        return redirect("agent:workflow-list")


class WorkflowDeleteView(View):
    """POST /agent/workflows/<pk>/delete/ — delete workflow from DB and filesystem."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        from django_celery_beat.models import PeriodicTask

        workflow = get_object_or_404(Workflow, pk=pk)
        name = workflow.name

        # Remove Celery Beat task
        if workflow.celery_beat_id:
            PeriodicTask.objects.filter(pk=workflow.celery_beat_id).delete()

        # Remove workflow YAML file from disk
        yml_path = Path(settings.AGENT_WORKSPACE_DIR) / "workflows" / Path(workflow.filename).name
        try:
            if yml_path.exists():
                yml_path.unlink()
        except Exception as exc:
            logger.warning("WorkflowDeleteView: could not remove file %s: %s", yml_path, exc)

        workflow.delete()
        logger.info("Workflow '%s' deleted", name)

        if request.htmx:
            response = HttpResponse(status=204)
            response["HX-Redirect"] = "/agent/workflows/"
            return response
        return redirect("agent:workflow-list")


# ── Knowledge Base ──────────────────────────────────────────────────────────


class KnowledgeListView(View):
    """List all knowledge documents."""

    def get(self, request: HttpRequest) -> HttpResponse:
        docs = KnowledgeDocument.objects.all()
        return render(request, "agent/knowledge.html", {"documents": docs})


class KnowledgeCreateView(View):
    """Create a knowledge document from file upload, URL, or pasted text."""

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, "agent/_knowledge_add_form.html")

    def post(self, request: HttpRequest) -> HttpResponse:
        from agent.rag.tasks import ingest_document_task
        from agent.rag.ingest import _fetch_url_content, _extract_pdf_text

        source_type = request.POST.get("source_type", "text")
        title = request.POST.get("title", "").strip()
        raw_content = ""

        if source_type == "url":
            url = request.POST.get("source_url", "").strip()
            if not url:
                return HttpResponse(
                    '<p class="text-red-400 text-sm">URL is required.</p>'
                )
            try:
                raw_content = _fetch_url_content(url)
            except Exception as exc:
                return HttpResponse(
                    f'<p class="text-red-400 text-sm">Failed to fetch URL: {exc}</p>'
                )
            if not title:
                title = url.split("/")[-1] or url[:80]
            doc = KnowledgeDocument.objects.create(
                title=title,
                source_type=KnowledgeDocument.SourceType.URL,
                source_url=url,
                raw_content=raw_content,
                status=KnowledgeDocument.Status.PENDING,
            )

        elif source_type == "upload":
            uploaded = request.FILES.get("file")
            if not uploaded:
                return HttpResponse(
                    '<p class="text-red-400 text-sm">File is required.</p>'
                )
            if not title:
                title = uploaded.name
            file_bytes = uploaded.read()
            name_lower = uploaded.name.lower()
            if name_lower.endswith(".pdf"):
                try:
                    raw_content = _extract_pdf_text(file_bytes)
                except Exception as exc:
                    return HttpResponse(
                        f'<p class="text-red-400 text-sm">Failed to parse PDF: {exc}</p>'
                    )
            else:
                raw_content = file_bytes.decode("utf-8", errors="replace")
            doc = KnowledgeDocument.objects.create(
                title=title,
                source_type=KnowledgeDocument.SourceType.UPLOAD,
                raw_content=raw_content,
                status=KnowledgeDocument.Status.PENDING,
            )

        else:  # text
            raw_content = request.POST.get("raw_content", "").strip()
            if not raw_content:
                return HttpResponse(
                    '<p class="text-red-400 text-sm">Text content is required.</p>'
                )
            if not title:
                title = raw_content[:60] + ("…" if len(raw_content) > 60 else "")
            doc = KnowledgeDocument.objects.create(
                title=title,
                source_type=KnowledgeDocument.SourceType.TEXT,
                raw_content=raw_content,
                status=KnowledgeDocument.Status.PENDING,
            )

        # Dispatch async ingestion
        ingest_document_task.delay(str(doc.id))

        if request.htmx:
            return HttpResponse(headers={"HX-Redirect": "/agent/knowledge/"})
        return redirect("agent:knowledge")


class KnowledgeToggleView(View):
    """Toggle is_active for a knowledge document."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        doc = get_object_or_404(KnowledgeDocument, pk=pk)
        doc.is_active = not doc.is_active
        doc.save(update_fields=["is_active"])
        return render(request, "agent/_knowledge_row.html", {"doc": doc})


class KnowledgeStatusView(View):
    """Return updated row partial for polling during ingestion."""

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        doc = get_object_or_404(KnowledgeDocument, pk=pk)
        return render(request, "agent/_knowledge_row.html", {"doc": doc})


class KnowledgeReingestView(View):
    """Re-ingest a single document (re-chunk + re-embed)."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        from agent.rag.tasks import ingest_document_task

        doc = get_object_or_404(KnowledgeDocument, pk=pk)
        doc.status = KnowledgeDocument.Status.PENDING
        doc.save(update_fields=["status"])
        ingest_document_task.delay(str(doc.id))
        return render(request, "agent/_knowledge_row.html", {"doc": doc})


class KnowledgeDeleteView(View):
    """Delete a knowledge document and all its chunks."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        doc = get_object_or_404(KnowledgeDocument, pk=pk)
        doc.delete()
        return HttpResponse("")

