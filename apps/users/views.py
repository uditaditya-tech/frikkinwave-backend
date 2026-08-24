"""
Views for the users app.

Views are the HTTP shell only: parse request → call service → return Response.
No business logic here.
"""

import logging
from typing import TYPE_CHECKING, Any, cast

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.schema.serializers import DetailSerializer
from apps.users.serializers import RegisterSerializer, TokenPairSerializer, UserReadSerializer
from apps.users.services import register_user

if TYPE_CHECKING:
    # Type-only: `request.user` is supplied by the auth middleware, so the
    # model never needs importing at runtime (see tests/test_architecture.py).
    from apps.users.models import User


logger = logging.getLogger(__name__)


class MeView(APIView):
    """
    GET /api/auth/me/

    Return the authenticated user's identity (id, email, username).
    Lets the frontend resolve the current user without decoding the JWT.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserReadSerializer})
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(UserReadSerializer(cast("User", request.user)).data)


class RegisterView(APIView):
    """
    POST /api/auth/register/

    Create a new user account and return a JWT token pair.
    Explicitly sets authentication_classes to prevent DRF's 401→403 silent
    demotion on AllowAny views.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"

    @extend_schema(
        request=RegisterSerializer,
        responses={
            201: TokenPairSerializer,
            400: DetailSerializer,
            429: DetailSerializer,
        },
    )
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

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None, 400: DetailSerializer})
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


# ---------------------------------------------------------------------------
# Throttled token views
#
# simplejwt ships these views ready to route, and `config/urls.py` used to point
# straight at them — which left the single most attackable endpoint on the
# service, the one that turns guesses into credentials, with no rate limit.
#
# Subclassing is the only way to attach a throttle: `throttle_classes` is a
# class attribute, so it cannot be set from the URLconf. The subclasses add
# nothing else; all token behaviour stays simplejwt's.
# ---------------------------------------------------------------------------


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """
    POST /api/auth/token/ — obtain a JWT pair, rate limited per client IP.

    The `auth_login` scope is the credential-stuffing defence. It counts every
    attempt, successful or not: counting only failures would let an attacker
    with one valid account probe indefinitely.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"


class ThrottledTokenRefreshView(TokenRefreshView):
    """
    POST /api/auth/token/refresh/ — exchange a refresh token, rate limited.

    A looser scope than login: refresh is a normal thing a client does on a
    timer, and this exists to bound a token-guessing loop rather than to shape
    ordinary traffic.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_refresh"
