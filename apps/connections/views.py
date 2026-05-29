"""
Views for the connections app.

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

from apps.connections.serializers import (
    ContactRequestCreateSerializer,
    ContactRequestReadSerializer,
)
from apps.connections.services import (
    DuplicateContactRequestError,
    NotPendingError,
    RecipientNotFoundError,
    SelfContactError,
    accept_contact_request,
    decline_contact_request,
    get_contact_request,
    list_contact_requests,
    send_contact_request,
)
from apps.users.models import User

logger = logging.getLogger(__name__)


class ContactRequestCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-created_at"


class ContactRequestListCreateView(APIView):
    """
    GET  /api/connections/requests/  — list own requests (?box=incoming|outgoing)
    POST /api/connections/requests/  — send a contact request
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        box = request.query_params.get("box", "incoming")
        if box not in ("incoming", "outgoing"):
            box = "incoming"
        queryset = list_contact_requests(user=cast(User, request.user), box=box)
        paginator = ContactRequestCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = ContactRequestReadSerializer(page, many=True, context={"request": request}).data
        return paginator.get_paginated_response(data)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = ContactRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            contact_request = send_contact_request(
                sender=cast(User, request.user),
                recipient_username=serializer.validated_data["recipient_username"],
                message=serializer.validated_data.get("message", ""),
            )
        except RecipientNotFoundError:
            return Response(
                {"detail": "No user found with that username."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SelfContactError:
            return Response(
                {"detail": "You cannot send a contact request to yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DuplicateContactRequestError:
            return Response(
                {"detail": "A contact request to this user already exists."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            ContactRequestReadSerializer(contact_request, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ContactRequestDetailView(APIView):
    """GET /api/connections/requests/<id>/ — retrieve a request you are party to."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, request_id: str, *args: Any, **kwargs: Any) -> Response:
        contact_request = get_contact_request(user=cast(User, request.user), request_id=request_id)
        if contact_request is None:
            return Response(
                {"detail": "Contact request not found."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(
            ContactRequestReadSerializer(contact_request, context={"request": request}).data
        )


class ContactRequestAcceptView(APIView):
    """POST /api/connections/requests/<id>/accept/ — recipient accepts."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, request_id: str, *args: Any, **kwargs: Any) -> Response:
        return _resolve_view(request, request_id, accept_contact_request)


class ContactRequestDeclineView(APIView):
    """POST /api/connections/requests/<id>/decline/ — recipient declines."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, request_id: str, *args: Any, **kwargs: Any) -> Response:
        return _resolve_view(request, request_id, decline_contact_request)


def _resolve_view(request: Request, request_id: str, action: Any) -> Response:
    """Shared accept/decline handling — both differ only in the service called."""
    try:
        contact_request = action(user=cast(User, request.user), request_id=request_id)
    except NotPendingError:
        return Response(
            {"detail": "This request has already been resolved."},
            status=status.HTTP_409_CONFLICT,
        )
    if contact_request is None:
        return Response({"detail": "Contact request not found."}, status=status.HTTP_404_NOT_FOUND)
    return Response(
        ContactRequestReadSerializer(contact_request, context={"request": request}).data
    )
