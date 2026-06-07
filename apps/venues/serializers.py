"""
Serializers for the venues app.

  - Read   — response shape (includes the owner's username).
  - Create — request body for create.
  - Update — PATCH input (all optional, no defaults).
"""

from typing import Any

from rest_framework import serializers

from apps.venues.models import Venue


class VenueReadSerializer(serializers.ModelSerializer[Venue]):
    owner_username = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = Venue
        fields = [
            "id",
            "owner_username",
            "name",
            "slug",
            "description",
            "address",
            "city",
            "country",
            "capacity",
            "website",
            "is_active",
            "created_at",
            "updated_at",
        ]


class VenueCreateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(max_length=300, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True)
    capacity = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    website = serializers.URLField(max_length=500, required=False, allow_blank=True)


class VenueUpdateSerializer(serializers.Serializer[Any]):
    """PATCH input — all optional, no defaults, so absent fields stay out of validated_data."""

    name = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(max_length=300, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True)
    capacity = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    website = serializers.URLField(max_length=500, required=False, allow_blank=True)
