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
from apps.musicians.serializers import (
    GenreSerializer,
    InstrumentSerializer,
    MusicianProfileReadSerializer,
    MusicianProfileWriteSerializer,
    ProfileSearchResultSerializer,
)
from apps.musicians.services import (
    ProfileAlreadyExistsError,
    coach_profile,
    create_profile,
    get_compatibility_blurb,
    get_public_profile,
    list_genres,
    list_instruments,
    list_profiles,
    search_profiles,
    update_profile,
)
from apps.users.models import User

logger = logging.getLogger(__name__)

SEARCH_QUERY_MAX_LEN = 500
SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 50


class InstrumentListView(APIView):
    """
    GET /api/musicians/instruments/

    Public lookup table — the full instrument catalogue for profile-editor pickers.
    Unauthenticated access allowed.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(InstrumentSerializer(list_instruments(), many=True).data)


class GenreListView(APIView):
    """
    GET /api/musicians/genres/

    Public lookup table — the full genre catalogue for profile-editor pickers.
    Unauthenticated access allowed.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(GenreSerializer(list_genres(), many=True).data)


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
    ?genre=<slug> ?available=true ?open_to_session=true. Cursor-paginated.
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
        if "open_to_session" in params:
            filters["open_to_session"] = params.get("open_to_session", "").lower() == "true"

        queryset = list_profiles(filters=filters)

        paginator = ProfileCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = MusicianProfileReadSerializer(page, many=True).data
        return paginator.get_paginated_response(data)


class ProfileSearchView(APIView):
    """
    GET /api/musicians/search/?q=<text>&limit=N&available=true

    Semantic search over profile embeddings (cosine kNN). Public.
    `q` is required; `limit` defaults to 20 (max 50); `available=true` restricts
    to profiles open to jamming. Results are ranked most-similar first, each with
    a `similarity` score. Returns an empty list if AI search is unavailable.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        query = request.query_params.get("q", "").strip()
        if not query:
            return Response(
                {"detail": "Query parameter 'q' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(query) > SEARCH_QUERY_MAX_LEN:
            return Response(
                {"detail": f"Query must be at most {SEARCH_QUERY_MAX_LEN} characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        limit = self._parse_limit(request.query_params.get("limit"))
        available_only = request.query_params.get("available", "").lower() == "true"

        results = search_profiles(query=query, limit=limit, available_only=available_only)
        data = ProfileSearchResultSerializer(results, many=True).data
        return Response({"query": query, "results": data})

    @staticmethod
    def _parse_limit(raw: str | None) -> int:
        if raw is None:
            return SEARCH_DEFAULT_LIMIT
        try:
            limit = int(raw)
        except ValueError:
            return SEARCH_DEFAULT_LIMIT
        return max(1, min(limit, SEARCH_MAX_LIMIT))


class ProfilePublicView(APIView):
    """
    GET /api/musicians/profiles/<username>/

    Public single-profile view. Unauthenticated access allowed.
    Returns 404 if no profile exists for that username.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, username: str, *args: Any, **kwargs: Any) -> Response:
        profile = get_public_profile(username=username)
        if profile is None:
            return Response({"detail": "Profile not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(MusicianProfileReadSerializer(profile).data)


class CompatibilityView(APIView):
    """
    GET /api/musicians/compatibility/<username>/

    Returns a cached "why you might click" blurb between the authenticated user's
    profile and <username>'s profile (gpt-4o-mini, generated once per pair).
    400 if the viewer has no profile or targets themselves; 404 if the target is
    unknown; 503 if AI is unavailable.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, username: str, *args: Any, **kwargs: Any) -> Response:
        viewer = (
            MusicianProfile.objects.prefetch_related("musician_instruments__instrument", "genres")
            .filter(user=cast(User, request.user))
            .first()
        )
        if viewer is None:
            return Response(
                {"detail": "Create a profile first to see compatibility."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        other = get_public_profile(username=username)
        if other is None:
            return Response({"detail": "Profile not found."}, status=status.HTTP_404_NOT_FOUND)
        if viewer.pk == other.pk:
            return Response(
                {"detail": "Cannot compare a profile with itself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        blurb = get_compatibility_blurb(viewer_profile=viewer, other_profile=other)
        if blurb is None:
            return Response(
                {"detail": "Compatibility is unavailable right now."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"with": username, "blurb": blurb})


class ProfileCoachView(APIView):
    """
    GET /api/musicians/profile/coach/

    Evaluate the authenticated user's own profile: returns a completeness score
    (0-100), structured per-field suggestions, and an LLM `tip` (null if AI is
    unavailable). 400 if the user has no profile yet.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        profile = (
            MusicianProfile.objects.prefetch_related("musician_instruments__instrument", "genres")
            .filter(user=cast(User, request.user))
            .first()
        )
        if profile is None:
            return Response(
                {"detail": "Create a profile first to get coaching."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(coach_profile(profile=profile))


class ProfileMeView(APIView):
    """
    GET  /api/musicians/profile/me/  — retrieve own profile
    PATCH /api/musicians/profile/me/ — partial update own profile
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def _get_profile(self, request: Request) -> MusicianProfile | None:
        return (
            MusicianProfile.objects.select_related("user")
            .prefetch_related(
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
