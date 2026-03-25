from uuid import uuid4
import json
from django.db import models
from pgvector.django import VectorField, HnswIndex
from core.models import TimeStampedModel


class EncryptedJSONField(models.TextField):
    """
    Stores a JSON-serialisable dict as a Fernet-encrypted string.
    Requires settings.FERNET_KEYS = [current_key, ...optional_old_keys].
    """

    def _get_fernet(self):
        from cryptography.fernet import Fernet, MultiFernet
        from django.conf import settings
        keys = [k.encode() for k in getattr(settings, "FERNET_KEYS", []) if k]
        if not keys:
            raise ValueError("FERNET_KEYS must be set in settings to use EncryptedJSONField")
        return MultiFernet([Fernet(k) for k in keys])

    def from_db_value(self, value, expression, connection):
        if not value:
            return {}
        try:
            return json.loads(self._get_fernet().decrypt(value.encode()).decode())
        except Exception:
            return {}

    def to_python(self, value):
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception:
            return {}

    def get_prep_value(self, value):
        if not value:
            value = {}
        return self._get_fernet().encrypt(json.dumps(value).encode()).decode()


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
        WORKFLOW = "workflow", "Workflow"

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
    triggered_skills = models.JSONField(default=list, blank=True)
    workflow = models.ForeignKey(
        "Workflow",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="runs",
    )
    workflow_step = models.IntegerField(null=True, blank=True)
    workflow_step_name = models.CharField(max_length=100, blank=True)
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


class Workflow(TimeStampedModel):
    """A scheduled multi-step workflow defined by a YAML file."""

    id = models.UUIDField(primary_key=True, default=uuid4)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    agent = models.ForeignKey(
        Agent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflows",
    )
    conversation = models.ForeignKey(
        "chat.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflows",
    )
    enabled = models.BooleanField(default=True)
    definition = models.JSONField()
    filename = models.CharField(max_length=255)
    delivery = models.CharField(max_length=20, default="announce")
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    celery_beat_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def step_count(self) -> int:
        return len(self.definition.get("steps", []))

    @property
    def schedule_display(self) -> str:
        trigger = self.definition.get("trigger", {})
        if "cron" in trigger:
            tz = trigger.get("timezone", "UTC")
            return f"cron: {trigger['cron']} ({tz})"
        if "interval_minutes" in trigger:
            return f"every {trigger['interval_minutes']} min"
        if "at" in trigger:
            return f"once at {trigger['at']}"
        return "unknown"


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


class MCPServer(TimeStampedModel):
    """Configuration for a Model Context Protocol server (local stdio or remote SSE)."""

    class Transport(models.TextChoices):
        STDIO = "stdio", "stdio (local process)"
        SSE = "sse", "SSE (remote HTTP)"

    class ConnectionStatus(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        CONNECTED = "connected", "Connected"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid4)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    transport = models.CharField(max_length=10, choices=Transport.choices)

    # stdio: shell command to launch the server process
    # e.g. "npx -y @modelcontextprotocol/server-filesystem /workspace"
    command = models.CharField(max_length=500, blank=True)

    # sse: remote endpoint URL
    url = models.CharField(max_length=500, blank=True)

    # Encrypted at rest. For stdio: injected as env vars. For SSE: sent as HTTP headers.
    env = EncryptedJSONField(default=dict, blank=True)

    # Tool names that may execute without user approval
    auto_approve_tools = models.JSONField(default=list, blank=True)

    # If True, all read_resource calls for this server are auto-approved
    auto_approve_resources = models.BooleanField(default=False)

    # Resource URIs to inject into every agent run's system context
    always_include_resources = models.JSONField(default=list, blank=True)

    connection_status = models.CharField(
        max_length=20,
        choices=ConnectionStatus.choices,
        default=ConnectionStatus.UNKNOWN,
    )
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        from django.core.exceptions import ValidationError
        if self.transport == self.Transport.STDIO and not self.command.strip():
            raise ValidationError({"command": "Required for stdio transport."})
        if self.transport == self.Transport.SSE and not self.url.strip():
            raise ValidationError({"url": "Required for SSE transport."})
        if " " in self.name or not self.name.replace("-", "").replace("_", "").isalnum():
            raise ValidationError({"name": "Name must be slug-like (letters, numbers, hyphens, underscores only)."})
        if not isinstance(self.env, dict):
            raise ValidationError({"env": "Must be a JSON object."})
        for k, v in self.env.items():
            if not isinstance(v, str):
                raise ValidationError({"env": f"Value for '{k}' must be a string."})

    @property
    def env_json(self) -> str:
        import json as _json
        return _json.dumps(self.env, indent=2) if self.env else ""


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


class SkillEmbedding(TimeStampedModel):
    """Stores a semantic embedding for each workspace skill for similarity-based routing."""

    skill_name = models.CharField(max_length=100, unique=True)
    embedding = VectorField(dimensions=1536)
    content_hash = models.CharField(max_length=64)  # SHA-256 of embedded text

    class Meta:
        indexes = [
            HnswIndex(
                name="skill_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            )
        ]

    def __str__(self):
        return f"SkillEmbedding({self.skill_name})"


class KnowledgeDocument(TimeStampedModel):
    """A source document in the knowledge base."""

    class SourceType(models.TextChoices):
        UPLOAD = "upload", "File Upload"
        URL = "url", "Web URL"
        TEXT = "text", "Pasted Text"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid4)
    title = models.CharField(max_length=255)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    source_url = models.URLField(blank=True)
    raw_content = models.TextField(help_text="Original full text")
    metadata = models.JSONField(default=dict)
    chunk_count = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class DocumentChunk(TimeStampedModel):
    """An embedded chunk of a KnowledgeDocument for vector search."""

    id = models.UUIDField(primary_key=True, default=uuid4)
    document = models.ForeignKey(
        KnowledgeDocument,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    content = models.TextField()
    embedding = VectorField(dimensions=1536)
    chunk_index = models.PositiveIntegerField()
    token_count = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(max_length=64)

    class Meta:
        ordering = ["document", "chunk_index"]
        indexes = [
            HnswIndex(
                name="docchunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            )
        ]

    def __str__(self):
        return f"DocumentChunk {self.document.title}[{self.chunk_index}]"
