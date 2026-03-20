from uuid import uuid4
from django.db import models
from core.models import TimeStampedModel


class Conversation(TimeStampedModel):
    class Interface(models.TextChoices):
        WEB = "web", "Web"
        TELEGRAM = "telegram", "Telegram"
        CLI = "cli", "CLI"

    id = models.UUIDField(primary_key=True, default=uuid4)
    interface = models.CharField(max_length=20, choices=Interface.choices)
    external_id = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=255, blank=True)
    system_prompt = models.TextField(blank=True)
    model         = models.CharField(max_length=100, blank=True, default="")
    temperature   = models.FloatField(null=True, blank=True)
    max_tokens    = models.IntegerField(null=True, blank=True)
    metadata      = models.JSONField(default=dict)
    # When set, this agent handles all incoming messages for this conversation.
    active_agent  = models.ForeignKey(
        "agent.Agent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="active_conversations",
    )

    class Meta:
        indexes = [
            models.Index(fields=["interface", "external_id"]),
        ]

    def __str__(self):
        return self.title or f"Conversation {self.id}"


class Message(TimeStampedModel):
    class Role(models.TextChoices):
        SYSTEM = "system", "System"
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        TOOL = "tool", "Tool"

    id = models.UUIDField(primary_key=True, default=uuid4)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    model = models.CharField(max_length=100, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:60]}"
