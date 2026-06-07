"""
Celery tasks for the listings app — application email notifications.

Event handlers, not inline calls: services emit them via
``transaction.on_commit(... .delay())`` after the DB row commits (scale rule #4
in CLAUDE.md). The task name + ``application_id`` payload is the message
contract that becomes a Kafka schema when listings is extracted.

Tasks stay thin: they delegate to the service layer, which owns the email
business logic. Transient send failures retry with backoff; a missing
application is handled inside the service (logged, no exception) so it never
poisons the queue with endless retries.
"""

from __future__ import annotations

from celery import shared_task

from apps.listings import services


@shared_task(
    name="listings.notify_new_application",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def notify_new_application(application_id: str) -> None:
    """Email the listing author that a new application is waiting for them."""
    services.notify_author_of_application(application_id=application_id)


@shared_task(
    name="listings.notify_application_accepted",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def notify_application_accepted(application_id: str) -> None:
    """Email the applicant that their application was accepted."""
    services.notify_applicant_of_acceptance(application_id=application_id)
