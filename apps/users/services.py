"""
Service layer for the users app.

All business logic lives here — views and tasks call services, never models directly.
No imports from other apps' models.
"""

import logging

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
