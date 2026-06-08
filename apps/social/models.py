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


class Activity(models.Model):
    """
    Canonical event-log row: one per thing a user did (the source of truth).

    Fully denormalized — the `summary` + `target_*` fields are supplied by the
    producing app, so rendering the feed never joins back into another app's
    tables. No GenericForeignKey: `target_type` is a free string, keeping
    `social` ignorant of every other app's schema.
    """

    class Verb(models.TextChoices):
        POSTED_LISTING = "posted_listing", "Posted a listing"
        CREATED_BAND = "created_band", "Created a band"

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    verb = models.CharField(max_length=32, choices=Verb.choices)
    summary = models.CharField(max_length=300)
    target_type = models.CharField(max_length=50)
    target_id = models.UUIDField(null=True, blank=True)
    target_slug = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["actor", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.actor_id} {self.verb} ({self.summary})"


class FeedEntry(models.Model):
    """
    A per-recipient inbox row (fan-out-on-write): one copy of an Activity placed
    in each follower's feed (plus the actor's own). The feed read touches only
    this table. `created_at` is denormalized from the Activity so cursor
    pagination orders correctly even for follow-time backfilled entries.
    """

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feed_entries",
    )
    activity = models.ForeignKey(
        Activity,
        on_delete=models.CASCADE,
        related_name="feed_entries",
    )
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "activity"],
                name="unique_feed_entry",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.owner_id} ← {self.activity_id}"
