"""
Service layer for the users app.

All business logic lives here — views and tasks call services, never models directly.
No imports from other apps' models.
"""

import logging
import uuid
from dataclasses import dataclass

from apps.users.models import User

logger = logging.getLogger(__name__)


def register_user(*, email: str, username: str, password: str) -> User:
    """
    Create a new user account.

    Caller is responsible for validating input (e.g. via RegisterSerializer)
    before calling this function.
    """
    user = User.objects.create_user(email=email, username=username, password=password)
    logger.info("user_registered", extra={"user_id": str(user.id), "email": user.email})
    return user


@dataclass(frozen=True, slots=True)
class UserRef:
    """
    A serializable snapshot of a user's identity — the public contract other
    apps see, in place of the ORM object.

    This is what an Identity service's `getUser` would return over the wire, so
    callers can only depend on data that survives a network hop. They must not
    receive a `User` model: it drags in the ORM, lazy relations, and this app's
    schema, none of which exist across a service boundary (MICROSERVICES.md §6).

    Assign it to a foreign key by id (`member_id=ref.id`), never by instance.
    """

    id: uuid.UUID
    username: str
    email: str


def get_user_ref(*, username: str) -> UserRef | None:
    """
    Resolve a username (case-insensitive) to a `UserRef`, or None.

    **The** public cross-app entry point for identity lookup. Returns a DTO, not
    a model, so nothing outside this app can reach into the ORM graph.
    """
    row = User.objects.filter(username__iexact=username).values("id", "username", "email").first()
    if row is None:
        return None
    return UserRef(**row)


def get_user_by_username(*, username: str) -> User | None:
    """
    Return the `User` **model** for a username, or None.

    Internal to the users app (and its own views/serializers). Other apps must
    use `get_user_ref` instead — returning an ORM object across an app boundary
    is exactly the coupling that blocks service extraction.
    """
    return User.objects.filter(username__iexact=username).first()
