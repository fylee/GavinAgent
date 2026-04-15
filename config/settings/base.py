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
# Agent timezone — used for system prompt datetime and default workflow cron timezone
# TIME_ZONE is set to match so that Django's |localtime filter uses the same zone.
TIME_ZONE = config("AGENT_TIMEZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = str(BASE_DIR / "media")

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
LLM_TIMEOUT_SECONDS = config("LLM_TIMEOUT_SECONDS", default=120, cast=int)

AVAILABLE_MODELS = [
    # OpenAI (direct)
    ("openai/gpt-4o-mini", "GPT-4o mini"),
    ("openai/gpt-4o", "GPT-4o"),
    # Anthropic
    ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("anthropic/claude-opus-4-6", "Claude Opus 4.6"),
    # Azure OpenAI — requires AZURE_API_KEY, AZURE_API_BASE, AZURE_API_VERSION
    ("azure/gpt-4o", "GPT-4o (Azure)"),
    ("azure/gpt-4o-mini", "GPT-4o mini (Azure)"),
    ("azure/gpt-4.1", "GPT-4.1 (Azure)"),
    ("azure/gpt-5", "GPT-5 (Azure)"),
    # Azure AI Model Catalog — requires AZURE_AI_API_KEY
    ("azure_ai/deepseek-r1", "DeepSeek-R1 (Azure AI)"),
    # Ollama (local) — requires Ollama running on OLLAMA_API_BASE
    ("ollama/llama3", "Llama 3 (Local)"),
    ("ollama/mistral", "Mistral (Local)"),
    ("ollama/phi3", "Phi-3 (Local)"),
]

# Embedding model — supports same provider prefixes as LiteLLM
# Examples: "openai/text-embedding-3-small", "azure/text-embedding-3-large"
EMBEDDING_MODEL = config("EMBEDDING_MODEL", default="openai/text-embedding-3-small")

# Agent workspace
AGENT_WORKSPACE_DIR = config(
    "AGENT_WORKSPACE_DIR", default=str(BASE_DIR / "agent" / "workspace")
)

# Path to the Claude Code CLI executable (used by agent/skills/author.py).
# On Windows, npm installs 'claude' as a .cmd shim which Python subprocess can't
# find by bare name.  Set this to the full path of claude.cmd, e.g.:
#   CLAUDE_CMD=C:\Users\fylee\AppData\Roaming\npm\claude.cmd
# If unset, author.py auto-detects via `npm prefix -g` at runtime.
CLAUDE_CMD = config("CLAUDE_CMD", default="")

# Agent timezone — configured once above as TIME_ZONE (also consumed by Django's |localtime).
AGENT_TIMEZONE = TIME_ZONE

# Skill similarity threshold for embedding-based routing (0.0–1.0, higher = stricter)
AGENT_SKILL_SIMILARITY_THRESHOLD = config(
    "AGENT_SKILL_SIMILARITY_THRESHOLD", default=0.35, cast=float
)

# Spec 023: Multi-source skill discovery
# Extra skill directories scanned in addition to agent/workspace/skills/.
# Format: comma-separated absolute paths.
from decouple import Csv as _Csv
AGENT_EXTRA_SKILLS_DIRS = config("AGENT_EXTRA_SKILLS_DIRS", default="", cast=_Csv())

# Whether to auto-scan standard agentskills.io directories:
# .agents/skills/, ~/.agents/skills/, ~/.claude/skills/
AGENT_SCAN_STANDARD_SKILL_DIRS = config(
    "AGENT_SCAN_STANDARD_SKILL_DIRS", default=True, cast=bool
)

# Spec 027: LLM call resilience — retry + fallback model chain
# Comma-separated list of models tried in order after the primary exhausts retries.
# Example: AGENT_FALLBACK_MODELS=anthropic/claude-haiku-4-5,openai/gpt-4o-mini
AGENT_FALLBACK_MODELS: list[str] = config(
    "AGENT_FALLBACK_MODELS", default="", cast=_Csv()
)
# Maximum retry attempts per model for transient errors (429, 503, 504, timeout).
AGENT_LLM_MAX_RETRIES: int = config("AGENT_LLM_MAX_RETRIES", default=3, cast=int)

# Spec 026: Skill enable/disable control
# Global list of skill names that are disabled on all platforms.
# Format: comma-separated skill names. Example: "skill-creator,mcp-builder"
AGENT_DISABLED_SKILLS: list[str] = config(
    "AGENT_DISABLED_SKILLS", default="", cast=_Csv()
)

# Per-platform disabled skills override.
# Format: "platform:skill-a,skill-b;platform2:skill-c"
# When set for a platform, replaces the global AGENT_DISABLED_SKILLS for that platform.
# Example: "chat:skill-creator;claude_code:fab-ops-analyst"
AGENT_PLATFORM_DISABLED_SKILLS: str = config(
    "AGENT_PLATFORM_DISABLED_SKILLS", default=""
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

# RAG Knowledge Base
RAG_CHUNK_SIZE_TOKENS = config("RAG_CHUNK_SIZE_TOKENS", default=500, cast=int)
RAG_CHUNK_OVERLAP_TOKENS = config("RAG_CHUNK_OVERLAP_TOKENS", default=50, cast=int)
RAG_SEARCH_LIMIT = config("RAG_SEARCH_LIMIT", default=5, cast=int)
RAG_SIMILARITY_THRESHOLD = config("RAG_SIMILARITY_THRESHOLD", default=0.3, cast=float)

# SearXNG — web search engine (used by web_search tool)
SEARXNG_URL = config("SEARXNG_URL", default="http://localhost:8888")

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
