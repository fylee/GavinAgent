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

            # Sync loaded skills to DB (create missing rows, don't overwrite enabled flag)
            from agent.models import Skill
            for name, entry in registry.all().items():
                Skill.objects.get_or_create(
                    name=name,
                    defaults={
                        "description": entry.description,
                        "path": entry.path,
                        "enabled": True,
                    },
                )
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

        # MCP connection pool is started exclusively by the Celery worker_init
        # signal (config/celery.py). Do NOT start it here — the Django dev server
        # runs ready() in multiple processes (autoreloader parent + child) and
        # would compete with the Celery worker for the same SSE connections,
        # leaving the worker's registry empty.
