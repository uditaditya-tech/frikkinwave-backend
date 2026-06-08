"""
Models for the reviews app — ratings + reviews (Phase 5, Block C).

Data-shape only — business logic lives in services.
A review is gated on a real completed interaction, but it does NOT FK across apps:
the gating interaction is referenced by a denormalized `context_type` + `context_id`
(no cross-app FK), verified through the owning app's service at create time. This
keeps reviews gate-agnostic — new gates are additive.
Both `author` and `subject` are Users via AUTH_USER_MODEL string refs.
"""

import uuid

import uuid6
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()


class Review(models.Model):
    """One user's 1-5 star rating + comment about another, tied to one interaction."""

    class Context(models.TextChoices):
        ENGAGEMENT = "engagement", "Engagement"

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews_written",
    )
    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews_received",
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    comment = models.TextField(blank=True)
    context_type = models.CharField(max_length=32, choices=Context.choices)
    context_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["author", "context_id"],
                name="unique_review_per_author_per_context",
            ),
            models.CheckConstraint(
                condition=models.Q(rating__gte=1) & models.Q(rating__lte=5),
                name="review_rating_range",
            ),
            models.CheckConstraint(
                condition=~models.Q(author=models.F("subject")),
                name="review_no_self",
            ),
        ]
        indexes = [
            models.Index(fields=["subject", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.author_id} → {self.subject_id}: {self.rating}★"
