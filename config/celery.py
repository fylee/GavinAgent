import os
from celery import Celery
from celery.signals import worker_init, worker_shutdown

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("agent")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_init.connect
def init_mcp(sender, **kwargs):
    try:
        from agent.mcp.pool import MCPConnectionPool
        MCPConnectionPool.get().start_all()
    except Exception:
        pass


@worker_shutdown.connect
def shutdown_mcp(sender, **kwargs):
    try:
        from agent.mcp.pool import MCPConnectionPool
        MCPConnectionPool.get().stop_all()
    except Exception:
        pass
