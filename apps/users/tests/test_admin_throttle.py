"""
Rate-limit tests for the Django admin login form.

Coverage:
  - Happy path: attempts under the limit reach the login view
  - Negative path: the attempt past the limit is refused with 429
  - Only POST to the login URL is throttled — GETs and other paths are not
  - The counter keys on the client IP

`/admin/` is a session-auth Django view, so none of this goes through DRF's
view-level throttling; the middleware is the only thing standing in front of it.
These tests exist because that is easy to remove by accident — deleting one line
from MIDDLEWARE disables it with no other symptom.
"""

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

# Must match DEFAULT_THROTTLE_RATES["admin_login"] in config/settings/base.py.
ADMIN_LOGIN_LIMIT = 5


@pytest.fixture(autouse=True)
def _clear_throttle_state(db: None) -> None:
    cache.clear()


@pytest.fixture
def admin_login_url() -> str:
    return reverse("admin:login")


def _attempt(client: Client, url: str, remote_addr: str | None = None):  # type: ignore[no-untyped-def]
    payload = {"username": "admin@example.com", "password": "wrong-password"}
    if remote_addr is None:
        return client.post(url, payload)
    return client.post(url, payload, REMOTE_ADDR=remote_addr)


@pytest.mark.django_db
class TestAdminLoginThrottle:
    def test_attempts_under_the_limit_reach_the_login_view(
        self, client: Client, admin_login_url: str
    ) -> None:
        """
        Below the limit the form answers normally.

        A failed admin login re-renders the form with an error — a 200, not a
        401 — so anything that is not 429 means the middleware let it through.
        """
        for i in range(ADMIN_LOGIN_LIMIT):
            response = _attempt(client, admin_login_url)
            assert response.status_code != 429, f"attempt {i + 1} was refused early"

    def test_attempt_past_the_limit_returns_429(self, client: Client, admin_login_url: str) -> None:
        for _ in range(ADMIN_LOGIN_LIMIT):
            _attempt(client, admin_login_url)

        response = _attempt(client, admin_login_url)
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_get_requests_are_not_throttled(self, client: Client, admin_login_url: str) -> None:
        """
        Serving the form costs nothing and is not an attempt.

        Throttling GETs would lock a legitimate admin out of the page after a
        few refreshes while doing nothing to slow a scripted attack, which posts
        directly.
        """
        for _ in range(ADMIN_LOGIN_LIMIT * 3):
            response = client.get(admin_login_url)
            assert response.status_code != 429

    def test_other_paths_are_untouched(self, client: Client, admin_login_url: str) -> None:
        """The middleware runs on every request; it must only act on this one."""
        for _ in range(ADMIN_LOGIN_LIMIT + 1):
            _attempt(client, admin_login_url)
        assert _attempt(client, admin_login_url).status_code == 429

        # The API's own login is a separate scope with a separate budget.
        response = client.post(
            "/api/auth/token/",
            {"email": "user@example.com", "password": "whatever"},
        )
        assert response.status_code != 429

        assert client.get("/api/health/").status_code == 200

    def test_a_different_client_ip_has_its_own_budget(
        self, client: Client, admin_login_url: str
    ) -> None:
        """One attacker must not be able to lock the real admin out."""
        for _ in range(ADMIN_LOGIN_LIMIT + 1):
            _attempt(client, admin_login_url, remote_addr="10.0.0.1")

        assert _attempt(client, admin_login_url, remote_addr="10.0.0.1").status_code == 429
        assert _attempt(client, admin_login_url, remote_addr="10.0.0.2").status_code != 429


@pytest.mark.django_db
class TestAdminPathIsConfigurable:
    def test_throttle_follows_the_admin_path_rather_than_hardcoding_it(
        self, admin_login_url: str
    ) -> None:
        """
        The middleware derives its target from the URLconf.

        If it ever hardcodes "/admin/login/", setting DJANGO_ADMIN_PATH moves the
        admin and the throttle keeps watching an address nothing serves — the
        control silently stops working with no error anywhere. Asserting the
        derivation is what stops that refactor.
        """
        from django.conf import settings

        from apps.users.middleware import AdminLoginThrottleMiddleware

        middleware = AdminLoginThrottleMiddleware(lambda request: None)  # type: ignore[arg-type, return-value]
        assert middleware._login_path == admin_login_url
        assert admin_login_url.startswith(f"/{settings.ADMIN_PATH}")
