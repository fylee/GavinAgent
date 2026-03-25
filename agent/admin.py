from django.contrib import admin
from .models import (
    Agent, AgentRun, DocumentChunk, HeartbeatLog, KnowledgeDocument,
    LLMUsage, Memory, ReembedLog, Skill, ToolExecution,
)


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ["name", "model", "is_active", "is_default", "created_at", "updated_at"]
    list_filter = ["is_active", "is_default", "model", "created_at"]
    search_fields = ["name", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["name"]


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ["id", "agent", "status", "trigger_source", "started_at", "finished_at", "created_at"]
    list_filter = ["status", "trigger_source", "agent", "created_at"]
    search_fields = ["id", "input", "output", "error"]
    readonly_fields = ["id", "created_at", "updated_at", "celery_task_id"]
    ordering = ["-created_at"]
    raw_id_fields = ["agent", "conversation"]


@admin.register(ToolExecution)
class ToolExecutionAdmin(admin.ModelAdmin):
    list_display = ["id", "tool_name", "status", "requires_approval", "duration_ms", "created_at"]
    list_filter = ["status", "tool_name", "requires_approval", "created_at"]
    search_fields = ["tool_name", "id"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]
    raw_id_fields = ["run"]


@admin.register(HeartbeatLog)
class HeartbeatLogAdmin(admin.ModelAdmin):
    list_display = ["id", "triggered_at", "status"]
    list_filter = ["status", "triggered_at"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-triggered_at"]


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ["name", "enabled", "installed_at"]
    list_filter = ["enabled"]
    search_fields = ["name", "description"]
    readonly_fields = ["id", "installed_at", "created_at", "updated_at"]


@admin.register(Memory)
class MemoryAdmin(admin.ModelAdmin):
    list_display = ["id", "agent", "source", "created_at"]
    list_filter = ["agent", "source", "created_at"]
    search_fields = ["content", "id"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]
    raw_id_fields = ["agent", "conversation"]


@admin.register(LLMUsage)
class LLMUsageAdmin(admin.ModelAdmin):
    list_display = ["id", "model", "source", "total_tokens", "estimated_cost_usd", "created_at"]
    list_filter = ["source", "model", "created_at"]
    search_fields = ["model", "id"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]
    raw_id_fields = ["run", "conversation"]


@admin.register(ReembedLog)
class ReembedLogAdmin(admin.ModelAdmin):
    list_display = ["id", "paragraph_count", "records_added", "records_deleted", "triggered_by", "created_at"]
    list_filter = ["triggered_by", "created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ["title", "source_type", "status", "chunk_count", "is_active", "created_at"]
    list_filter = ["source_type", "status", "is_active", "created_at"]
    search_fields = ["title"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ["id", "document", "chunk_index", "token_count", "created_at"]
    list_filter = ["document", "created_at"]
    search_fields = ["content"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["document", "chunk_index"]
    raw_id_fields = ["document"]
