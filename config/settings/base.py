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

# Where the Django admin is mounted. Trailing slash required — it is passed
# straight to `path()`.
#
# Defence in depth, NOT the control: moving the admin off the default path only
# costs a scanner one guess, and AdminLoginThrottleMiddleware is what actually
# bounds guessing. It defaults to "admin/" so nothing changes unless the
# variable is set. The throttle reverses `admin:login` rather than hardcoding a
# path, so it follows this wherever it points.
ADMIN_PATH = env("DJANGO_ADMIN_PATH", default="admin/")

# Whether /api/schema/ and /api/docs/ answer anonymous callers.
#
# True here because local dev and CI want them open — the setup instructions in
# CLAUDE.md tell you to open /api/docs/ to check the server is alive.
# production.py sets it False, which makes both staff-only: the schema lists
# every path, parameter and response shape in the project, which is a map of the
# attack surface rather than something to hand out.
SCHEMA_IS_PUBLIC = env.bool("DJANGO_SCHEMA_IS_PUBLIC", default=True)

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
    # Above SessionMiddleware on purpose: a throttled admin login POST is
    # refused before the session and user lookups it would otherwise cost,
    # so a brute-force attempt cannot drive database load by being rejected.
    "apps.users.middleware.AdminLoginThrottleMiddleware",
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

# Persistent connections. Django's default is CONN_MAX_AGE=0 — a fresh connection
# per request — and on RDS that handshake was **measured at 16.9ms while the query
# it exists to run took 0.58ms**, so ~29x the request's real work was spent getting
# a socket. It is most of the 36ms floor on every DB-backed endpoint.
#
# Safe here without a pooler: peak concurrent connections under a c=800 load test
# was 6 — one per gunicorn worker (2 pods x 3) — against a db.t4g.micro ceiling of
# roughly 112. The pooler question only arrives when pods x workers approaches
# max_connections; that is PgBouncer's job, not this setting's.
#
# CONN_HEALTH_CHECKS is what makes reuse safe: a connection idle across requests
# can be closed by the server (RDS failover, idle timeout) and would otherwise
# surface as an OperationalError on the next query instead of a reconnect.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DJANGO_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

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
    # Rates for `ScopedRateThrottle`, applied per view via `throttle_scope`.
    #
    # There is deliberately NO `DEFAULT_THROTTLE_CLASSES`: every throttled
    # request costs a cache read and write, and the cache is the database (see
    # CACHES below). A global default would put those on the profile feed and
    # search — the two highest-volume endpoints — to meter scraping, which is
    # not the threat being addressed here. Only the credential endpoints are
    # scoped, and they are low-volume by nature.
    #
    # All three scopes key on the client IP, because the caller is anonymous at
    # exactly the moment that matters. NUM_PROXIES in production.py is what
    # makes that IP the real one rather than the load balancer's.
    "DEFAULT_THROTTLE_RATES": {
        # Generous enough to survive a fat-fingered password and a retry;
        # useless for credential stuffing, which needs thousands of attempts.
        "auth_login": "10/min",
        # Account creation is a rare act for a real person and a bulk one for
        # an abuser. Per hour, not per minute, so a shared NAT does not lock a
        # whole office out after three signups.
        "auth_register": "10/hour",
        # A session refreshes every 15 minutes (ACCESS_TOKEN_LIFETIME), so a
        # user with several devices and tabs is still far inside this.
        "auth_refresh": "60/hour",
        # Tighter than user login: there are a handful of staff accounts, never
        # a burst of legitimate admin sign-ins, and the prize is larger.
        # Applied by AdminLoginThrottleMiddleware, not by a DRF view — /admin/
        # is a session-auth Django view that DRF's throttles never see.
        "admin_login": "5/min",
    },
}

# ---------------------------------------------------------------------------
# Cache
#
# Exists for ONE purpose: somewhere for throttle counters to live that every pod
# can see. Redis went away with Celery (KAFKA.md stage 5) and is not coming back
# for this, so the store is the database.
#
# The default — LocMemCache — would be actively misleading here. It is
# per-process, and production runs 2 pods x 3 gunicorn workers, so a "10/min"
# login limit would really be six independent counters permitting up to 60/min,
# and which one a request hit would depend on the load balancer. A throttle that
# looks configured and is not is worse than no throttle, because nobody
# re-checks it.
#
# The table is created by a migration (`users/0002_throttle_cache_table`) rather
# than by `createcachetable`, so it exists in test databases and on a fresh
# deploy without a separate step anyone has to remember.
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "throttle_cache",
    }
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
# Search (OpenSearch)
#
# An empty URL is a supported state, not a misconfiguration: local dev and CI
# run without a cluster and search degrades to an empty result set. Same
# contract the empty OpenAI key had — "not configured" and "upstream down" are
# deliberately one case, because an upstream failure must never 500 a user.
# ---------------------------------------------------------------------------
OPENSEARCH_URL = env("OPENSEARCH_URL", default="")

