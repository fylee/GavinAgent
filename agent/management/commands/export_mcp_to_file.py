"""
Spec 029 — One-time migration helper.

Reads MCPServer records from the database and writes them to
agent/workspace/mcp_servers.json.  Run this BEFORE deploying the
file-based config and running `migrate` to drop the DB table.

Usage:
    uv run python manage.py export_mcp_to_file
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Export MCPServer DB records to agent/workspace/mcp_servers.json (run before migration)."

    def handle(self, *args, **options) -> None:
        try:
            from agent.models import MCPServer
        except ImportError:
            self.stderr.write(
                self.style.ERROR(
                    "MCPServer model not found — the DB table may already have been removed."
                )
            )
            return

        try:
            servers = list(MCPServer.objects.all())
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f"Cannot query MCPServer table: {exc}")
            )
            return

        if not servers:
            self.stdout.write("No MCPServer records found — nothing to export.")
            return

        from agent.mcp.config import MCPServerConfig, save_servers

        configs: dict[str, MCPServerConfig] = {}
        for srv in servers:
            transport = srv.transport  # "stdio" | "sse"
            env_dict = dict(srv.env or {})

            # The old DB model stored everything in `env` for both transports.
            # Split into env (stdio) and headers (SSE) in the new schema.
            if transport == "sse":
                headers = env_dict
                env = {}
            else:
                headers = {}
                env = env_dict

            # command may be a full "npx -y @mcp/server" string; split into command + args.
            import shlex
            parts = shlex.split(srv.command or "") if transport == "stdio" else []
            command = parts[0] if parts else (srv.command or "")
            cmd_args = parts[1:] if len(parts) > 1 else []

            cfg = MCPServerConfig(
                name=srv.name,
                type=transport,
                url=srv.url or "",
                headers=headers,
                command=command,
                args=cmd_args,
                env=env,
                enabled=srv.enabled,
                description=getattr(srv, "description", "") or "",
                auto_approve_tools=list(srv.auto_approve_tools or []),
                auto_approve_resources=getattr(srv, "auto_approve_resources", False),
                always_include_resources=list(srv.always_include_resources or []),
                session_dead_error_codes=list(srv.session_dead_error_codes or []),
                health_probe_tool=srv.health_probe_tool or "",
            )
            configs[srv.name] = cfg
            self.stdout.write(f"  Exporting [{transport}]: {srv.name}")

        save_servers(configs)
        self.stdout.write(self.style.SUCCESS(
            f"\nExported {len(configs)} server(s) to mcp_servers.json.\n"
            "Next steps:\n"
            "  1. Verify agent/workspace/mcp_servers.json looks correct.\n"
            "  2. Deploy new code.\n"
            "  3. Run: uv run python manage.py migrate"
        ))
