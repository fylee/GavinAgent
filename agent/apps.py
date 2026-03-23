from django.apps import AppConfig


class AgentConfig(AppConfig):
    name = "agent"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        import agent.signals  # noqa: F401 — registers signal receivers

        from agent.workspace import ensure_workspace
        try:
            ensure_workspace()
        except Exception:
            # Don't crash Django startup if workspace can't be created
            pass

        # Load skills into registry
        from pathlib import Path
        from django.conf import settings
        from agent.skills.loader import SkillLoader
        from agent.skills import registry

        skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
        try:
            loader = SkillLoader(skills_dir)
            loader.load_all(registry)
        except Exception:
            pass

        # Load workflows from workspace/workflows/
        try:
            from agent.workflows.loader import WorkflowLoader
            WorkflowLoader().load_all()
        except Exception:
            pass

        # Embed skills for semantic routing (run in a thread — DB must be ready)
        import threading
        def _embed_skills():
            try:
                from agent.skills.embeddings import embed_all_skills
                embed_all_skills()
            except Exception:
                pass
        threading.Thread(target=_embed_skills, daemon=True).start()

        # Start MCP connection pool (connects all enabled MCP servers)
        try:
            from agent.mcp.pool import MCPConnectionPool
            MCPConnectionPool.get().start_all()
        except Exception:
            pass
