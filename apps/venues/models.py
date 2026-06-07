"""
Models for the venues app — user-owned venue profiles.

Data-shape only — business logic lives in services.
FK uses AUTH_USER_MODEL as a string ref, so there is no cross-app model import.
The Phase 5 "venue user-type" is a later auth refinement; for now a venue is
simply owned by a regular user.
"""

import uuid

import uuid6
from django.conf import settings
from django.db import models


def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()


class Venue(models.Model):
    """A venue profile (club, bar, studio, hall), owned by one user."""

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="venues",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    website = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"
