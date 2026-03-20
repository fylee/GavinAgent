from uuid import uuid4
from django.db import models
from pgvector.django import VectorField, HnswIndex
from core.models import TimeStampedModel


class Agent(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid4)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    system_prompt = models.TextField()
    tools = models.JSONField(default=list)
    model = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict)

    def save(self, *args, **kwargs):
        if self.is_default:
            Agent.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class AgentRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        WAITING = "waiting", "Waiting"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class TriggerSource(models.TextChoices):
        WEB = "web", "Web"
        TELEGRAM = "telegram", "Telegram"
        CLI = "cli", "CLI"
        HEARTBEAT = "heartbeat", "Heartbeat"

    id = models.UUIDField(primary_key=True, default=uuid4)
    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="runs")
    conversation = models.ForeignKey(
        "chat.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    trigger_source = models.CharField(max_length=20, choices=TriggerSource.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    input = models.TextField()
    output = models.TextField(blank=True)
    graph_state = models.JSONField(default=dict)
    celery_task_id = models.CharField(max_length=255, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["agent", "status"]),
        ]

    def __str__(self):
        return f"AgentRun {self.id} ({self.status})"


class ToolExecution(TimeStampedModel):
    """Audit log of every tool call the agent made or attempted."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid4)
    run = models.ForeignKey(
        AgentRun, on_delete=models.CASCADE, related_name="tool_executions"
    )
    tool_name = models.CharField(max_length=100)
    input = models.JSONField(default=dict)
    output = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    requires_approval = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ToolExecution {self.tool_name} ({self.status})"


class HeartbeatLog(TimeStampedModel):
    """One record per Celery Beat heartbeat trigger."""

    class Status(models.TextChoices):
        OK = "ok", "OK"
        ACTED = "acted", "Acted"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid4)
    triggered_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices)
    actions_taken = models.JSONField(default=list)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-triggered_at"]

    def __str__(self):
        return f"HeartbeatLog {self.triggered_at} ({self.status})"


class Skill(TimeStampedModel):
    """Registry of installed skills."""

    id = models.UUIDField(primary_key=True, default=uuid4)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    path = models.CharField(max_length=500)
    enabled = models.BooleanField(default=True)
    installed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class LLMUsage(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid4)
    model = models.CharField(max_length=100)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    source = models.CharField(max_length=20, default="unknown")  # "agent" | "chat" | "unknown"
    run = models.ForeignKey(
        AgentRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="llm_usages"
    )
    conversation = models.ForeignKey(
        "chat.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="llm_usages",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["source", "created_at"]),
        ]

    def __str__(self):
        return f"LLMUsage {self.model} {self.total_tokens}t ({self.source})"


class ReembedLog(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid4)
    paragraph_count = models.IntegerField()
    records_added = models.IntegerField(default=0)
    records_deleted = models.IntegerField(default=0)
    triggered_by = models.CharField(max_length=20, default="auto")  # "auto" | "manual" | "file_write"

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ReembedLog {self.created_at} ({self.paragraph_count} paragraphs)"


class Memory(TimeStampedModel):
    """Vector memory store. Requires pgvector extension."""

    id = models.UUIDField(primary_key=True, default=uuid4)
    agent = models.ForeignKey(
        Agent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memories",
    )
    conversation = models.ForeignKey(
        "chat.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    content = models.TextField()
    embedding = VectorField(dimensions=1536)
    source = models.CharField(max_length=50, blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        indexes = [
            HnswIndex(
                name="memory_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            )
        ]

    def __str__(self):
        return f"Memory {self.id}: {self.content[:60]}"
