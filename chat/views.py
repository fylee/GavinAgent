from __future__ import annotations
from datetime import timedelta

from django.conf import settings
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, DetailView
from django_htmx.http import HttpResponseClientRedirect

from .models import Conversation, Message
from .tasks import process_chat_message


import re as _re
_MCP_INVALID_CHARS = _re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize_mcp_name(name: str) -> str:
    """Match the same sanitization used in MCPToolEntry.llm_function_name."""
    return _MCP_INVALID_CHARS.sub("_", name)


def _build_usage_lists(
    triggered_skills: list[str],
    mcp_servers_active: list[str],
    tool_executions,
    mcp_registry=None,
) -> tuple[list[dict], list[dict]]:
    """Return skills_with_usage and mcp_with_usage sorted: used first (colored), unused last (gray).

    A skill is 'used' when the 'skill' tool was explicitly called with that skill name.
    An MCP server is 'used' when at least one of its tools was executed.

    MCP server matching uses prefix extraction from te.tool_name (format:
    <sanitized_server>__<tool>) rather than the registry singleton, which is
    only populated in the Celery worker process — not in the web server process.
    """
    # Pre-compute sanitized → original mapping for MCP servers
    sanitized_to_server = {_sanitize_mcp_name(s): s for s in mcp_servers_active}

    actually_used_skills: set[str] = set()
    actually_used_mcp: set[str] = set()
    for te in tool_executions:
        if te.tool_name == "skill" and isinstance(te.input, dict):
            sname = te.input.get("name") or te.input.get("skill_name")
            if sname:
                actually_used_skills.add(sname)
        if "__" in te.tool_name:
            # Try registry first (works in Celery worker process)
            if mcp_registry:
                entry = mcp_registry.get(te.tool_name)
                if entry:
                    actually_used_mcp.add(entry.server_name)
                    continue
            # Fallback: derive server from sanitized prefix of tool_name
            # e.g. "research_mcp__s2_search_papers" → prefix "research_mcp"
            # which matches _sanitize_mcp_name("research-mcp") = "research_mcp"
            prefix = te.tool_name.split("__", 1)[0]
            matched = sanitized_to_server.get(prefix)
            if matched:
                actually_used_mcp.add(matched)

    skills_with_usage = sorted(
        [{"name": s, "used": s in actually_used_skills} for s in triggered_skills],
        key=lambda x: (0 if x["used"] else 1),
    )
    mcp_with_usage = sorted(
        [{"name": s, "used": s in actually_used_mcp} for s in mcp_servers_active],
        key=lambda x: (0 if x["used"] else 1),
    )
    return skills_with_usage, mcp_with_usage


class SidebarMixin:
    """Adds sidebar conversation groups and available models to context."""

    def get_sidebar_context(self) -> dict:
        from agent.models import Workflow

        conversations = list(
            Conversation.objects.filter(
                interface=Conversation.Interface.WEB,
            ).exclude(
                metadata__workflow_inbox=True,
                metadata__has_key="workflow_inbox",
            ).order_by("-updated_at")[:100]
        )
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        week_ago = today - timedelta(days=7)

        groups = [
            ("Today", [c for c in conversations if c.updated_at.date() == today]),
            ("Yesterday", [c for c in conversations if c.updated_at.date() == yesterday]),
            (
                "Previous 7 days",
                [c for c in conversations if week_ago < c.updated_at.date() < yesterday],
            ),
            ("Older", [c for c in conversations if c.updated_at.date() <= week_ago]),
        ]
        # Scheduled workflows — each shown as a separate entry in the sidebar
        scheduled_workflows = list(
            Workflow.objects.filter(enabled=True).order_by("name")[:50]
        )
        return {
            "conversation_groups": [(label, items) for label, items in groups if items],
            "scheduled_workflows": scheduled_workflows,
            "available_models": settings.AVAILABLE_MODELS,
        }


class ConversationListView(SidebarMixin, TemplateView):
    template_name = "chat/conversation_list.html"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.get_sidebar_context())
        return ctx


class ConversationCreateView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        from agent.models import Agent
        default_agent = Agent.objects.filter(is_default=True, is_active=True).first()
        conversation = Conversation.objects.create(
            interface=Conversation.Interface.WEB,
            active_agent=default_agent,
        )
        url = reverse("chat:detail", kwargs={"pk": conversation.id})
        if request.htmx:
            return HttpResponseClientRedirect(url)
        return redirect(url)


