from __future__ import annotations
from datetime import timedelta

from django.conf import settings
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, DetailView
from django_htmx.http import HttpResponseClientRedirect

from .models import Conversation, Message
from .tasks import process_chat_message


class SidebarMixin:
    """Adds sidebar conversation groups and available models to context."""

    def get_sidebar_context(self) -> dict:
        conversations = list(
            Conversation.objects.filter(
                interface=Conversation.Interface.WEB
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
        return {
            "conversation_groups": [(label, items) for label, items in groups if items],
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
        conversation = Conversation.objects.create(
            interface=Conversation.Interface.WEB,
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
        if not conversation.title:
            conversation.title = content[:60]
            conversation.save(update_fields=["title", "updated_at"])

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
            # Agent is PENDING or RUNNING (or WAITING with no pending TE yet) — keep polling
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
            html = render_to_string(
                "chat/_message.html", {"message": assistant_msg}, request=request
            )
        else:
            html = render_to_string(
                "chat/_typing_indicator.html",
                {"conversation": conversation, "user_msg_id": user_msg.id},
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
