import logging
import os
from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("agent")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

logger = logging.getLogger(__name__)


@worker_process_init.connect
def init_mcp(sender, **kwargs):
    """Start MCP connections in each worker process (prefork-safe)."""
    try:
        from agent.mcp.pool import MCPConnectionPool
        MCPConnectionPool.get().start_all()
    except Exception as exc:
        logger.error("MCP pool init failed: %s", exc, exc_info=True)


@worker_process_shutdown.connect
def shutdown_mcp(sender, **kwargs):
    try:
        from agent.mcp.pool import MCPConnectionPool
        MCPConnectionPool.get().stop_all()
    except Exception as exc:
        logger.warning("MCP pool shutdown error: %s", exc)
