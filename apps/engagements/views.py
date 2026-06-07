"""
Views for the engagements app.

HTTP shell only: parse request → call service → return Response.
"""

import logging
from typing import Any, cast

from rest_framework import status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.engagements.serializers import (
    EngagementRequestCreateSerializer,
    EngagementRequestReadSerializer,
)
from apps.engagements.services import (
    MusicianNotFoundError,
    NotAcceptedError,
    NotPendingError,
    SelfEngagementError,
    accept_engagement_request,
    complete_engagement_request,
    decline_engagement_request,
    get_engagement_request,
    list_engagement_requests,
    send_engagement_request,
)
from apps.users.models import User

logger = logging.getLogger(__name__)


class EngagementCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-created_at"


class EngagementListCreateView(APIView):
    """
    GET  /api/engagements/  — list own requests (?box=incoming|outgoing)
    POST /api/engagements/  — send a hire request
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        box = request.query_params.get("box", "incoming")
        if box not in ("incoming", "outgoing"):
            box = "incoming"
        queryset = list_engagement_requests(user=cast(User, request.user), box=box)
        paginator = EngagementCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = EngagementRequestReadSerializer(page, many=True, context={"request": request}).data
        return paginator.get_paginated_response(data)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = EngagementRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            engagement = send_engagement_request(
                requester=cast(User, request.user),
                musician_username=serializer.validated_data["musician_username"],
                message=serializer.validated_data.get("message", ""),
                proposed_date=serializer.validated_data.get("proposed_date"),
                rate_offer=serializer.validated_data.get("rate_offer", ""),
            )
        except MusicianNotFoundError:
            return Response(
                {"detail": "No user found with that username."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SelfEngagementError:
            return Response(
                {"detail": "You cannot hire yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            EngagementRequestReadSerializer(engagement, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class EngagementDetailView(APIView):
    """GET /api/engagements/<id>/ — retrieve a request you are party to."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, engagement_id: str, *args: Any, **kwargs: Any) -> Response:
        engagement = get_engagement_request(
            user=cast(User, request.user), engagement_id=engagement_id
        )
        if engagement is None:
            return Response(
                {"detail": "Engagement request not found."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(
            EngagementRequestReadSerializer(engagement, context={"request": request}).data
        )


class EngagementAcceptView(APIView):
    """POST /api/engagements/<id>/accept/ — hired musician accepts."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, engagement_id: str, *args: Any, **kwargs: Any) -> Response:
        return _resolve_view(request, engagement_id, accept_engagement_request)


class EngagementDeclineView(APIView):
    """POST /api/engagements/<id>/decline/ — hired musician declines."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, engagement_id: str, *args: Any, **kwargs: Any) -> Response:
        return _resolve_view(request, engagement_id, decline_engagement_request)


class EngagementCompleteView(APIView):
    """POST /api/engagements/<id>/complete/ — either party marks an accepted request done."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, engagement_id: str, *args: Any, **kwargs: Any) -> Response:
        try:
            engagement = complete_engagement_request(
                user=cast(User, request.user), engagement_id=engagement_id
            )
        except NotAcceptedError:
            return Response(
                {"detail": "Only an accepted request can be completed."},
                status=status.HTTP_409_CONFLICT,
            )
        if engagement is None:
            return Response(
                {"detail": "Engagement request not found."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(
            EngagementRequestReadSerializer(engagement, context={"request": request}).data
        )


def _resolve_view(request: Request, engagement_id: str, action: Any) -> Response:
    """Shared accept/decline handling — both differ only in the service called."""
    try:
        engagement = action(user=cast(User, request.user), engagement_id=engagement_id)
    except NotPendingError:
        return Response(
            {"detail": "This request has already been resolved."},
            status=status.HTTP_409_CONFLICT,
        )
    if engagement is None:
        return Response(
            {"detail": "Engagement request not found."}, status=status.HTTP_404_NOT_FOUND
        )
    return Response(EngagementRequestReadSerializer(engagement, context={"request": request}).data)
