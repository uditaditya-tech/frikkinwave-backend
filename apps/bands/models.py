"""
Models for the bands app — bands as group entities + member rosters.

Data-shape only — business logic lives in services.
FKs use AUTH_USER_MODEL as a string ref, so there is no cross-app model import.
A member is a User, not a musicians.MusicianProfile — profile data is joined
through musicians.services, keeping the apps decoupled for later extraction.
"""

import uuid

import uuid6
from django.conf import settings
from django.db import models


def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()


class Band(models.Model):
    """A band / group, owned by one user, with an invited member roster."""

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_bands",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=120, unique=True)
    bio = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


class BandMembership(models.Model):
    """An invitation / membership tying a user to a band. Owner invites; invitee resolves."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    band = models.ForeignKey(
        Band,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="band_memberships",
    )
    role = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["band", "member"],
                name="unique_membership_per_band",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.member_id} @ {self.band_id} ({self.status})"
