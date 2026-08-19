"""
Celery consumers for the search app.

Routed to the `search` queue (CELERY_TASK_ROUTES) and consumed by their own
Deployment: embedding work is spiky and bound by OpenAI latency, so it must not
compete with feed fan-out on the general worker.

Payloads are self-contained — the handler never reads a profile row.
"""

from __future__ import annotations

from celery import shared_task

from apps.search import services


@shared_task(
    name="search.index_profile",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def index_profile(profile_id: str, embedding_text: str, is_available: bool = True) -> None:
    """Embed and store a profile's text (see services.index_profile)."""
    services.index_profile(
        profile_id=profile_id,
        embedding_text=embedding_text,
        is_available=is_available,
    )
