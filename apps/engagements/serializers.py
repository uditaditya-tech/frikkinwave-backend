"""
Serializers for the engagements app.

  - Read  — response shape, with a conditional contact_email reveal.
  - Create — request body for sending a hire request.
"""

from typing import Any

from rest_framework import serializers

from apps.engagements.models import EngagementRequest


class EngagementRequestReadSerializer(serializers.ModelSerializer[EngagementRequest]):
    requester_username = serializers.CharField(source="requester.username", read_only=True)
    musician_username = serializers.CharField(source="musician.username", read_only=True)
    contact_email = serializers.SerializerMethodField()

    class Meta:
        model = EngagementRequest
        fields = [
            "id",
            "requester_username",
            "musician_username",
            "message",
            "proposed_date",
            "rate_offer",
            "status",
            "contact_email",
            "created_at",
            "updated_at",
        ]

    def get_contact_email(self, obj: EngagementRequest) -> str | None:
        """
        Reveal the *other* party's email once the request is accepted (or
        completed). Before then — or to anyone not a party — this stays None.
        """
        if obj.status not in (
            EngagementRequest.Status.ACCEPTED,
            EngagementRequest.Status.COMPLETED,
        ):
            return None
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        if viewer is None or not viewer.is_authenticated:
            return None
        if viewer.pk == obj.requester_id:
            return obj.musician.email
        if viewer.pk == obj.musician_id:
            return obj.requester.email
        return None


class EngagementRequestCreateSerializer(serializers.Serializer[Any]):
    musician_username = serializers.CharField(max_length=50)
    message = serializers.CharField(required=False, allow_blank=True, default="")
    proposed_date = serializers.DateField(required=False, allow_null=True)
    rate_offer = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