class ConversationDetailView(SidebarMixin, DetailView):
    model = Conversation
    template_name = "chat/conversation.html"
    context_object_name = "conversation"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["chat_messages"] = self.object.messages.all()
        ctx["current_conversation_id"] = self.object.id
        ctx.update(self.get_sidebar_context())

        # Agent toggle context
        from agent.models import Agent
        ctx["default_agent"] = Agent.objects.filter(is_default=True, is_active=True).first()
        return ctx


class ConversationUpdateView(View):
    def patch(self, request: HttpRequest, pk: str) -> HttpResponse:
        conversation = get_object_or_404(Conversation, pk=pk)
        data = QueryDict(request.body)
        update_fields: list[str] = ["updated_at"]

        if "model" in data:
            conversation.model = data["model"]
            update_fields.append("model")
        if "title" in data:
            conversation.title = data["title"].strip()
            update_fields.append("title")
        if "system_prompt" in data:
            conversation.system_prompt = data["system_prompt"]
            update_fields.append("system_prompt")
        if "temperature" in data:
            val = data["temperature"].strip()
            conversation.temperature = float(val) if val else None
            update_fields.append("temperature")
        if "max_tokens" in data:
            val = data["max_tokens"].strip()
            conversation.max_tokens = int(val) if val else None
            update_fields.append("max_tokens")

        conversation.save(update_fields=update_fields)
        return HttpResponse(status=204)


class MessageCreateView(View):
    def post(self, request: HttpRequest, conversation_pk: str) -> HttpResponse:
        conversation = get_object_or_404(Conversation, pk=conversation_pk)
        content = request.POST.get("content", "").strip()
        if not content:
            return HttpResponse(status=400)

        user_msg = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=content,
        )

        # Auto-title from first message
        title_updated = False
        if not conversation.title:
            conversation.title = content[:60]
            conversation.save(update_fields=["title", "updated_at"])
            title_updated = True

        # Only use regular chat processing when no agent is active.
        # Reload to get the active_agent FK (may have been set via toggle).
        conversation.refresh_from_db(fields=["active_agent"])
        if not conversation.active_agent_id:
            process_chat_message.delay(str(conversation.id))

        # Notify agent system — signal handler will enqueue the run if one exists
        try:
            from chat.signals import message_created
            message_created.send(
                sender=None,
                conversation_id=str(conversation.id),
                message_id=str(user_msg.id),
            )
        except Exception:
            pass

        if request.htmx:
            html = render_to_string(
                "chat/_message.html", {"message": user_msg}, request=request
            )
            html += render_to_string(
                "chat/_typing_indicator.html",
                {"conversation": conversation, "user_msg_id": user_msg.id},
                request=request,
            )
            if title_updated:
                title = conversation.title
                html += (
                    f'<span id="conversation-title" hx-swap-oob="true"'
                    f' class="flex-1 text-sm font-medium text-gray-200 truncate">{title}</span>'
                    f'<span id="sidebar-title-{conversation.id}" hx-swap-oob="true"'
                    f' class="truncate">{title}</span>'
                )
            return HttpResponse(html)

        return redirect("chat:detail", pk=conversation.id)


