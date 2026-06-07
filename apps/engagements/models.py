"""
Models for the engagements app — the session-musician hire-intent marketplace.

Data-shape only — business logic lives in services.
FKs use AUTH_USER_MODEL as a string ref, so there is no cross-app model import.
Hire-intent only: this tracks the request lifecycle, not real payments.
"""

import uuid

import uuid6
from django.conf import settings
from django.db import models


def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()


class EngagementRequest(models.Model):
    """A request to hire a musician for session/paid work. Musician accepts/declines."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_engagement_requests",
    )
    musician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_engagement_requests",
    )
    message = models.TextField(blank=True)
    proposed_date = models.DateField(null=True, blank=True)
    rate_offer = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.requester_id} → {self.musician_id} ({self.status})"
