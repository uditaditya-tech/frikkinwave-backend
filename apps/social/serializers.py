"""
Serializers for the social app.

Follow edges are public — no reveal logic. Each list surfaces the *other* end of
the edge as a username plus when the edge was formed:
  - following list  → the user being followed
  - followers list  → the user doing the following
"""

from rest_framework import serializers

from apps.social.models import FeedEntry, Follow


class FollowingReadSerializer(serializers.ModelSerializer[Follow]):
    """An edge in the caller's *following* list — surfaces the followed user."""

    username = serializers.CharField(source="followed.username", read_only=True)

    class Meta:
        model = Follow
        fields = ["username", "created_at"]


class FollowerReadSerializer(serializers.ModelSerializer[Follow]):
    """An edge in a user's *followers* list — surfaces the follower."""

    username = serializers.CharField(source="follower.username", read_only=True)

    class Meta:
        model = Follow
        fields = ["username", "created_at"]


class FeedEntryReadSerializer(serializers.ModelSerializer[FeedEntry]):
    """
    One feed item — flattens the joined Activity. Renders entirely from the
    denormalized Activity fields plus the actor's username; no producer-app join.
    """

    actor_username = serializers.CharField(source="activity.actor.username", read_only=True)
    verb = serializers.CharField(source="activity.verb", read_only=True)
    summary = serializers.CharField(source="activity.summary", read_only=True)
    target_type = serializers.CharField(source="activity.target_type", read_only=True)
    target_id = serializers.UUIDField(source="activity.target_id", read_only=True)
    target_slug = serializers.CharField(source="activity.target_slug", read_only=True)

    class Meta:
        model = FeedEntry
        fields = [
            "id",
            "actor_username",
            "verb",
            "summary",
            "target_type",
            "target_id",
            "target_slug",
            "created_at",
        ]
