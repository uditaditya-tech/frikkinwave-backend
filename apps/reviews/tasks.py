"""
Celery tasks for the reviews app — rating propagation.

Event handler, not an inline call: `create_review` publishes `review.created` to
the transactional outbox — ``publish()`` inside the caller's transaction (scale
rule #4 in CLAUDE.md) — so a profile read never has to aggregate the reviews
tables.

The topic + `subject_user_id` payload is the message contract that becomes a
Kafka event when reviews is extracted into its own service (see
MICROSERVICES.md). The handler recomputes from source, so redelivery is a no-op.
"""

from __future__ import annotations

from celery import shared_task

from apps.reviews import services


@shared_task(
    name="reviews.propagate_profile_rating",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def propagate_profile_rating(subject_user_id: str) -> None:
    """Recompute the subject's rating rollup and push it onto their profile."""
    services.propagate_rating_to_profile(subject_user_id=subject_user_id)
