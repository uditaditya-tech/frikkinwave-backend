"""
Serializers for the social app.

Follow edges are public — no reveal logic. Each list surfaces the *other* end of
the edge as a username plus when the edge was formed:
  - following list  → the user being followed
  - followers list  → the user doing the following
"""

from rest_framework import serializers

from apps.social.models import Follow


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
