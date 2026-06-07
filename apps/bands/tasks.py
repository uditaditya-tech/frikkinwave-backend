"""
Celery tasks for the bands app — band invitation email notifications.

Event handlers, not inline calls: services emit them via
``transaction.on_commit(... .delay())`` after the DB row commits (scale rule #4
in CLAUDE.md). The task name + ``membership_id`` payload is the message contract
that becomes a Kafka schema when bands is extracted.

Tasks stay thin: they delegate to the service layer, which owns the email
business logic. Transient send failures retry with backoff; a missing
membership is handled inside the service (logged, no exception) so it never
poisons the queue with endless retries.
"""

from __future__ import annotations

from celery import shared_task

from apps.bands import services


@shared_task(
    name="bands.notify_band_invite",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def notify_band_invite(membership_id: str) -> None:
    """Email the invited user that a band wants them on the roster."""
    services.notify_member_of_invite(membership_id=membership_id)


@shared_task(
    name="bands.notify_band_invite_accepted",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def notify_band_invite_accepted(membership_id: str) -> None:
    """Email the band owner that an invitee accepted."""
    services.notify_owner_of_acceptance(membership_id=membership_id)
