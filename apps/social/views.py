"""
Views for the social app.

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

from apps.social.serializers import FollowerReadSerializer, FollowingReadSerializer
from apps.social.services import (
    SelfFollowError,
    UserNotFoundError,
    follow_user,
    get_user_or_raise,
    list_followers,
    list_following,
    unfollow_user,
)
from apps.users.models import User

logger = logging.getLogger(__name__)


def _user_not_found() -> Response:
    return Response(
        {"detail": "No user found with that username."}, status=status.HTTP_404_NOT_FOUND
    )


class SocialCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-created_at"


class FollowView(APIView):
    """
    POST   /api/social/follow/<username>/ — follow a user (idempotent)
    DELETE /api/social/follow/<username>/ — unfollow
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, username: str, *args: Any, **kwargs: Any) -> Response:
        try:
            follow, created = follow_user(follower=cast(User, request.user), username=username)
        except UserNotFoundError:
            return _user_not_found()
        except SelfFollowError:
            return Response(
                {"detail": "You cannot follow yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(FollowingReadSerializer(follow).data, status=code)

    def delete(self, request: Request, username: str, *args: Any, **kwargs: Any) -> Response:
        try:
            unfollow_user(follower=cast(User, request.user), username=username)
        except UserNotFoundError:
            return _user_not_found()
        # Idempotent: deleting a non-existent edge is still a 204.
        return Response(status=status.HTTP_204_NO_CONTENT)


class FollowingListView(APIView):
    """GET /api/social/following/ — users the caller follows."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = list_following(user=cast(User, request.user))
        paginator = SocialCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(FollowingReadSerializer(page, many=True).data)


class FollowersListView(APIView):
    """GET /api/social/followers/ — users who follow the caller."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = list_followers(user=cast(User, request.user))
        paginator = SocialCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(FollowerReadSerializer(page, many=True).data)


class PublicFollowingView(APIView):
    """GET /api/social/<username>/following/ — public list of who a user follows."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, username: str, *args: Any, **kwargs: Any) -> Response:
        try:
            target = get_user_or_raise(username=username)
        except UserNotFoundError:
            return _user_not_found()
        queryset = list_following(user=target)
        paginator = SocialCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(FollowingReadSerializer(page, many=True).data)


class PublicFollowersView(APIView):
    """GET /api/social/<username>/followers/ — public list of a user's followers."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, username: str, *args: Any, **kwargs: Any) -> Response:
        try:
            target = get_user_or_raise(username=username)
        except UserNotFoundError:
            return _user_not_found()
        queryset = list_followers(user=target)
        paginator = SocialCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(FollowerReadSerializer(page, many=True).data)
