"""
Service layer for the connections app.

All business logic lives here. Views call services; services call models.
Cross-app access goes through apps.users.services — never a model import.
apps.users.models.User is referenced only under TYPE_CHECKING.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.db.models import Q

from apps.connections.models import ContactRequest
from apps.users.services import get_user_ref

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)


class RecipientNotFoundError(Exception):
    """No user exists for the given recipient username."""


class SelfContactError(Exception):
    """A user tried to send a contact request to themselves."""


class DuplicateContactRequestError(Exception):
    """A contact request from this sender to this recipient already exists."""


class NotPendingError(Exception):
    """An accept/decline was attempted on a request that is not pending."""


def send_contact_request(
    *,
    sender: User,
    recipient_username: str,
    message: str = "",
) -> ContactRequest:
    """
    Create a pending contact request from `sender` to the user identified by
    `recipient_username`.

    Raises:
        RecipientNotFoundError — no such recipient.
        SelfContactError — sender and recipient are the same user.
        DuplicateContactRequestError — a request for this pair already exists.
    """
    recipient = get_user_ref(username=recipient_username)
    if recipient is None:
        raise RecipientNotFoundError

    if recipient.id == sender.pk:
        raise SelfContactError

    try:
        request = ContactRequest.objects.create(
            sender=sender,
            recipient_id=recipient.id,
            message=message,
        )
    except IntegrityError as exc:
        raise DuplicateContactRequestError from exc

    # Emit the "new contact request" event once the row actually commits, so a
    # rolled-back transaction never enqueues a task pointing at a phantom row.
    # Local import avoids a tasks ↔ services import cycle.
    from apps.events.services import publish

    # Self-contained payload: the consumer is a separate service and must not
    # need a database read to send this.
    publish(
        topic="contact_request.created",
        payload={
            "recipient_email": recipient.email,
            "sender_username": sender.username,
            "message": request.message,
        },
    )

    logger.info(
        "contact_request_sent",
        extra={"request_id": str(request.id), "sender_id": str(sender.pk)},
    )
    return request


def list_contact_requests(*, user: User, box: str) -> QuerySet[ContactRequest]:
    """
    Return the user's contact requests for the given box.

    box="incoming" → requests where the user is the recipient.
    box="outgoing" → requests where the user is the sender.
    """
    queryset = ContactRequest.objects.select_related("sender", "recipient")
    if box == "outgoing":
        queryset = queryset.filter(sender=user)
    else:
        queryset = queryset.filter(recipient=user)
    logger.info("contact_requests_listed", extra={"user_id": str(user.pk), "box": box})
    return queryset


def get_contact_request(*, user: User, request_id: str) -> ContactRequest | None:
    """
    Return a single contact request if `user` is a party to it, else None.

    Non-parties get None (the view turns that into a 404) so a request's
    existence is never leaked to outsiders.
    """
    return (
        ContactRequest.objects.select_related("sender", "recipient")
        .filter(id=request_id)
        .filter(Q(sender=user) | Q(recipient=user))
        .first()
    )


def accept_contact_request(*, user: User, request_id: str) -> ContactRequest | None:
    """
    Accept a pending request. Only the recipient may accept.

    Returns the updated request, or None if no pending request with that id is
    addressed to `user`. Raises NotPendingError if it exists but is resolved.
    """
    return _resolve(user=user, request_id=request_id, new_status=ContactRequest.Status.ACCEPTED)


def decline_contact_request(*, user: User, request_id: str) -> ContactRequest | None:
    """Decline a pending request. Only the recipient may decline. See accept."""
    return _resolve(user=user, request_id=request_id, new_status=ContactRequest.Status.DECLINED)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve(*, user: User, request_id: str, new_status: str) -> ContactRequest | None:
    request = (
        ContactRequest.objects.select_related("sender", "recipient")
        .filter(id=request_id, recipient=user)
        .first()
    )
    if request is None:
        return None
    if request.status != ContactRequest.Status.PENDING:
        raise NotPendingError
    request.status = new_status
    request.save(update_fields=["status", "updated_at"])

    # Notify the sender only when their request is accepted (decline is silent).
    if new_status == ContactRequest.Status.ACCEPTED:
        from apps.events.services import publish

        publish(
            topic="contact_request.accepted",
            payload={
                "recipient_email": request.sender.email,
                "accepter_username": request.recipient.username,
                "accepter_email": request.recipient.email,
            },
        )

    logger.info(
        "contact_request_resolved",
        extra={"request_id": str(request.id), "status": new_status},
    )
    return request
