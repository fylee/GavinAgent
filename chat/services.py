from __future__ import annotations
from .models import Conversation, Message
from core.llm import get_completion


class ChatService:
    def __init__(self, conversation: Conversation):
        self.conversation = conversation

    def build_messages(self) -> list[dict]:
        msgs = []
        if self.conversation.system_prompt:
            msgs.append({"role": "system", "content": self.conversation.system_prompt})
        for m in self.conversation.messages.filter(
            role__in=[Message.Role.USER, Message.Role.ASSISTANT]
        ).order_by("created_at"):
            msgs.append({"role": m.role, "content": m.content})
        return msgs

    def reply(self) -> Message:
        """Generate and save the assistant reply for the current conversation state."""
        messages = self.build_messages()
        model = self.conversation.model or None
        kwargs: dict = {}
        if self.conversation.temperature is not None:
            kwargs["temperature"] = self.conversation.temperature
        if self.conversation.max_tokens is not None:
            kwargs["max_tokens"] = self.conversation.max_tokens
        response = get_completion(messages, model=model, source="chat", conversation=self.conversation, **kwargs)
        choice = response.choices[0].message
        return Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.ASSISTANT,
            content=choice.content,
            model=response.model,
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
        )
