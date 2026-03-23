"""
Seed a default general-purpose agent if no agent named 'Agentic Assistant'
already exists and no default agent is already configured.

Safe to run multiple times — never overwrites existing data.
"""
import uuid
from django.db import migrations


DEFAULT_SYSTEM_PROMPT = """\
You are a capable, proactive assistant. Today's date is {{today}}.

You have access to tools — use them whenever they would help give a better answer. \
Don't ask permission before using a tool; just use it. If a task requires multiple \
steps or tool calls, work through them autonomously until you have a complete answer.

Be concise and direct. Prefer doing over explaining.\
"""

DEFAULT_TOOLS = ["web_read", "api_get", "file_read", "file_write", "shell", "chart", "get_datetime", "reload_workflows"]


def seed_default_agent(apps, schema_editor):
    Agent = apps.get_model("agent", "Agent")

    # Don't create if an agent with this name already exists
    if Agent.objects.filter(name="Agentic Assistant").exists():
        return

    # Don't mark as default if one already exists
    has_default = Agent.objects.filter(is_default=True).exists()

    Agent.objects.create(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        name="Agentic Assistant",
        description="General-purpose assistant with access to web, files, shell, and chart tools.",
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        tools=DEFAULT_TOOLS,
        model="openai/gpt-4o-mini",
        is_active=True,
        is_default=not has_default,
    )


def unseed_default_agent(apps, schema_editor):
    Agent = apps.get_model("agent", "Agent")
    Agent.objects.filter(id=uuid.UUID("00000000-0000-0000-0000-000000000001")).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("agent", "0007_add_workflow_model"),
    ]

    operations = [
        migrations.RunPython(seed_default_agent, reverse_code=unseed_default_agent),
    ]