class MessageStreamView(View):
    """Poll endpoint: returns typing indicator until the assistant reply is ready."""

    def get(self, request: HttpRequest, conversation_pk: str, pk: str) -> HttpResponse:
        conversation = get_object_or_404(Conversation, pk=conversation_pk)
        try:
            user_msg = Message.objects.get(pk=pk, conversation=conversation)
        except Message.DoesNotExist:
            return HttpResponse(status=404)

        from agent.models import AgentRun, ToolExecution

        conversation.refresh_from_db(fields=["active_agent"])

        # If an agent is enabled for this conversation, show agent-specific states.
        # This prevents a regular chat reply from stealing the typing indicator.
        active_agent_run = None
        if conversation.active_agent_id:
            active_agent_run = AgentRun.objects.filter(
                conversation=conversation,
                status__in=[AgentRun.Status.PENDING, AgentRun.Status.RUNNING, AgentRun.Status.WAITING],
            ).first()

        if active_agent_run:
            if active_agent_run.status == AgentRun.Status.WAITING:
                pending_te = (
                    ToolExecution.objects.filter(
                        run=active_agent_run,
                        status=ToolExecution.Status.PENDING,
                    )
                    .order_by("created_at")
                    .first()
                )
                if pending_te:
                    html = render_to_string(
                        "chat/_tool_approval_card.html",
                        {"tool_execution": pending_te, "conversation": conversation, "user_msg_id": user_msg.id},
                        request=request,
                    )
                    return HttpResponse(html)
            # Agent is PENDING or RUNNING (or WAITING with no pending TE yet)
            # Fetch all tool executions so far to show progress
            tool_executions = list(
                ToolExecution.objects.filter(run=active_agent_run)
                .order_by("created_at")
            )
            # Annotate with parallel group metadata for the template
            from agent.views import _annotate_tool_executions
            tool_executions = _annotate_tool_executions(tool_executions)
            triggered_skills = active_agent_run.triggered_skills or []
            graph_state = active_agent_run.graph_state or {}
            loop_trace = graph_state.get("loop_trace", [])
            # MCP servers: prefer graph_state (set early by call_llm), supplement
            # with any from actual tool executions (tool_name like "Server__tool")
            mcp_from_state = graph_state.get("mcp_servers_active", [])
            # Resolve MCP server names from tool executions using the registry
            # so we get the original server_name (e.g. "EDWM MCP") not the
            # sanitized llm_function_name prefix (e.g. "EDWM_MCP").
            try:
                from agent.mcp.registry import get_registry as get_mcp_registry
                _mcp_reg = get_mcp_registry()
            except Exception:
                _mcp_reg = None
            mcp_from_executions = []
            for te in tool_executions:
                if "__" not in te.tool_name:
                    continue
                entry = _mcp_reg.get(te.tool_name) if _mcp_reg else None
                if entry:
                    mcp_from_executions.append(entry.server_name)
            mcp_servers_active = sorted(set(mcp_from_state) | set(mcp_from_executions))
            # Annotate each execution with a clean display name and MCP flag
            for te in tool_executions:
                if "__" in te.tool_name:
                    te.display_name = te.tool_name.split("__", 1)[1]
                    te.is_mcp = True
                else:
                    te.display_name = te.tool_name
                    te.is_mcp = False
            skills_with_usage, mcp_with_usage = _build_usage_lists(
                triggered_skills, mcp_servers_active, tool_executions, _mcp_reg
            )
            # Group TEs by round so they render interleaved with loop_trace reasoning.
            # TEs with round=None (no round field yet) are distributed in created_at order
            # across rounds using a positional fallback.
            te_by_round: dict = {}
            unrooted: list = []
            for te in tool_executions:
                if te.round:
                    te_by_round.setdefault(te.round, []).append(te)
                else:
                    unrooted.append(te)
            # Distribute unrooted TEs evenly across rounds (oldest first)
            if unrooted and loop_trace:
                for i, te in enumerate(unrooted):
                    r = loop_trace[i % len(loop_trace)].get("round", i + 1)
                    te_by_round.setdefault(r, []).append(te)
            loop_trace_with_tes = [
                {**entry,
                 "tool_executions": te_by_round.get(entry.get("round", 0), []),
                 "elapsed_s": (
                     round(entry["ts"] - active_agent_run.started_at.timestamp(), 1)
                     if entry.get("ts") and active_agent_run.started_at else None
                 )}
                for entry in loop_trace
            ]
            # Append a synthetic in-progress entry if LLM is currently streaming
            streaming_round = graph_state.get("_streaming_round")
            if streaming_round:
                completed_rounds = {e.get("round") for e in loop_trace_with_tes}
                if streaming_round.get("round") not in completed_rounds:
                    loop_trace_with_tes = loop_trace_with_tes + [{
                        "round": streaming_round["round"],
                        "decision": "streaming",
                        "reasoning": streaming_round.get("reasoning") or "",
                        "ts": streaming_round.get("ts"),
                        "tool_executions": [],
                        "llm_ms": None,
                        "elapsed_s": (
                            round(streaming_round["ts"] - active_agent_run.started_at.timestamp(), 1)
                            if streaming_round.get("ts") and active_agent_run.started_at else None
                        ),
                    }]
            # If there are TEs but no loop_trace yet (first round in flight),
            # pass them raw so at least something is visible.
            bare_tes = tool_executions if (not loop_trace and tool_executions) else []
            if tool_executions or triggered_skills or mcp_servers_active or loop_trace_with_tes:
                html = render_to_string(
                    "chat/_tool_progress.html",
                    {
                        "conversation": conversation,
                        "user_msg_id": user_msg.id,
                        "tool_executions": bare_tes,
                        "triggered_skills": triggered_skills,
                        "mcp_servers_active": mcp_servers_active,
                        "skills_with_usage": skills_with_usage,
                        "mcp_with_usage": mcp_with_usage,
                        "loop_trace": loop_trace_with_tes,
                    },
                    request=request,
                )
            else:
                html = render_to_string(
                    "chat/_typing_indicator.html",
                    {"conversation": conversation, "user_msg_id": user_msg.id},
                    request=request,
                )
            return HttpResponse(html)

        # No active agent — check for the regular assistant reply
        assistant_msg = (
            Message.objects.filter(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                created_at__gt=user_msg.created_at,
            )
            .order_by("created_at")
            .first()
        )

        if assistant_msg:
            msg_ctx: dict = {"message": assistant_msg}
            run_id = (assistant_msg.metadata or {}).get("run_id")
            if run_id:
                try:
                    from agent.models import AgentRun, ToolExecution
                    completed_run = AgentRun.objects.get(pk=run_id)
                    gs = completed_run.graph_state or {}
                    run_tes = list(
                        ToolExecution.objects.filter(run=completed_run)
                        .order_by("created_at")
                        .select_related()
                    )
                    for te in run_tes:
                        if "__" in te.tool_name:
                            te.display_name = te.tool_name.split("__", 1)[1]
                            te.is_mcp = True
                        else:
                            te.display_name = te.tool_name
                            te.is_mcp = False
                    loop_trace = gs.get("loop_trace", [])
                    te_by_round: dict = {}
                    for te in run_tes:
                        te_by_round.setdefault(te.round or 0, []).append(te)
                    loop_trace_with_tes = [
                        {**entry,
                         "tool_executions": te_by_round.get(entry.get("round", 0), []),
                         "elapsed_s": (
                             round(entry["ts"] - completed_run.started_at.timestamp(), 1)
                             if entry.get("ts") and completed_run.started_at else None
                         )}
                        for entry in loop_trace
                    ]
                    bare_tes = run_tes if (not loop_trace and run_tes) else []
                    run_triggered_skills = completed_run.triggered_skills or []
                    run_mcp_servers = gs.get("mcp_servers_active", [])
                    # MCP registry for usage detection
                    try:
                        from agent.mcp.registry import get_registry as get_mcp_registry
                        _run_mcp_reg = get_mcp_registry()
                    except Exception:
                        _run_mcp_reg = None
                    # Build unique tool summary: [{name, is_mcp, count, has_error}]
                    _tool_counts: dict = {}
                    for te in run_tes:
                        key = te.display_name
                        if key not in _tool_counts:
                            _tool_counts[key] = {"name": key, "is_mcp": te.is_mcp, "count": 0, "has_error": False}
                        _tool_counts[key]["count"] += 1
                        if te.status == "error":
                            _tool_counts[key]["has_error"] = True
                    unique_tools = list(_tool_counts.values())
                    # Usage-sorted skills and MCP lists
                    skills_with_usage, mcp_with_usage = _build_usage_lists(
                        run_triggered_skills, run_mcp_servers, run_tes, _run_mcp_reg
                    )
                    if run_tes or run_triggered_skills or run_mcp_servers or loop_trace:
                        msg_ctx["run_trace"] = {
                            "tool_executions": bare_tes,
                            "all_tool_executions": run_tes,
                            "unique_tools": unique_tools,
                            "triggered_skills": run_triggered_skills,
                            "mcp_servers_active": run_mcp_servers,
                            "skills_with_usage": skills_with_usage,
                            "mcp_with_usage": mcp_with_usage,
                            "loop_trace": loop_trace_with_tes,
                        }
                except Exception:
                    pass
            html = render_to_string(
                "chat/_message.html", msg_ctx, request=request
            )
            # Refresh title — may have been updated by title-generation task
            conversation.refresh_from_db(fields=["title"])
            from django.utils.html import escape as _esc
            _title = _esc(conversation.title or "")
            html += (
                f'<span id="conversation-title" hx-swap-oob="true"'
                f' class="flex-1 text-sm font-medium text-gray-200 truncate">{_title}</span>'
                f'<span id="sidebar-title-{conversation.id}" hx-swap-oob="true"'
                f' class="truncate">{_title}</span>'
            )
        else:
            html = render_to_string(
                "chat/_typing_indicator.html",
                {"conversation": conversation, "user_msg_id": user_msg.id},
                request=request,
            )
        return HttpResponse(html)


