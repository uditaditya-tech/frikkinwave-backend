"""
Serializers for the users app.

Kept separate from models — serializers are the HTTP boundary layer,
not part of the domain model.
"""

from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.users.models import User


class UserReadSerializer(serializers.ModelSerializer[User]):
    """Public identity shape for the authenticated user (`/api/auth/me/`)."""

    class Meta:
        model = User
        fields = ["id", "email", "username", "date_joined"]


class RegisterSerializer(serializers.Serializer[Any]):
    email = serializers.EmailField()
    username = serializers.SlugField(max_length=50)
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value.lower()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        try:
            validate_password(attrs["password"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        attrs.pop("password_confirm")
        return attrs
