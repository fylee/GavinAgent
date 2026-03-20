from __future__ import annotations

import logging

from chat.signals import message_created

logger = logging.getLogger(__name__)


def on_message_created(sender, conversation_id: str, message_id: str, **kwargs) -> None:
    """Listen for new user messages and trigger the agent if the conversation has one active."""
    from chat.models import Conversation, Message
    from agent.models import AgentRun
    from agent.runner import AgentRunner

    try:
        conversation = Conversation.objects.select_related("active_agent").get(pk=conversation_id)
    except Conversation.DoesNotExist:
        return

    agent = conversation.active_agent
    if agent is None or not agent.is_active:
        return

    # If there's already a WAITING run (paused for approval), resume it.
    waiting_run = (
        AgentRun.objects.filter(
            conversation=conversation,
            status=AgentRun.Status.WAITING,
        )
        .first()
    )
    if waiting_run:
        AgentRunner.enqueue(waiting_run)
        return

    # Create a fresh run for this message.
    try:
        message = Message.objects.get(pk=message_id)
        input_text = message.content
    except Message.DoesNotExist:
        input_text = ""

    run = AgentRun.objects.create(
        agent=agent,
        conversation=conversation,
        trigger_source=AgentRun.TriggerSource.WEB,
        input=input_text,
    )
    AgentRunner.enqueue(run)


message_created.connect(on_message_created)
