"""
Models for the listings app — the gig & audition board.

Data-shape only — business logic lives in services.
FKs use AUTH_USER_MODEL as a string ref, so there is no cross-app model import.
"""

import uuid

import uuid6
from django.conf import settings
from django.db import models


def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()


class Listing(models.Model):
    """A gig, audition, or venue posting that musicians can browse and apply to."""

    class ListingType(models.TextChoices):
        GIG = "gig", "Gig"
        AUDITION = "audition", "Audition"
        VENUE = "venue", "Venue"

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    listing_type = models.CharField(
        max_length=10,
        choices=ListingType.choices,
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    is_paid = models.BooleanField(default=False)
    pay_description = models.CharField(max_length=200, blank=True)
    deadline = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.listing_type})"


class ListingApplication(models.Model):
    """A musician's application to a listing. The contact-request variant for the board."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listing_applications",
    )
    message = models.TextField(blank=True)
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
                fields=["listing", "applicant"],
                name="unique_application_per_listing",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.applicant_id} → {self.listing_id} ({self.status})"
