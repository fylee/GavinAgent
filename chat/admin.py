from django.contrib import admin
from .models import Conversation, Message


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "interface", "external_id", "created_at", "updated_at"]
    list_filter = ["interface", "created_at"]
    search_fields = ["title", "external_id", "id"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["id", "conversation", "role", "model", "input_tokens", "output_tokens", "created_at"]
    list_filter = ["role", "model", "created_at"]
    search_fields = ["content", "id", "conversation__title"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["created_at"]
    raw_id_fields = ["conversation"]
