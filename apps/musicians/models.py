"""
Models for the musicians app.

MusicianProfile extends the User with music-specific data.
User → MusicianProfile is a one-to-one relationship created at profile setup time.

Design decisions:
- city/country as free-text for Phase 1 simplicity; can normalise to lookup table
  later via a migration if geo-search demands it.
- is_available signals jam-partner availability — the Phase 1 anchor feature.
- Reference AUTH_USER_MODEL via settings string to avoid cross-app model imports.
"""

import uuid
from typing import ClassVar

import uuid6
from django.conf import settings
from django.db import models


def _new_uuid() -> uuid.UUID:
    """
    Generate a UUIDv7 (time-ordered, index-friendly).
    Uses the uuid6 backport — upgrade to stdlib uuid.uuid7() when on Python 3.14+.
    """
    return uuid6.uuid7()


class MusicianProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="musician_profile",
    )

    bio = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    is_available = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"MusicianProfile({self.user_id})"
