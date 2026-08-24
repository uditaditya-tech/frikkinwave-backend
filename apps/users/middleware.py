"""
Rate limiting for the Django admin login form.

DRF's throttles only wrap DRF views, so `/admin/` — a session-auth Django view —
sat unmetered while every credential endpoint on the API was limited. That gap
is the more attractive of the two: an admin password is worth more than a user's,
and the form is on a predictable path.

**This reuses DRF's throttle engine rather than reimplementing it.**
`SimpleRateThrottle.allow_request` touches the request only through
`get_cache_key`, and `get_ident` reads only `request.META`, so both work
unchanged on a plain Django `HttpRequest`. Reusing them means the admin throttle
and the API throttles share one sliding-window implementation, one cache, one
rate table, and — critically — one definition of "the client's IP", so
`NUM_PROXIES` cannot end up meaning two different things in one codebase.

**The `arg-type` ignores below are that reuse, and nothing more.** DRF's stubs
type these methods for a DRF `Request` and `APIView` because that is how DRF
itself calls them; the runtime contract they actually rely on is `request.META`,
which `HttpRequest` has. The ignores are per-line rather than a file-level
suppression so that any *other* type error here still fails the build.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from django.http import HttpResponse
from django.urls import NoReverseMatch, reverse
from django.utils.functional import cached_property
from rest_framework.throttling import SimpleRateThrottle

if TYPE_CHECKING:  # pragma: no cover
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


class AdminLoginRateThrottle(SimpleRateThrottle):
    """
    Per-IP throttle for the admin login form.

    The rate comes from `DEFAULT_THROTTLE_RATES["admin_login"]`, so every limit
    in the project is declared in one table even though this one is applied
    outside DRF.
    """

    scope = "admin_login"

    def get_cache_key(self, request: HttpRequest, view: object = None) -> str:
        """
        Key on the client IP.

        Always returns a key — never None. Returning None is DRF's "do not
        throttle this caller" signal, and there is no caller here who should be
        exempt: the whole point is that the requester is anonymous and
        unidentified until they guess correctly.
        """
        ident = self.get_ident(request)  # type: ignore[arg-type]  # HttpRequest: reads META only
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AdminLoginThrottleMiddleware:
    """
    Reject admin login attempts past the configured rate with a 429.

    Only POST to the login URL is throttled. GETs are the form itself and cost
    nothing to serve, and throttling authenticated admin work would punish the
    one person the admin exists for.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    @cached_property
    def _login_path(self) -> str | None:
        """
        Resolve the admin login URL once, at first use.

        Reversed rather than hardcoded, so moving the admin off `/admin/` via
        DJANGO_ADMIN_PATH keeps the throttle pointed at it — a hardcoded
        "/admin/login/" would silently stop matching and disable this entirely,
        which is exactly the kind of security control that fails quietly.

        Resolved lazily because middleware is built during startup, when the
        URLconf may not be loaded yet. None means the admin is not routed at
        all, and the middleware becomes a no-op.
        """
        try:
            return reverse("admin:login")
        except NoReverseMatch:
            return None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "POST" and request.path == self._login_path:
            throttle = AdminLoginRateThrottle()
            # view=None: allow_request only forwards it to get_cache_key, which
            # is overridden above and ignores it.
            if not throttle.allow_request(request, None):  # type: ignore[arg-type]
                wait = throttle.wait()
                logger.warning(
                    "admin_login_throttled",
                    extra={
                        "ident": throttle.get_ident(request),  # type: ignore[arg-type]
                        "wait_seconds": wait,
                    },
                )
                response = HttpResponse(
                    "Too many login attempts. Try again later.",
                    content_type="text/plain",
                    status=429,
                )
                if wait is not None:
                    # Same contract DRF gives API clients, so a proxy or a
                    # monitoring tool reads this the same way on both.
                    response["Retry-After"] = str(int(wait) + 1)
                return response

        return self.get_response(request)
