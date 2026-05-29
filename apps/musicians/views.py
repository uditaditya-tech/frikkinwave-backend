"""
Views for the musicians app.

Views are the HTTP shell only: parse request → call service → return Response.
No business logic here.
"""

import logging
from typing import Any, cast

from rest_framework import status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.musicians.models import MusicianProfile
from apps.musicians.serializers import MusicianProfileReadSerializer, MusicianProfileWriteSerializer
from apps.musicians.services import (
    ProfileAlreadyExistsError,
    create_profile,
    list_profiles,
    update_profile,
)
from apps.users.models import User

logger = logging.getLogger(__name__)


class ProfileCursorPagination(CursorPagination):
    """Cursor pagination for the public profile feed — no COUNT(*), stable under inserts."""

    page_size = 20
    ordering = "-created_at"


class ProfileCreateView(APIView):
    """
    POST /api/musicians/profile/

    Create the authenticated user's musician profile.
    Returns 409 if a profile already exists.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = MusicianProfileWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            profile = create_profile(user=cast(User, request.user), data=serializer.validated_data)
        except ProfileAlreadyExistsError:
            return Response(
                {"detail": "A profile already exists for this user."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            MusicianProfileReadSerializer(profile).data,
            status=status.HTTP_201_CREATED,
        )


class ProfileListView(APIView):
    """
    GET /api/musicians/profiles/

    Public discovery feed. Unauthenticated access allowed.
    Optional, combinable query params: ?city= ?country= ?instrument=<slug>
    ?genre=<slug> ?available=true. Cursor-paginated.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        params = request.query_params
        filters: dict[str, Any] = {}
        for key in ("city", "country", "instrument", "genre"):
            if value := params.get(key):
                filters[key] = value
        if "available" in params:
            filters["available"] = params.get("available", "").lower() == "true"

        queryset = list_profiles(filters=filters)

        paginator = ProfileCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = MusicianProfileReadSerializer(page, many=True).data
        return paginator.get_paginated_response(data)


class ProfileMeView(APIView):
    """
    GET  /api/musicians/profile/me/  — retrieve own profile
    PATCH /api/musicians/profile/me/ — partial update own profile
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def _get_profile(self, request: Request) -> MusicianProfile | None:
        return (
            MusicianProfile.objects.prefetch_related(
                "musician_instruments__instrument",
                "genres",
            )
            .filter(user=cast(User, request.user))
            .first()
        )

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        profile = self._get_profile(request)
        if profile is None:
            return Response({"detail": "Profile not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(MusicianProfileReadSerializer(profile).data)

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        profile = self._get_profile(request)
        if profile is None:
            return Response({"detail": "Profile not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = MusicianProfileWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        profile = update_profile(profile=profile, data=serializer.validated_data)
        return Response(MusicianProfileReadSerializer(profile).data)
