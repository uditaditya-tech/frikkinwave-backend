# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — builder: install dependencies into an isolated virtualenv.
# psycopg2-binary ships as a prebuilt wheel, so no compiler toolchain is needed.
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

# uv: fast, reproducible installs from the pinned requirements lockfile.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Build the venv from the lockfile only — code changes don't bust this layer.
COPY requirements/base.txt requirements/base.txt
RUN uv venv /opt/venv && \
    uv pip install --python /opt/venv/bin/python -r requirements/base.txt

# ---------------------------------------------------------------------------
# Stage 2 — runtime: slim image with just the venv + app code, run as non-root.
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

# Copy the prebuilt virtualenv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the application source.
COPY . .

# Collect static files at build time. WhiteNoise's manifest storage needs this
# to exist in the image. Production settings read several env vars at import,
# so feed placeholders — none of these values are baked into the static output.
RUN DJANGO_SECRET_KEY="build-time-placeholder-not-used-at-runtime" \
    DATABASE_URL="postgres://placeholder:placeholder@localhost:5432/placeholder" \
    ALLOWED_HOSTS="localhost" \
    CORS_ALLOWED_ORIGINS="https://localhost" \
    python manage.py collectstatic --noinput

# Run as an unprivileged user — the container filesystem is treated as read-only
# at runtime (stateless; all real storage is S3/RDS).
RUN useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Sync gunicorn workers — the app is sync Django/DRF (no async views).
# Worker count is tunable per task size via WEB_CONCURRENCY; logs go to stdout.
CMD ["sh", "-c", "gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers ${WEB_CONCURRENCY:-3} \
    --access-logfile - \
    --error-logfile -"]
