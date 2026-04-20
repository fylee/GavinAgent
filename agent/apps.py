from django.apps import AppConfig


class AgentConfig(AppConfig):
    name = "agent"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        import agent.signals  # noqa: F401 — registers signal receivers

        # When running as an MCP server, skip all expensive startup work
        # (workspace setup, skill DB sync, embedding) so mcp.run() is reached
        # before the MCP client connection times out.
        import os
        if os.environ.get("GAVIN_MCP_SERVER"):
            return

        from agent.workspace import ensure_workspace
        try:
            ensure_workspace()
        except Exception:
            # Don't crash Django startup if workspace can't be created
            pass

        # Load skills into registry from all source directories (Spec 023).
        # check_db_trust=False during startup — DB may not be fully ready yet,
        # and the trust check only gates embedding, not registry loading.
        from agent.skills.discovery import all_skill_dirs
        from agent.skills.loader import SkillLoader
        from agent.skills import registry

        all_loaded: list[str] = []
        last_src = None
        try:
            sources = all_skill_dirs(check_db_trust=False)
            for src in sources:
                loader = SkillLoader(src.path)
                loaded = loader.load_all(registry)
                all_loaded.extend(loaded)
                last_src = src
        except Exception:
            pass

        # Sync loaded skills to DB after migrations are complete.
        # Using post_migrate avoids RuntimeWarning from DB access in ready().
        from django.db.models.signals import post_migrate

        def _sync_skills_to_db(sender, **kwargs):
            from agent.models import Skill
            for name in all_loaded:
                entry = registry.get(name)
                if entry and last_src:
                    try:
                        Skill.objects.get_or_create(
                            name=name,
                            defaults={
                                "description": entry.description,
                                "path": entry.path,
                                "source_dir": str(last_src.path),
                                "enabled": True,
                            },
                        )
                    except Exception:
                        pass

        post_migrate.connect(_sync_skills_to_db, weak=False)

        # Load workflows after migrations are complete (accesses DB).
        def _load_workflows(sender, **kwargs):
            try:
                from agent.workflows.loader import WorkflowLoader
                WorkflowLoader().load_all()
            except Exception:
                pass

        post_migrate.connect(_load_workflows, weak=False)

        # Embed skills for semantic routing (run in a thread — DB must be ready).
        # Spec 023: native_only=True at startup to avoid latency from large external
        # skill collections. Use `python manage.py embed_skills` for extra dirs.
        import threading
        def _embed_skills():
            try:
                from agent.skills.embeddings import embed_all_skills
                embed_all_skills(native_only=True)
            except Exception:
                pass
        threading.Thread(target=_embed_skills, daemon=True).start()

        # MCP connection pool is started exclusively by the Celery worker_init
        # signal (config/celery.py). Do NOT start it here — the Django dev server
        # runs ready() in multiple processes (autoreloader parent + child) and
        # would compete with the Celery worker for the same SSE connections,
        # leaving the worker's registry empty.
