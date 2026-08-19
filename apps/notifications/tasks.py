"""
Notification consumers.

One task per event topic. They could be a single generic task, but explicit
tasks keep the outbox rule intact — a payload's keys are passed straight through
as the handler's kwargs, so each contract stays typed, greppable, and
independently retryable.

Every task here is routed to the `notifications` queue (see CELERY_TASK_ROUTES)
and consumed by its own Deployment, so a stalled mail provider cannot block
embedding generation or feed fan-out.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from apps.notifications import services

_RETRY = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_jitter": True,
    "max_retries": 3,
}


def _task(topic: str) -> Any:
    """Build a consumer that forwards its payload straight to deliver()."""

    def handler(**payload: Any) -> None:
        services.deliver(kind=topic, **payload)

    handler.__name__ = topic.replace(".", "_")
    return shared_task(name=f"notifications.{handler.__name__}", **_RETRY)(handler)


contact_request_created = _task("contact_request.created")
contact_request_accepted = _task("contact_request.accepted")
engagement_requested = _task("engagement.requested")
engagement_accepted = _task("engagement.accepted")
listing_application_created = _task("listing.application_created")
listing_application_accepted = _task("listing.application_accepted")
band_invite_created = _task("band.invite_created")
band_invite_accepted = _task("band.invite_accepted")