# Index name. Configurable so a full rebuild can be written into a fresh index
# and swapped in by alias, and so a test run can isolate itself from a dev index
# sitting in the same local cluster.
OPENSEARCH_INDEX = env("OPENSEARCH_INDEX", default="profiles")

# Seconds before a READ is abandoned. See the comment on the client: this bounds
# how long a request thread can be held by a slow cluster, so it is deliberately
# short — search degrades to [] rather than exhausting the worker pool.
OPENSEARCH_TIMEOUT = env.float("OPENSEARCH_TIMEOUT", default=3.0)

# Seconds before a WRITE is abandoned — indexing, pruning, the rebuild.
#
# Much longer, because the constraint is the opposite one. Writes run in a Kafka
# consumer and in a deploy Job, where nothing is waiting on the other end and a
# timeout is not free: it burns one of KAFKA_CONSUMER_MAX_ATTEMPTS (3), and
# running out sends the event to the dead-letter topic, leaving that profile
# silently stale in search until the next rebuild.
#
# Measured in production 2026-08-23: a COLD connection to the VPC endpoint —
# DNS, TCP, TLS handshake — did not complete inside 3s, twice in a row, and the
# event was indexed on attempt 3 of 3. The same call took 0.128s once the pool
# was warm. Sharing the read timeout meant a routine cold start consumed the
# entire retry budget and left nothing for an actual transient.
OPENSEARCH_WRITE_TIMEOUT = env.float("OPENSEARCH_WRITE_TIMEOUT", default=20.0)

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

#: Where the relay records that its poll loop is still turning, for the
#: liveness probe. The one deliberate exception to "never write to local disk"
#: (CLAUDE.md): it is liveness evidence that must die with the pod, not state.
EVENT_RELAY_HEARTBEAT_FILE = env("EVENT_RELAY_HEARTBEAT_FILE", default="/tmp/relay-heartbeat")

#: Seconds the relay loop sleeps when it finds nothing. The upper bound on event
#: latency, so keep it short — this is what replaced the Celery post-commit
#: nudge. A full batch skips the sleep entirely so a backlog drains at speed.
EVENT_RELAY_INTERVAL = env.float("EVENT_RELAY_INTERVAL", default=1.0)

#: How long a PUBLISHED outbox row is kept before `purge_outbox` deletes it.
#:
#: The outbox is a queue, not a ledger: once the relay has produced an event,
#: Kafka is the durable record and the row has done its whole job. Kept forever
#: the table grows without bound, and its payload carries recipient email
#: addresses — so this is a data-retention setting as much as a storage one.
#:
#: Seven days is long enough to still have the row while investigating something
#: that happened last week, and short enough that "forever" is not the answer.
#: UNPUBLISHED rows are never purged at any age — see `purge_published`.
EVENT_OUTBOX_RETENTION_DAYS = env.int("EVENT_OUTBOX_RETENTION_DAYS", default=7)

#: Port the relay serves Prometheus metrics on, in `--loop` mode only.
#:
#: The relay is the highest-consequence pod here — one replica, and if it is
#: down NOTHING is delivered. Consumer-lag alerts cannot see that: a stalled
#: relay means nothing reaches Kafka to be lagged on, so every Kafka-side alert
#: stays silent while the whole pipeline is dead. Scraping this endpoint is what
#: makes the relay's own liveness alertable.
EVENT_RELAY_METRICS_PORT = env.int("EVENT_RELAY_METRICS_PORT", default=9100)

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
    # Four models define a `Status` choices class, and drf-spectacular names enum
    # components after the FIELD — so all four collided and it resolved the clash
    # with a hash, `Status7baEnum`: a valid schema and a terrible name to meet in
    # generated client code.
    #
    # There are only TWO distinct sets, which is the interesting part.
    # BandMembership, ContactRequest and ListingApplication have byte-identical
    # choices, so they are one component and naming them separately is an error
    # ("multiple names for the same choice set"). EngagementRequest adds
    # `completed`, which makes it genuinely different.
    "ENUM_NAME_OVERRIDES": {
        # pending / accepted / declined — the shared invite-and-respond lifecycle.
        "RequestStatusEnum": "apps.connections.models.ContactRequest.Status",
        # The same, plus `completed`: an engagement outlives its acceptance.
        "EngagementStatusEnum": "apps.engagements.models.EngagementRequest.Status",
    },
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
