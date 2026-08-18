"""
Celery task for the outbox relay.

Two triggers, deliberately:
  - the **nudge** — `publish()` fires this on_commit for low latency. Losing it
    is harmless.
  - the **sweep** — `manage.py relay_outbox` (a K8s CronJob in production) is
    what actually *guarantees* delivery.
"""

from __future__ import annotations

from celery import shared_task

from apps.events import services


@shared_task(
    name="events.relay_outbox",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def relay_outbox() -> int:
    """Publish pending outbox events. Returns how many were dispatched."""
    return services.relay_pending()
