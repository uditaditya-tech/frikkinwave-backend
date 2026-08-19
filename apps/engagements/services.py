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

from django.db.models import Q

from apps.engagements.models import EngagementRequest
from apps.users.services import get_user_ref

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
    musician = get_user_ref(username=musician_username)
    if musician is None:
        raise MusicianNotFoundError
    if musician.id == requester.pk:
        raise SelfEngagementError

    engagement = EngagementRequest.objects.create(
        requester=requester,
        musician_id=musician.id,
        message=message,
        proposed_date=proposed_date,
        rate_offer=rate_offer,
    )

    # Emit once the row commits, so a rolled-back transaction never enqueues a
    # task pointing at a phantom row. Local import avoids a tasks ↔ services cycle.
    from apps.events.services import publish

    # Self-contained payload: the consumer is a separate service and must not
    # need a database read to send this.
    publish(
        topic="engagement.requested",
        payload={
            "recipient_email": musician.email,
            "requester_username": requester.username,
            # Nullable field. The old notify task called .isoformat() on it
            # unguarded — that crashed too, but inside a Celery retry loop where
            # it was invisible. Building the payload in the request path makes
            # the same bug a 500, so it has to be handled honestly.
            "proposed_date": (
                engagement.proposed_date.isoformat() if engagement.proposed_date else ""
            ),
            "rate_offer": engagement.rate_offer,
            "message": engagement.message,
        },
    )

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
        from apps.events.services import publish

        publish(
            topic="engagement.accepted",
            payload={
                "recipient_email": engagement.requester.email,
                "musician_username": engagement.musician.username,
                "musician_email": engagement.musician.email,
            },
        )

    logger.info(
        "engagement_request_resolved",
        extra={"engagement_id": str(engagement.id), "status": new_status},
    )
    return engagement
