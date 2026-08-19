"""
Notification delivery.

The only app in the codebase whose service layer touches no models at all. It
receives primitives, renders copy, and sends. That is what makes it extractable:
point it at a broker and it needs nothing else from this process.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.mail import send_mail

from apps.notifications.renderers import RENDERERS

logger = logging.getLogger(__name__)


class UnknownNotificationKind(Exception):
    """Raised when a kind has no renderer — a producer/consumer contract break."""


def deliver(*, kind: str, recipient_email: str, **context: Any) -> None:
    """
    Render and send one notification.

    A missing recipient address is skipped rather than raised: it means the
    producer published an incomplete event, and retrying cannot conjure an
    address. Retrying forever on a poisoned message is worse than dropping it
    with a log line.

    An unknown `kind`, by contrast, IS raised. It means the registry and this
    service have drifted, which is a deploy-ordering bug worth surfacing loudly
    (and the architecture test below catches it before it ships).
    """
    if not recipient_email:
        logger.warning("notification_skipped_no_recipient", extra={"kind": kind})
        return

    renderer = RENDERERS.get(kind)
    if renderer is None:
        raise UnknownNotificationKind(kind)

    subject, body = renderer(**context)

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
    )
    logger.info("notification_sent", extra={"kind": kind, "to": recipient_email})
