"""
Views for the bands app.

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

from apps.bands.serializers import (
    BandCreateSerializer,
    BandInviteSerializer,
    BandMembershipReadSerializer,
    BandReadSerializer,
    BandUpdateSerializer,
)
from apps.bands.services import (
    BandNotFoundError,
    DuplicateMembershipError,
    MemberNotFoundError,
    NotBandOwnerError,
    NotPendingError,
    SelfInviteError,
    accept_membership,
    create_band,
    deactivate_band,
    decline_membership,
    get_band,
    get_membership,
    invite_member,
    list_band_members,
    list_bands,
    list_memberships,
    update_band,
)
from apps.users.models import User

logger = logging.getLogger(__name__)


class BandCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-created_at"


class BandListCreateView(APIView):
    """
    GET  /api/bands/  — browse active bands (?city=&country=)
    POST /api/bands/  — create a band (auth)
    """

    def get_authenticators(self) -> list[Any]:
        return [JWTAuthentication()]

    def get_permissions(self) -> list[Any]:
        if self.request.method == "POST":
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = list_bands(
            city=request.query_params.get("city"),
            country=request.query_params.get("country"),
        )
        paginator = BandCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = BandReadSerializer(page, many=True).data
        return paginator.get_paginated_response(data)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = BandCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        band = create_band(owner=cast(User, request.user), **serializer.validated_data)
        return Response(BandReadSerializer(band).data, status=status.HTTP_201_CREATED)


class BandDetailView(APIView):
    """
    GET    /api/bands/<slug>/  — public band page (with accepted roster)
    PATCH  /api/bands/<slug>/  — update (owner only)
    DELETE /api/bands/<slug>/  — soft-delete (owner only)
    """

    def get_authenticators(self) -> list[Any]:
        return [JWTAuthentication()]

    def get_permissions(self) -> list[Any]:
        if self.request.method in ("PATCH", "DELETE"):
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request: Request, slug: str, *args: Any, **kwargs: Any) -> Response:
        band = get_band(slug=slug)
        if band is None:
            return Response({"detail": "Band not found."}, status=status.HTTP_404_NOT_FOUND)
        band.accepted_members = list(list_band_members(band=band))  # type: ignore[attr-defined]
        return Response(BandReadSerializer(band).data)

    def patch(self, request: Request, slug: str, *args: Any, **kwargs: Any) -> Response:
        serializer = BandUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            band = update_band(
                owner=cast(User, request.user), slug=slug, **serializer.validated_data
            )
        except BandNotFoundError:
            return Response({"detail": "Band not found."}, status=status.HTTP_404_NOT_FOUND)
        except NotBandOwnerError:
            return Response(
                {"detail": "You can only edit bands you own."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(BandReadSerializer(band).data)

    def delete(self, request: Request, slug: str, *args: Any, **kwargs: Any) -> Response:
        try:
            deactivate_band(owner=cast(User, request.user), slug=slug)
        except BandNotFoundError:
            return Response({"detail": "Band not found."}, status=status.HTTP_404_NOT_FOUND)
        except NotBandOwnerError:
            return Response(
                {"detail": "You can only delete bands you own."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class BandInviteView(APIView):
    """POST /api/bands/<slug>/invite/ — owner invites a user to the band."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, slug: str, *args: Any, **kwargs: Any) -> Response:
        serializer = BandInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            membership = invite_member(
                owner=cast(User, request.user),
                slug=slug,
                member_username=serializer.validated_data["member_username"],
                role=serializer.validated_data.get("role", ""),
            )
        except BandNotFoundError:
            return Response({"detail": "Band not found."}, status=status.HTTP_404_NOT_FOUND)
        except NotBandOwnerError:
            return Response(
                {"detail": "Only the band owner can invite members."},
                status=status.HTTP_403_FORBIDDEN,
            )
        except MemberNotFoundError:
            return Response(
                {"detail": "No user found with that username."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SelfInviteError:
            return Response(
                {"detail": "You already own this band."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DuplicateMembershipError:
            return Response(
                {"detail": "That user already has a membership for this band."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            BandMembershipReadSerializer(membership, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class BandMembershipListView(APIView):
    """GET /api/bands/memberships/ — the caller's own memberships / invites."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = list_memberships(user=cast(User, request.user))
        paginator = BandCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = BandMembershipReadSerializer(page, many=True, context={"request": request}).data
        return paginator.get_paginated_response(data)


class BandMembershipDetailView(APIView):
    """GET /api/bands/memberships/<id>/ — retrieve a membership you are party to."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, membership_id: str, *args: Any, **kwargs: Any) -> Response:
        membership = get_membership(user=cast(User, request.user), membership_id=membership_id)
        if membership is None:
            return Response({"detail": "Membership not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BandMembershipReadSerializer(membership, context={"request": request}).data)


class BandMembershipAcceptView(APIView):
    """POST /api/bands/memberships/<id>/accept/ — invited member accepts."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, membership_id: str, *args: Any, **kwargs: Any) -> Response:
        return _resolve_membership_view(request, membership_id, accept_membership)


class BandMembershipDeclineView(APIView):
    """POST /api/bands/memberships/<id>/decline/ — invited member declines."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, membership_id: str, *args: Any, **kwargs: Any) -> Response:
        return _resolve_membership_view(request, membership_id, decline_membership)


def _resolve_membership_view(request: Request, membership_id: str, action: Any) -> Response:
    """Shared accept/decline handling — both differ only in the service called."""
    try:
        membership = action(user=cast(User, request.user), membership_id=membership_id)
    except NotPendingError:
        return Response(
            {"detail": "This invitation has already been resolved."},
            status=status.HTTP_409_CONFLICT,
        )
    if membership is None:
        return Response({"detail": "Membership not found."}, status=status.HTTP_404_NOT_FOUND)
    return Response(BandMembershipReadSerializer(membership, context={"request": request}).data)
