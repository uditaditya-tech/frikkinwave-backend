"""
Celery tasks for the musicians app — embedding generation.

Event handler, not an inline call: create_profile / update_profile emit this via
transaction.on_commit (scale rule #4 in CLAUDE.md). The task stays thin and
delegates to the service, which owns the build-text → embed → store logic.

Transient OpenAI failures retry with backoff. A missing profile, a missing API
key, and unchanged embedding text are all handled inside the service (logged,
no exception) so they never trigger retries.
"""

from __future__ import annotations

from celery import shared_task

from apps.musicians import services


@shared_task(
    name="musicians.generate_profile_embedding",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def generate_profile_embedding_task(profile_id: str) -> None:
    """Compute and store the embedding for a profile (see services)."""
    services.generate_profile_embedding(profile_id=profile_id)
