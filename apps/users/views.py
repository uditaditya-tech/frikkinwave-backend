"""
Views for the users app.

Views are the HTTP shell only: parse request → call service → return Response.
No business logic here.
"""

import logging
from typing import Any

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.serializers import RegisterSerializer
from apps.users.services import register_user

logger = logging.getLogger(__name__)


class RegisterView(APIView):
    """
    POST /api/auth/register/

    Create a new user account and return a JWT token pair.
    Explicitly sets authentication_classes to prevent DRF's 401→403 silent
    demotion on AllowAny views.
    """

    authentication_classes = [JWTAuthentication]  # noqa: RUF012
    permission_classes = [AllowAny]  # noqa: RUF012

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data: dict[str, Any] = serializer.validated_data
        user = register_user(
            email=data["email"],
            username=data["username"],
            password=data["password"],
        )
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


class LogoutView(APIView):
    """
    POST /api/auth/logout/

    Blacklist the provided refresh token, invalidating the session.
    Body: {"refresh": "<refresh_token>"}
    """

    authentication_classes = [JWTAuthentication]  # noqa: RUF012
    permission_classes = [IsAuthenticated]  # noqa: RUF012

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        refresh_token: Any = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"detail": "refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return Response(
                {"detail": "Token is invalid or already blacklisted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        logger.info("user_logged_out", extra={"user_id": str(request.user.pk)})
        return Response(status=status.HTTP_204_NO_CONTENT)
