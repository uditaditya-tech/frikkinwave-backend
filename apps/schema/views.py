"""
The OpenAPI schema and Swagger UI, gated by environment.

These endpoints publish every path, parameter, enum and response shape in the
project. That is exactly what you want in development and exactly what you do
not want handed to an anonymous caller in production, where it is a complete
map of the attack surface.

**Why subclasses and not `SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"]`.** That
setting is the documented lever and it does work — but drf-spectacular binds it
as a *class attribute* at import time (`permission_classes =
spectacular_settings.SERVE_PERMISSIONS`), so `override_settings` cannot reach it
and no test can prove the gate is closed. This finding began as a comment in the
URLconf asserting a protection that did not exist; replacing it with a second
control nothing can verify would repeat the mistake in a quieter way.

Resolving the permission **per request** in `get_permissions` is what makes it
testable, and the tests in `tests/test_schema_access.py` are the point of the
whole arrangement.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.conf import settings
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authentication import BaseAuthentication, SessionAuthentication
from rest_framework.permissions import AllowAny, BasePermission, IsAdminUser
from rest_framework_simplejwt.authentication import JWTAuthentication


class _EnvironmentGatedSchema:
    """
    Public where `SCHEMA_IS_PUBLIC` is set, staff-only everywhere else.

    Session auth is listed first so the browsable Swagger UI works for someone
    already signed into the Django admin — the realistic way a maintainer reads
    these docs in production. JWT stays available for scripted access.
    """

    # Annotated because the mixin has to widen the element type to match what
    # APIView declares it as Sequence[type[BaseAuthentication]]; a bare list
    # infers as the two concrete classes and collides with the base, and a
    # list[...] annotation collides too because list is invariant.
    # (Not a ClassVar — see RUF012 in CLAUDE.md.)
    authentication_classes: Sequence[type[BaseAuthentication]] = [
        SessionAuthentication,
        JWTAuthentication,
    ]

    def get_permissions(self) -> list[BasePermission]:
        """
        Read the setting on every request rather than at import.

        This is the difference between a gate that can be tested and one that
        can only be asserted about. Cheap: a settings attribute lookup.
        """
        if settings.SCHEMA_IS_PUBLIC:
            return [AllowAny()]
        return [IsAdminUser()]


class GatedSchemaView(_EnvironmentGatedSchema, SpectacularAPIView):
    """GET /api/schema/ — the raw OpenAPI document."""


class GatedSwaggerView(_EnvironmentGatedSchema, SpectacularSwaggerView):
    """GET /api/docs/ — Swagger UI, which fetches the document above."""
