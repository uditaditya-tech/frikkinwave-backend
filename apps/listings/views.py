"""
Views for the listings app.

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

from apps.listings.serializers import (
    ListingCreateSerializer,
    ListingReadSerializer,
    ListingUpdateSerializer,
)
from apps.listings.services import (
    ListingNotFoundError,
    NotListingAuthorError,
    create_listing,
    deactivate_listing,
    get_listing,
    list_listings,
    update_listing,
)
from apps.users.models import User

logger = logging.getLogger(__name__)


class ListingCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-created_at"


class ListingListCreateView(APIView):
    """
    GET  /api/listings/  — browse active listings (?type=&city=&country=)
    POST /api/listings/  — post a listing (auth)
    """

    def get_authenticators(self) -> list[Any]:
        return [JWTAuthentication()]

    def get_permissions(self) -> list[Any]:
        # Browsing is public; posting requires auth.
        if self.request.method == "POST":
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = list_listings(
            listing_type=request.query_params.get("type"),
            city=request.query_params.get("city"),
            country=request.query_params.get("country"),
        )
        paginator = ListingCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = ListingReadSerializer(page, many=True).data
        return paginator.get_paginated_response(data)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = ListingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        listing = create_listing(author=cast(User, request.user), **serializer.validated_data)
        return Response(
            ListingReadSerializer(listing).data,
            status=status.HTTP_201_CREATED,
        )


class ListingDetailView(APIView):
    """
    GET    /api/listings/<id>/  — retrieve an active listing (public)
    PATCH  /api/listings/<id>/  — update (author only)
    DELETE /api/listings/<id>/  — soft-delete (author only)
    """

    def get_authenticators(self) -> list[Any]:
        return [JWTAuthentication()]

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("PATCH", "DELETE"):
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request: Request, listing_id: str, *args: Any, **kwargs: Any) -> Response:
        listing = get_listing(listing_id=listing_id)
        if listing is None:
            return Response({"detail": "Listing not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ListingReadSerializer(listing).data)

    def patch(self, request: Request, listing_id: str, *args: Any, **kwargs: Any) -> Response:
        serializer = ListingUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            listing = update_listing(
                author=cast(User, request.user),
                listing_id=listing_id,
                **serializer.validated_data,
            )
        except ListingNotFoundError:
            return Response({"detail": "Listing not found."}, status=status.HTTP_404_NOT_FOUND)
        except NotListingAuthorError:
            return Response(
                {"detail": "You can only edit your own listings."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(ListingReadSerializer(listing).data)

    def delete(self, request: Request, listing_id: str, *args: Any, **kwargs: Any) -> Response:
        try:
            deactivate_listing(author=cast(User, request.user), listing_id=listing_id)
        except ListingNotFoundError:
            return Response({"detail": "Listing not found."}, status=status.HTTP_404_NOT_FOUND)
        except NotListingAuthorError:
            return Response(
                {"detail": "You can only delete your own listings."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
