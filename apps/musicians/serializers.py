"""
Serializers for the musicians app.

Two serializer shapes per resource:
  - Read  (nested objects, used in responses)
  - Write (flat IDs, used for create / update input)
"""

from typing import Any

from rest_framework import serializers

from apps.musicians.models import Genre, Instrument, MusicianInstrument, MusicianProfile

# ---------------------------------------------------------------------------
# Nested read serializers (responses)
# ---------------------------------------------------------------------------


class InstrumentSerializer(serializers.ModelSerializer[Instrument]):
    class Meta:
        model = Instrument
        fields = ["id", "name", "slug"]


class GenreSerializer(serializers.ModelSerializer[Genre]):
    class Meta:
        model = Genre
        fields = ["id", "name", "slug"]


class MusicianInstrumentReadSerializer(serializers.ModelSerializer[MusicianInstrument]):
    instrument = InstrumentSerializer(read_only=True)

    class Meta:
        model = MusicianInstrument
        fields = ["instrument", "proficiency"]


class MusicianProfileReadSerializer(serializers.ModelSerializer[MusicianProfile]):
    # username is the public handle — keys the public profile route and contact
    # requests, so it must travel with every profile in the discovery feed.
    username = serializers.CharField(source="user.username", read_only=True)
    # source matches the related_name on MusicianInstrument.profile FK
    instruments = MusicianInstrumentReadSerializer(
        source="musician_instruments",
        many=True,
        read_only=True,
    )
    genres = GenreSerializer(many=True, read_only=True)

    class Meta:
        model = MusicianProfile
        fields = [
            "id",
            "user_id",
            "username",
            "bio",
            "city",
            "country",
            "is_available",
            "instruments",
            "genres",
            "created_at",
            "updated_at",
        ]


# ---------------------------------------------------------------------------
# Write serializers (create / update input)
# ---------------------------------------------------------------------------


class MusicianInstrumentWriteSerializer(serializers.Serializer[Any]):
    instrument = serializers.PrimaryKeyRelatedField(queryset=Instrument.objects.all())
    proficiency = serializers.ChoiceField(
        choices=MusicianInstrument.Proficiency.choices,
        default=MusicianInstrument.Proficiency.INTERMEDIATE,
    )


class MusicianProfileWriteSerializer(serializers.Serializer[Any]):
    """
    Used for both POST (create) and PATCH (update).

    All fields are optional with no defaults — fields absent from the request
    body are not present in validated_data, so the service layer can distinguish
    "not provided" from "explicitly set to empty".
    """

    bio = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    country = serializers.CharField(required=False, allow_blank=True, max_length=100)
    is_available = serializers.BooleanField(required=False)
    instruments = MusicianInstrumentWriteSerializer(many=True, required=False)
    genres = serializers.PrimaryKeyRelatedField(
        queryset=Genre.objects.all(),
        many=True,
        required=False,
    )

    def validate_instruments(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [item["instrument"].pk for item in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Duplicate instruments are not allowed.")
        return value