class WorkflowOutputView(SidebarMixin, View):
    """Conversation-style view showing output messages for a single workflow, with inline edit panel."""

    template_name = "chat/workflow_output.html"

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        import yaml as _yaml
        from pathlib import Path
        from django.conf import settings
        from agent.models import Workflow, AgentRun as _AgentRun

        workflow = get_object_or_404(Workflow, pk=pk)
        output_messages = (
            Message.objects.filter(metadata__workflow_id=str(pk))
            .order_by("created_at")
        )
        active_run = _AgentRun.objects.filter(
            workflow=workflow,
            status__in=[_AgentRun.Status.PENDING, _AgentRun.Status.RUNNING, _AgentRun.Status.WAITING],
        ).first()

        yml_path = Path(settings.AGENT_WORKSPACE_DIR) / "workflows" / Path(workflow.filename).name
        if yml_path.exists():
            workflow_yaml = yml_path.read_text(encoding="utf-8")
        else:
            workflow_yaml = _yaml.dump(workflow.definition, allow_unicode=True, default_flow_style=False)

        ctx = {
            "workflow": workflow,
            "output_messages": output_messages,
            "active_run": active_run,
            "current_workflow_id": str(pk),
            "workflow_yaml": workflow_yaml,
        }
        ctx.update(self.get_sidebar_context())
        return render(request, self.template_name, ctx)


