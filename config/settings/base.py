from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django_htmx",
    "django_celery_beat",
    "core",
    "chat",
    "agent",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

import dj_database_url

DATABASES = {
    "default": dj_database_url.config(
        default=config(
            "DATABASE_URL",
            default="postgresql://postgres:postgres@localhost:5432/agent_db",
        ),
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Celery
CELERY_BROKER_URL = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"

# Telegram
TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default="")

# LiteLLM
LITELLM_DEFAULT_MODEL = config("LITELLM_DEFAULT_MODEL", default="openai/gpt-4o-mini")

AVAILABLE_MODELS = [
    ("openai/gpt-4o-mini", "GPT-4o mini"),
    ("openai/gpt-4o", "GPT-4o"),
    ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("anthropic/claude-opus-4-6", "Claude Opus 4.6"),
]

# Agent workspace
AGENT_WORKSPACE_DIR = config(
    "AGENT_WORKSPACE_DIR", default=str(BASE_DIR / "agent" / "workspace")
)

# Agent timezone — used for system prompt datetime and default workflow cron timezone
# Use any IANA timezone name: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
AGENT_TIMEZONE = config("AGENT_TIMEZONE", default="UTC")

# Skill similarity threshold for embedding-based routing (0.0–1.0, higher = stricter)
AGENT_SKILL_SIMILARITY_THRESHOLD = config(
    "AGENT_SKILL_SIMILARITY_THRESHOLD", default=0.35, cast=float
)

# Maximum tool-call rounds per agent run before force-concluding
AGENT_MAX_TOOL_CALL_ROUNDS = config("AGENT_MAX_TOOL_CALL_ROUNDS", default=20, cast=int)

# Number of recent chat messages to include as context (filters old/poisoned history)
AGENT_HISTORY_WINDOW = config("AGENT_HISTORY_WINDOW", default=10, cast=int)

# Heartbeat interval (minutes)
AGENT_HEARTBEAT_INTERVAL_MINUTES = config(
    "AGENT_HEARTBEAT_INTERVAL_MINUTES", default=30, cast=int
)

# Context window budget (tokens left for output)
AGENT_CONTEXT_BUDGET_TOKENS = config(
    "AGENT_CONTEXT_BUDGET_TOKENS", default=8000, cast=int
)

# Tool execution constraints
AGENT_TOOL_TIMEOUT_SECONDS = config(
    "AGENT_TOOL_TIMEOUT_SECONDS", default=30, cast=int
)
AGENT_BROWSER_TIMEOUT_SECONDS = config(
    "AGENT_BROWSER_TIMEOUT_SECONDS", default=60, cast=int
)
MAX_TOOL_OUTPUT_CHARS = config("MAX_TOOL_OUTPUT_CHARS", default=20000, cast=int)

# MCP — Fernet encryption keys for MCPServer.env field
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEYS = [
    k for k in [
        config("FERNET_KEY", default=""),
        config("FERNET_KEY_PREVIOUS", default=""),
    ] if k
]

# LangSmith tracing (optional)
LANGSMITH_API_KEY = config("LANGSMITH_API_KEY", default="")
LANGSMITH_PROJECT = config("LANGSMITH_PROJECT", default="agent")
