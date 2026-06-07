"""
Views for the venues app.

HTTP shell only: parse request → call service → return Response.
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

from apps.users.models import User
from apps.venues.serializers import (
    VenueCreateSerializer,
    VenueReadSerializer,
    VenueUpdateSerializer,
)
from apps.venues.services import (
    NotVenueOwnerError,
    VenueNotFoundError,
    create_venue,
    deactivate_venue,
    get_venue,
    list_venues,
    update_venue,
)

logger = logging.getLogger(__name__)


class VenueCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-created_at"


class VenueListCreateView(APIView):
    """
    GET  /api/venues/  — browse active venues (?city=&country=)
    POST /api/venues/  — create a venue (auth)
    """

    def get_authenticators(self) -> list[Any]:
        return [JWTAuthentication()]

    def get_permissions(self) -> list[Any]:
        if self.request.method == "POST":
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = list_venues(
            city=request.query_params.get("city"),
            country=request.query_params.get("country"),
        )
        paginator = VenueCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = VenueReadSerializer(page, many=True).data
        return paginator.get_paginated_response(data)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = VenueCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        venue = create_venue(owner=cast(User, request.user), **serializer.validated_data)
        return Response(VenueReadSerializer(venue).data, status=status.HTTP_201_CREATED)


class VenueDetailView(APIView):
    """
    GET    /api/venues/<slug>/  — public venue page
    PATCH  /api/venues/<slug>/  — update (owner only)
    DELETE /api/venues/<slug>/  — soft-delete (owner only)
    """

    def get_authenticators(self) -> list[Any]:
        return [JWTAuthentication()]

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("PATCH", "DELETE"):
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request: Request, slug: str, *args: Any, **kwargs: Any) -> Response:
        venue = get_venue(slug=slug)
        if venue is None:
            return Response({"detail": "Venue not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(VenueReadSerializer(venue).data)

    def patch(self, request: Request, slug: str, *args: Any, **kwargs: Any) -> Response:
        serializer = VenueUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            venue = update_venue(
                owner=cast(User, request.user), slug=slug, **serializer.validated_data
            )
        except VenueNotFoundError:
            return Response({"detail": "Venue not found."}, status=status.HTTP_404_NOT_FOUND)
        except NotVenueOwnerError:
            return Response(
                {"detail": "You can only edit venues you own."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(VenueReadSerializer(venue).data)

    def delete(self, request: Request, slug: str, *args: Any, **kwargs: Any) -> Response:
        try:
            deactivate_venue(owner=cast(User, request.user), slug=slug)
        except VenueNotFoundError:
            return Response({"detail": "Venue not found."}, status=status.HTTP_404_NOT_FOUND)
        except NotVenueOwnerError:
            return Response(
                {"detail": "You can only delete venues you own."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
