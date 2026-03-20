from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView
from django_htmx.http import HttpResponseClientRedirect

from .models import Agent, AgentRun, HeartbeatLog, LLMUsage, ReembedLog, Skill, ToolExecution
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
        qs = AgentRun.objects.select_related("agent").order_by("-created_at")
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
        run = AgentRun.objects.create(
            agent=agent,
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
        tool_executions = run.tool_executions.order_by("created_at")
        ctx = {"run": run, "tool_executions": tool_executions}
        return render(request, self.template_name, ctx)


class RunStatusView(View):
    def get(self, request: HttpRequest, pk) -> HttpResponse:
        run = get_object_or_404(AgentRun, pk=pk)
        tool_executions = run.tool_executions.order_by("created_at")
        html = render_to_string(
            "agent/_run_status.html",
            {"run": run, "tool_executions": tool_executions},
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
            tool_executions = run.tool_executions.order_by("created_at")
            html = render_to_string(
                "agent/_run_status.html",
                {"run": run, "tool_executions": tool_executions},
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
            tool_executions = run.tool_executions.order_by("created_at")
            html = render_to_string(
                "agent/_run_status.html",
                {"run": run, "tool_executions": tool_executions},
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
            file_mtime = timezone.datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
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
        from agent.skills import registry
        ctx = super().get_context_data(**kwargs)
        db_skills_map = {s.name: s for s in Skill.objects.all()}
        skills_with_db = []
        for entry in registry.all().values():
            skills_with_db.append({
                "entry": entry,
                "db": db_skills_map.get(entry.name),
            })
        ctx["skills_with_db"] = skills_with_db
        return ctx


class SkillInstallView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        from agent.skills.loader import SkillLoader
        from agent.skills import registry
        from pathlib import Path

        skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
        loader = SkillLoader(skills_dir)
        loaded = loader.load_all(registry)

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


class SkillToggleView(View):
    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        skill_db = get_object_or_404(Skill, name=name)
        skill_db.enabled = not skill_db.enabled
        skill_db.save(update_fields=["enabled"])

        from agent.skills import registry
        entry = registry.get(name)

        if request.htmx:
            return render(
                request,
                "agent/_skill_row.html",
                {"entry": entry, "db": skill_db},
            )
        return redirect("agent:skills")


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

ALLOWED_WORKSPACE_FILES = ["AGENTS.md", "SOUL.md", "HEARTBEAT.md"]


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
                "mtime": timezone.datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc) if fpath.exists() else None,
                "size": fpath.stat().st_size if fpath.exists() else 0,
            })
        return render(request, self.template_name, {"files": files})


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