class WorkflowOutputPollView(View):
    """HTMX poll endpoint: returns new workflow output messages since a given message id.
    Used by the workflow output page to live-update as runs complete.
    """

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        from agent.models import Workflow, AgentRun
        workflow = get_object_or_404(Workflow, pk=pk)
        after_id = request.GET.get("after_id", "")

        # Determine if a run is currently active for this workflow
        active_run = AgentRun.objects.filter(
            workflow=workflow,
            status__in=[AgentRun.Status.PENDING, AgentRun.Status.RUNNING, AgentRun.Status.WAITING],
        ).first()

        new_messages: list[Message] = []
        if after_id:
            try:
                anchor = Message.objects.get(pk=after_id)
                new_messages = list(
                    Message.objects.filter(
                        metadata__workflow_id=str(pk),
                        created_at__gt=anchor.created_at,
                    ).order_by("created_at")
                )
            except Message.DoesNotExist:
                pass

        html = render_to_string(
            "chat/_workflow_poll.html",
            {
                "new_messages": new_messages,
                "workflow": workflow,
                "after_id": after_id,
                "active_run": active_run,
            },
            request=request,
        )
        return HttpResponse(html)


class ConversationAgentToggleView(View):
    """Enable or disable the agent for a conversation."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        from agent.models import Agent, AgentRun

        conversation = get_object_or_404(Conversation, pk=pk)
        action = request.POST.get("action", "enable")

        if action == "disable":
            # Clear the active agent and cancel any in-flight runs
            conversation.active_agent = None
            conversation.save(update_fields=["active_agent", "updated_at"])
            AgentRun.objects.filter(
                conversation=conversation,
                status__in=[AgentRun.Status.PENDING, AgentRun.Status.WAITING, AgentRun.Status.RUNNING],
            ).update(
                status=AgentRun.Status.FAILED,
                error="Disabled by user",
            )
        else:
            agent = Agent.objects.filter(is_default=True, is_active=True).first()
            if agent is None:
                return HttpResponse("No active default agent configured.", status=400)
            conversation.active_agent = agent
            conversation.save(update_fields=["active_agent", "updated_at"])

        conversation.refresh_from_db()
        default_agent = Agent.objects.filter(is_default=True, is_active=True).first()

        if request.htmx:
            html = render_to_string(
                "chat/_agent_toggle.html",
                {
                    "conversation": conversation,
                    "default_agent": default_agent,
                },
                request=request,
            )
            return HttpResponse(html)

        return redirect("chat:detail", pk=conversation.id)


class CancelRunView(View):
    """Cancel any in-flight AgentRuns for a conversation."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        from agent.models import AgentRun

        conversation = get_object_or_404(Conversation, pk=pk)
        AgentRun.objects.filter(
            conversation=conversation,
            status__in=[
                AgentRun.Status.PENDING,
                AgentRun.Status.RUNNING,
                AgentRun.Status.WAITING,
            ],
        ).update(status=AgentRun.Status.FAILED, error="Cancelled by user")
        return HttpResponse(
            "", status=200, headers={"HX-Trigger": "runCancelled"}
        )
