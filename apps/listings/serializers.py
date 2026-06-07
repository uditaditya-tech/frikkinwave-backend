"""
Serializers for the listings app.

  - Read  — response shape (includes the author's username).
  - Write — request body for create / update (PATCH-friendly: all optional).
"""

from typing import Any

from rest_framework import serializers

from apps.listings.models import Listing, ListingApplication


class ListingReadSerializer(serializers.ModelSerializer[Listing]):
    author_username = serializers.CharField(source="author.username", read_only=True)

    class Meta:
        model = Listing
        fields = [
            "id",
            "author_username",
            "listing_type",
            "title",
            "description",
            "city",
            "country",
            "is_paid",
            "pay_description",
            "deadline",
            "is_active",
            "created_at",
            "updated_at",
        ]


class ListingCreateSerializer(serializers.Serializer[Any]):
    listing_type = serializers.ChoiceField(choices=Listing.ListingType.choices)
    title = serializers.CharField(max_length=200)
    description = serializers.CharField()
    city = serializers.CharField(max_length=100)
    country = serializers.CharField(max_length=100)
    is_paid = serializers.BooleanField(required=False, default=False)
    pay_description = serializers.CharField(max_length=200, required=False, allow_blank=True)
    deadline = serializers.DateField(required=False, allow_null=True)


class ListingUpdateSerializer(serializers.Serializer[Any]):
    """
    PATCH input. All fields optional with no defaults — fields absent from the
    body stay out of validated_data, so the service distinguishes "not provided"
    from "set to empty".
    """

    listing_type = serializers.ChoiceField(choices=Listing.ListingType.choices, required=False)
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False)
    city = serializers.CharField(max_length=100, required=False)
    country = serializers.CharField(max_length=100, required=False)
    is_paid = serializers.BooleanField(required=False)
    pay_description = serializers.CharField(max_length=200, required=False, allow_blank=True)
    deadline = serializers.DateField(required=False, allow_null=True)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


class ListingApplicationReadSerializer(serializers.ModelSerializer[ListingApplication]):
    applicant_username = serializers.CharField(source="applicant.username", read_only=True)
    listing_id = serializers.UUIDField(source="listing.id", read_only=True)
    listing_title = serializers.CharField(source="listing.title", read_only=True)
    contact_email = serializers.SerializerMethodField()

    class Meta:
        model = ListingApplication
        fields = [
            "id",
            "listing_id",
            "listing_title",
            "applicant_username",
            "message",
            "status",
            "contact_email",
            "created_at",
            "updated_at",
        ]

    def get_contact_email(self, obj: ListingApplication) -> str | None:
        """
        Reveal the *other* party's email, but only once the application is
        accepted. Before acceptance — or to anyone not a party — this stays None.
        """
        if obj.status != ListingApplication.Status.ACCEPTED:
            return None
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        if viewer is None or not viewer.is_authenticated:
            return None
        if viewer.pk == obj.applicant_id:
            return obj.listing.author.email
        if viewer.pk == obj.listing.author_id:
            return obj.applicant.email
        return None


class ListingApplicationCreateSerializer(serializers.Serializer[Any]):
    message = serializers.CharField(required=False, allow_blank=True, default="")
