"""
Celery tasks for the engagements app — hire-request email notifications.

Event handlers, not inline calls: services emit them via
``transaction.on_commit(... .delay())`` after the DB row commits (scale rule #4
in CLAUDE.md). The task name + ``engagement_id`` payload is the message contract
that becomes a Kafka schema when engagements is extracted.

Tasks stay thin: they delegate to the service layer, which owns the email
business logic. Transient send failures retry with backoff; a missing request
is handled inside the service (logged, no exception) so it never poisons the
queue with endless retries.
"""

from __future__ import annotations

from celery import shared_task

from apps.engagements import services


@shared_task(
    name="engagements.notify_new_engagement_request",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def notify_new_engagement_request(engagement_id: str) -> None:
    """Email the musician that someone wants to hire them."""
    services.notify_musician_of_request(engagement_id=engagement_id)


@shared_task(
    name="engagements.notify_engagement_request_accepted",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def notify_engagement_request_accepted(engagement_id: str) -> None:
    """Email the requester that their hire request was accepted."""
    services.notify_requester_of_acceptance(engagement_id=engagement_id)
