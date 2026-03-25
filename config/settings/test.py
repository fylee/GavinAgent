import os

# Allow synchronous DB operations in async contexts (needed for Playwright e2e tests)
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

from pathlib import Path

from .base import *  # noqa: F401, F403

# ── Core ─────────────────────────────────────────────────────────────────────
SECRET_KEY = "test-secret-key-not-for-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

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
# Don't run tasks eagerly in e2e tests — they block views and cause timeouts
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False
# Swallow broker connection errors so delay() doesn't crash views
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# ── Agent workspace ─────────────────────────────────────────────────────────
AGENT_WORKSPACE_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "workspace"
)

# ── Disable external services ───────────────────────────────────────────────
SEARXNG_URL = "http://searxng-test:8888"
LANGSMITH_API_KEY = ""

# ── Fernet key for MCPServer EncryptedJSONField ─────────────────────────────
from cryptography.fernet import Fernet as _Fernet

FERNET_KEYS = [_Fernet.generate_key().decode()]

# ── Speed up tests ──────────────────────────────────────────────────────────
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
