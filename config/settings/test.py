from pathlib import Path

from .base import *  # noqa: F401, F403

# ── Database ─────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "agent_test_db",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "localhost",
        "PORT": "5432",
    }
}

# ── Celery ───────────────────────────────────────────────────────────────────
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# ── Agent workspace ─────────────────────────────────────────────────────────
AGENT_WORKSPACE_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "workspace"
)

# ── Disable external services ───────────────────────────────────────────────
SEARXNG_URL = "http://searxng-test:8888"
LANGSMITH_API_KEY = ""

# ── Speed up tests ──────────────────────────────────────────────────────────
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
