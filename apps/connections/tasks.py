"""
Celery tasks for the connections app — contact-request email notifications.

These are event handlers, not inline calls: services emit them via
``transaction.on_commit(... .delay())`` after the DB row commits (scale rule #4
in CLAUDE.md). The task name + ``request_id`` payload is the message contract
that becomes a Kafka schema when connections is extracted into its own service.

Tasks stay thin: they delegate to the service layer, which owns the email
business logic. Transient send failures retry with backoff; a missing request
is handled inside the service (logged, no exception) so it never poisons the
queue with endless retries.
"""

from __future__ import annotations

from celery import shared_task

from apps.connections import services


@shared_task(
    name="connections.notify_new_contact_request",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def notify_new_contact_request(request_id: str) -> None:
    """Email the recipient that a new contact request is waiting for them."""
    services.notify_recipient_of_request(request_id=request_id)


@shared_task(
    name="connections.notify_contact_request_accepted",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def notify_contact_request_accepted(request_id: str) -> None:
    """Email the sender that their contact request was accepted."""
    services.notify_sender_of_acceptance(request_id=request_id)
