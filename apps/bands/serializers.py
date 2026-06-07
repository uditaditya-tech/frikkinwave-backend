"""
Serializers for the bands app.

  - Band Read / Create / Update.
  - Membership Read (with reveal-on-accept contact email) / Invite.
"""

from typing import Any

from rest_framework import serializers

from apps.bands.models import Band, BandMembership


class BandMemberSerializer(serializers.ModelSerializer[BandMembership]):
    """A roster entry on the public band page (accepted members only)."""

    member_username = serializers.CharField(source="member.username", read_only=True)

    class Meta:
        model = BandMembership
        fields = ["member_username", "role"]


class BandReadSerializer(serializers.ModelSerializer[Band]):
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    members = serializers.SerializerMethodField()

    class Meta:
        model = Band
        fields = [
            "id",
            "owner_username",
            "name",
            "slug",
            "bio",
            "city",
            "country",
            "is_active",
            "members",
            "created_at",
            "updated_at",
        ]

    def get_members(self, obj: Band) -> Any:
        # Populated from a prefetched/queried accepted roster placed on the
        # instance by the view; absent in list views (kept light).
        roster = getattr(obj, "accepted_members", None)
        if roster is None:
            return []
        return BandMemberSerializer(roster, many=True).data


class BandCreateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=200)
    bio = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True)


class BandUpdateSerializer(serializers.Serializer[Any]):
    """PATCH input — all optional, no defaults, so absent fields stay out of validated_data."""

    name = serializers.CharField(max_length=200, required=False)
    bio = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True)


class BandMembershipReadSerializer(serializers.ModelSerializer[BandMembership]):
    band_slug = serializers.CharField(source="band.slug", read_only=True)
    band_name = serializers.CharField(source="band.name", read_only=True)
    member_username = serializers.CharField(source="member.username", read_only=True)
    contact_email = serializers.SerializerMethodField()

    class Meta:
        model = BandMembership
        fields = [
            "id",
            "band_slug",
            "band_name",
            "member_username",
            "role",
            "status",
            "contact_email",
            "created_at",
            "updated_at",
        ]

    def get_contact_email(self, obj: BandMembership) -> str | None:
        """
        Reveal the *other* party's email once the membership is accepted.
        Before acceptance — or to anyone not a party — this stays None.
        """
        if obj.status != BandMembership.Status.ACCEPTED:
            return None
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        if viewer is None or not viewer.is_authenticated:
            return None
        if viewer.pk == obj.member_id:
            return obj.band.owner.email
        if viewer.pk == obj.band.owner_id:
            return obj.member.email
        return None


class BandInviteSerializer(serializers.Serializer[Any]):
    member_username = serializers.CharField(max_length=50)
    role = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
