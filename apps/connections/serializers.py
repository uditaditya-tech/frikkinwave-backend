"""
Serializers for the connections app.

  - Read  — response shape, with a conditional contact_email reveal.
  - Create — request body for sending a contact request.
"""

from typing import Any

from rest_framework import serializers

from apps.connections.models import ContactRequest


class ContactRequestReadSerializer(serializers.ModelSerializer[ContactRequest]):
    sender_username = serializers.CharField(source="sender.username", read_only=True)
    recipient_username = serializers.CharField(source="recipient.username", read_only=True)
    contact_email = serializers.SerializerMethodField()

    class Meta:
        model = ContactRequest
        fields = [
            "id",
            "sender_username",
            "recipient_username",
            "message",
            "status",
            "contact_email",
            "created_at",
            "updated_at",
        ]

    def get_contact_email(self, obj: ContactRequest) -> str | None:
        """
        Reveal the *other* party's email, but only once the request is accepted.

        Before acceptance — or to anyone who is not a party — this stays None.
        """
        if obj.status != ContactRequest.Status.ACCEPTED:
            return None
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        if viewer is None or not viewer.is_authenticated:
            return None
        if viewer.pk == obj.sender_id:
            return obj.recipient.email
        if viewer.pk == obj.recipient_id:
            return obj.sender.email
        return None


class ContactRequestCreateSerializer(serializers.Serializer[Any]):
    recipient_username = serializers.CharField(max_length=50)
    message = serializers.CharField(required=False, allow_blank=True, default="")
