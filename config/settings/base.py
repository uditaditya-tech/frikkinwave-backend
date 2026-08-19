"""
Base settings shared across all environments.
Environment-specific files (local.py, production.py) import from here.
"""

from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# .env is loaded by local.py / production.py before importing base — or here
# as a fallback so manage.py commands (e.g. collectstatic) work without fuss.
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = []

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # required for pgvector's HnswIndex
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",
]

LOCAL_APPS = [
    # Platform apps (no domain concepts) come first.
    "apps.events",
    "apps.notifications",
    "apps.search",
    "apps.users",
    "apps.musicians",
    "apps.connections",
    "apps.listings",
    "apps.bands",
    "apps.engagements",
    "apps.venues",
    "apps.social",
    "apps.reviews",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Custom user model (must be set before first migration)
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "users.User"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL"),
}

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ---------------------------------------------------------------------------
# Default primary key
# We use UUIDv7 on each model explicitly; this is a safety net for any model
# that forgets to declare a pk.
# ---------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# ---------------------------------------------------------------------------
# JWT (simplejwt)
# ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ---------------------------------------------------------------------------
# OpenAI (Phase 2 AI — embeddings, blurbs, profile coach)
# Empty key is allowed: the embedding task skips + logs rather than erroring,
# so profiles still save without a key (local dev, or prod before the secret).
# ---------------------------------------------------------------------------
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
OPENAI_EMBEDDING_MODEL = env("OPENAI_EMBEDDING_MODEL", default="text-embedding-3-small")
OPENAI_CHAT_MODEL = env("OPENAI_CHAT_MODEL", default="gpt-4o-mini")

# Semantic search: minimum cosine similarity (1 - cosine distance) a profile must
# score against the query to be returned. Results below this are dropped, so a
# query with no good matches returns fewer (or zero) results rather than padding
# the list with weak ones. 0.0 disables the floor (return all nearest).
# Default 0.4: measured against text-embedding-3-small in prod, strong matches
# score ~0.72-0.78, moderate ~0.45-0.55, noise <0.3 — so 0.8 returned nothing.
SEARCH_SIMILARITY_THRESHOLD = env.float("SEARCH_SIMILARITY_THRESHOLD", default=0.4)

# ---------------------------------------------------------------------------
# Events (KAFKA.md)
#
# Kafka is the only transport. The EVENT_TRANSPORT flag is gone with stage 5 —
# it existed to make the switchover reversible, and it did its job twice.
# ---------------------------------------------------------------------------

#: Run the outbox relay INLINE in the producer's process, on commit.
#:
#: False in production: a synchronous Kafka produce in the request path would
#: add up to KAFKA_FLUSH_TIMEOUT to every user-facing request during a broker
#: outage. The relay Deployment (`relay_outbox --loop`) does the work instead.
#:
#: True under pytest, so `django_capture_on_commit_callbacks(execute=True)`
#: still drives events end to end without a broker or a relay process — the same
#: trick CELERY_TASK_ALWAYS_EAGER used to play. Set in local.py for the same
#: reason it was: settings load before any conftest body runs.
EVENT_RELAY_INLINE = env.bool("EVENT_RELAY_INLINE", default=False)

#: Seconds the relay loop sleeps when it finds nothing. The upper bound on event
#: latency, so keep it short — this is what replaced the Celery post-commit
#: nudge. A full batch skips the sleep entirely so a backlog drains at speed.
EVENT_RELAY_INTERVAL = env.float("EVENT_RELAY_INTERVAL", default=1.0)

