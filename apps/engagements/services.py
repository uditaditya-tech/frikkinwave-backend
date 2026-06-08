"""
Service layer for the engagements app.

All business logic lives here. Views call services; services call models.
Cross-app access (username → user) goes through apps.users.services — never a
model import. apps.users.models.User is referenced only under TYPE_CHECKING.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q

from apps.engagements.models import EngagementRequest
from apps.users.services import get_user_by_username

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)


class MusicianNotFoundError(Exception):
    """No user exists for the given musician username."""


class SelfEngagementError(Exception):
    """A user tried to hire themselves."""


class NotPendingError(Exception):
    """An accept/decline was attempted on a request that is not pending."""


class NotAcceptedError(Exception):
    """A complete was attempted on a request that is not accepted."""


def send_engagement_request(
    *,
    requester: User,
    musician_username: str,
    message: str = "",
    proposed_date: date | None = None,
    rate_offer: str = "",
) -> EngagementRequest:
    """
    Create a pending hire request from `requester` to the named musician.

    Raises:
        MusicianNotFoundError — no such musician.
        SelfEngagementError — requester and musician are the same user.
    """
    musician = get_user_by_username(username=musician_username)
    if musician is None:
        raise MusicianNotFoundError
    if musician.pk == requester.pk:
        raise SelfEngagementError

    engagement = EngagementRequest.objects.create(
        requester=requester,
        musician=musician,
        message=message,
        proposed_date=proposed_date,
        rate_offer=rate_offer,
    )

    # Emit once the row commits, so a rolled-back transaction never enqueues a
    # task pointing at a phantom row. Local import avoids a tasks ↔ services cycle.
    from apps.engagements.tasks import notify_new_engagement_request

    transaction.on_commit(lambda: notify_new_engagement_request.delay(str(engagement.id)))

    logger.info(
        "engagement_request_sent",
        extra={"engagement_id": str(engagement.id), "requester_id": str(requester.pk)},
    )
    return engagement


def list_engagement_requests(*, user: User, box: str) -> QuerySet[EngagementRequest]:
    """
    Return the user's engagement requests for the given box.

    box="incoming" → requests where the user is the hired musician.
    box="outgoing" → requests the user sent as a hirer.
    """
    queryset = EngagementRequest.objects.select_related("requester", "musician")
    if box == "outgoing":
        queryset = queryset.filter(requester=user)
    else:
        queryset = queryset.filter(musician=user)
    return queryset


def get_engagement_request(*, user: User, engagement_id: str) -> EngagementRequest | None:
    """
    Return a single request if `user` is a party to it, else None — so its
    existence is never leaked to outsiders.
    """
    return (
        EngagementRequest.objects.select_related("requester", "musician")
        .filter(id=engagement_id)
        .filter(Q(requester=user) | Q(musician=user))
        .first()
    )


def accept_engagement_request(*, user: User, engagement_id: str) -> EngagementRequest | None:
    """Accept a pending request. Only the hired musician may accept."""
    return _resolve(
        user=user, engagement_id=engagement_id, new_status=EngagementRequest.Status.ACCEPTED
    )


def decline_engagement_request(*, user: User, engagement_id: str) -> EngagementRequest | None:
    """Decline a pending request. Only the hired musician may decline."""
    return _resolve(
        user=user, engagement_id=engagement_id, new_status=EngagementRequest.Status.DECLINED
    )


def complete_engagement_request(*, user: User, engagement_id: str) -> EngagementRequest | None:
    """
    Mark an accepted request completed. Either party may do so.

    Returns None if no request with that id involves `user`. Raises
    NotAcceptedError if it exists but is not in the accepted state.
    """
    engagement = (
        EngagementRequest.objects.select_related("requester", "musician")
        .filter(id=engagement_id)
        .filter(Q(requester=user) | Q(musician=user))
        .first()
    )
    if engagement is None:
        return None
    if engagement.status != EngagementRequest.Status.ACCEPTED:
        raise NotAcceptedError
    engagement.status = EngagementRequest.Status.COMPLETED
    engagement.save(update_fields=["status", "updated_at"])
    logger.info("engagement_request_completed", extra={"engagement_id": str(engagement.id)})
    return engagement


def parties_of_completed_engagement(*, engagement_id: str) -> set[uuid.UUID] | None:
    """
    Return the two party user-ids of a COMPLETED engagement, or None if no such
    completed engagement exists.

    Public cross-app gate for the reviews app: a caller verifies that two users
    actually finished an engagement together (so a review can't be left against a
    stranger) without importing the EngagementRequest model.
    """
    engagement = EngagementRequest.objects.filter(
        id=engagement_id, status=EngagementRequest.Status.COMPLETED
    ).first()
    if engagement is None:
        return None
    return {engagement.requester_id, engagement.musician_id}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve(*, user: User, engagement_id: str, new_status: str) -> EngagementRequest | None:
    engagement = (
        EngagementRequest.objects.select_related("requester", "musician")
        .filter(id=engagement_id, musician=user)
        .first()
    )
    if engagement is None:
        return None
    if engagement.status != EngagementRequest.Status.PENDING:
        raise NotPendingError
    engagement.status = new_status
    engagement.save(update_fields=["status", "updated_at"])

    # Notify the requester only when their request is accepted (decline is silent).
    if new_status == EngagementRequest.Status.ACCEPTED:
        from apps.engagements.tasks import notify_engagement_request_accepted

        transaction.on_commit(lambda: notify_engagement_request_accepted.delay(str(engagement.id)))

    logger.info(
        "engagement_request_resolved",
        extra={"engagement_id": str(engagement.id), "status": new_status},
    )
    return engagement


# ---------------------------------------------------------------------------
# Email notifications (invoked by Celery tasks in apps/engagements/tasks.py)
# ---------------------------------------------------------------------------


def notify_musician_of_request(*, engagement_id: str) -> None:
    """
    Email the musician that someone wants to hire them.

    A missing request (deleted before the task ran) is logged and ignored rather
    than raised — the task must not retry forever on a row that is gone.
    """
    engagement = (
        EngagementRequest.objects.select_related("requester", "musician")
        .filter(id=engagement_id)
        .first()
    )
    if engagement is None:
        logger.warning("notify_musician_skipped_missing_request", extra={"id": engagement_id})
        return

    requester_name = engagement.requester.username
    body = f"{requester_name} wants to hire you for session work on frikkinwave."
    if engagement.proposed_date:
        body += f"\n\nProposed date: {engagement.proposed_date.isoformat()}"
    if engagement.rate_offer:
        body += f"\nRate offered: {engagement.rate_offer}"
    if engagement.message:
        body += f'\n\nThey said:\n"{engagement.message}"'
    body += "\n\nLog in to frikkinwave to accept or decline."

    send_mail(
        subject=f"{requester_name} wants to hire you on frikkinwave",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[engagement.musician.email],
    )
    logger.info("engagement_request_notification_sent", extra={"id": engagement_id})


def notify_requester_of_acceptance(*, engagement_id: str) -> None:
    """
    Email the requester that the musician accepted, revealing the musician's
    contact email (the reveal-on-accept rule). Missing request: logged, ignored.
    """
    engagement = (
        EngagementRequest.objects.select_related("requester", "musician")
        .filter(id=engagement_id)
        .first()
    )
    if engagement is None:
        logger.warning("notify_requester_skipped_missing_request", extra={"id": engagement_id})
        return

    musician_name = engagement.musician.username
    body = (
        f"{musician_name} accepted your hire request on frikkinwave.\n\n"
        f"You can now reach them at: {engagement.musician.email}"
    )

    send_mail(
        subject=f"{musician_name} accepted your hire request",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[engagement.requester.email],
    )
    logger.info("engagement_request_acceptance_sent", extra={"id": engagement_id})
