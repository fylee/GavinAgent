"""
Spec 029 — Remove agent_mcpserver table.

Run `export_mcp_to_file` BEFORE applying this migration to preserve any
existing server configurations in mcp_servers.json.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("agent", "0014_add_mcp_session_dead_fields"),
    ]

    operations = [
        migrations.DeleteModel(
            name="MCPServer",
        ),
    ]