# The credential and CA come from the
# Strimzi-generated Secrets (KafkaUser `frikkinwave-app` and the cluster CA),
# mounted into the worker and the relay CronJob — never baked into the image and
# never in the chart's values.
KAFKA_BOOTSTRAP_SERVERS = env("KAFKA_BOOTSTRAP_SERVERS", default="")
# SASL_SSL today (SCRAM-SHA-512). Kept as settings rather than hardcoded so
# moving to mTLS is a configuration change, not a code change.
KAFKA_SECURITY_PROTOCOL = env("KAFKA_SECURITY_PROTOCOL", default="SASL_SSL")
KAFKA_SASL_MECHANISM = env("KAFKA_SASL_MECHANISM", default="SCRAM-SHA-512")
KAFKA_SASL_USERNAME = env("KAFKA_SASL_USERNAME", default="")
KAFKA_SASL_PASSWORD = env("KAFKA_SASL_PASSWORD", default="")
KAFKA_SSL_CA_LOCATION = env("KAFKA_SSL_CA_LOCATION", default="")
# mTLS alternative — unset while using SCRAM.
KAFKA_SSL_CERTIFICATE_LOCATION = env("KAFKA_SSL_CERTIFICATE_LOCATION", default="")
KAFKA_SSL_KEY_LOCATION = env("KAFKA_SSL_KEY_LOCATION", default="")

# ---------------------------------------------------------------------------
# Kafka consumers (KAFKA.md stage 4)
# ---------------------------------------------------------------------------

#: Group ids are "<prefix>.<app>". The prefix must stay inside the KafkaUser's
#: group ACL (`frikkinwave`, patternType prefix) or every consumer fails
#: authorization — which looks like a consumer that starts and reads nothing.
KAFKA_CONSUMER_GROUP_PREFIX = env("KAFKA_CONSUMER_GROUP_PREFIX", default="frikkinwave")

#: In-process attempts before a message is dead-lettered. Small on purpose: the
#: partition is stalled for the whole retry, so this covers a transient blip and
#: nothing more. Celery's equivalent could afford to be generous because a stuck
#: task blocked only itself.
KAFKA_CONSUMER_MAX_ATTEMPTS = env.int("KAFKA_CONSUMER_MAX_ATTEMPTS", default=3)

#: Seconds, multiplied by the attempt number for a linear backoff.
KAFKA_CONSUMER_RETRY_BACKOFF = env.float("KAFKA_CONSUMER_RETRY_BACKOFF", default=1.0)

#: Suffix for the dead-letter topic of any given topic.
KAFKA_DLT_SUFFIX = env("KAFKA_DLT_SUFFIX", default=".dlt")

#: Poll timeout. Bounds how long a SIGTERM waits before the loop notices.
KAFKA_CONSUMER_POLL_TIMEOUT = env.float("KAFKA_CONSUMER_POLL_TIMEOUT", default=1.0)

#: Seconds to wait for the broker to acknowledge. The relay marks an event
#: published only after this returns, so it must be a real bound, not infinite.
KAFKA_FLUSH_TIMEOUT = env.float("KAFKA_FLUSH_TIMEOUT", default=10.0)

# ---------------------------------------------------------------------------
# drf-spectacular (OpenAPI)
# ---------------------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    "TITLE": "frikkinwave API",
    "DESCRIPTION": "Global musician network — jam, gig, connect.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------------------------------------------------------------------------
# Email
# Default sender for transactional email (contact-request notifications, etc.).
# Local dev uses the console backend; production overrides this from env and
# uses SMTP. Defined here so every environment has a sender address.
# ---------------------------------------------------------------------------
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@frikkinwave.com")

# ---------------------------------------------------------------------------
# CORS (frontend origin configured per environment)
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS: list[str] = []

# ---------------------------------------------------------------------------
# Logging — structured JSON output
# All log output is JSON so it's parseable by CloudWatch / Datadog on EKS.
# Never use print() — always use logging.getLogger(__name__).
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "fmt": "%(asctime)s %(name)s %(levelname)s %(message)s",
        },
        "simple": {
            # Human-readable fallback for local dev (overridden in local.py)
            "format": "{levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.db.queries": {
            "handlers": ["console"],
            "level": "WARNING",  # Set to DEBUG locally to see SQL
            "propagate": False,
        },
    },
}
