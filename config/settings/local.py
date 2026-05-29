"""
Local development settings.
Uses SQLite or local Postgres via DATABASE_URL in .env.
"""

from .base import *

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# Allow all origins in local dev
CORS_ALLOW_ALL_ORIGINS = True

# Disable password hashing speed-up for faster tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Django Debug Toolbar (optional — only if installed)
try:
    import debug_toolbar  # type: ignore[import-not-found]  # noqa: F401

    INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]
    MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware", *MIDDLEWARE]
    INTERNAL_IPS = ["127.0.0.1"]
except ImportError:
    pass

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Human-readable logs in local dev (base.py defaults to JSON for production)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "{levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "DEBUG",
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.db.queries": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
    },
}
