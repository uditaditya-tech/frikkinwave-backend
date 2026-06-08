"""
Models for the social app — the follow graph (Phase 5, Block A).

Data-shape only — business logic lives in services.
Both ends of an edge are Users, referenced via AUTH_USER_MODEL as a string ref,
so there is no cross-app model import. Follows are user→user only for now;
band / venue targets are a later extension.
"""

import uuid

import uuid6
from django.conf import settings
from django.db import models


def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()


class Follow(models.Model):
    """A directed follow edge: `follower` follows `followed`."""

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="following_set",
    )
    followed = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="follower_set",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "followed"],
                name="unique_follow_edge",
            ),
            models.CheckConstraint(
                condition=~models.Q(follower=models.F("followed")),
                name="no_self_follow",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.follower_id} → {self.followed_id}"
