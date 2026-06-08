"""
Serializers for the reviews app.

  - ReviewCreate — write input (subject username + gating engagement + rating).
  - ReviewRead — public review row (surfaces the author's username).
"""

from typing import Any

from rest_framework import serializers

from apps.reviews.models import Review


class ReviewCreateSerializer(serializers.Serializer[Any]):
    subject_username = serializers.CharField(max_length=50)
    engagement_id = serializers.UUIDField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class ReviewReadSerializer(serializers.ModelSerializer[Review]):
    author_username = serializers.CharField(source="author.username", read_only=True)
    subject_username = serializers.CharField(source="subject.username", read_only=True)

    class Meta:
        model = Review
        fields = [
            "id",
            "author_username",
            "subject_username",
            "rating",
            "comment",
            "context_type",
            "context_id",
            "created_at",
        ]
