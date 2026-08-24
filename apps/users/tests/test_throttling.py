"""
Rate-limit tests for the credential endpoints.

Coverage:
  - Happy path: traffic under the limit is never throttled
  - Negative path: the request past the limit is refused with 429
  - The counter keys on the client IP, not globally

**Every test clears the cache first.** Throttle state lives in the `default`
cache, which is a database table here, so it is inside the test transaction and
rolls back — but only for tests that touch the database. Clearing explicitly
makes each test independent of what ran before it regardless, which matters
because a leaked counter would show up as a failure in an unrelated test rather
than here.
"""

import pytest
from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.users.models import User

REGISTER_URL = "/api/auth/register/"
LOGIN_URL = "/api/auth/token/"

# Must match DEFAULT_THROTTLE_RATES in config/settings/base.py.
LOGIN_LIMIT = 10
REGISTER_LIMIT = 10


@pytest.fixture(autouse=True)
def _clear_throttle_state(db: None) -> None:
    cache.clear()


def _login(
    client: APIClient,
    password: str = "WrongPass999!",
    remote_addr: str | None = None,
) -> Response:
    """A login attempt. Wrong password by default — a throttle counts attempts."""
    payload = {"email": "user@example.com", "password": password}
    if remote_addr is None:
        return client.post(LOGIN_URL, payload)
    return client.post(LOGIN_URL, payload, REMOTE_ADDR=remote_addr)


@pytest.mark.django_db
class TestLoginThrottle:
    def test_attempts_under_the_limit_are_allowed(self, api_client: APIClient, user: User) -> None:
        """The limit is a ceiling, not a target — nothing below it is refused."""
        for i in range(LOGIN_LIMIT):
            response = _login(api_client)
            assert response.status_code == 401, f"attempt {i + 1} was refused early"

    def test_attempt_past_the_limit_returns_429(self, api_client: APIClient, user: User) -> None:
        for _ in range(LOGIN_LIMIT):
            _login(api_client)

        response = _login(api_client)
        assert response.status_code == 429
        # DRF advertises when to come back; a client that honours it stops
        # hammering, and its absence would mean the throttle is unusable.
        assert "Retry-After" in response.headers

    def test_correct_credentials_are_throttled_too(self, api_client: APIClient, user: User) -> None:
        """
        The counter must not reset on success.

        Counting only failures would leave an attacker holding one valid account
        free to probe forever: alternate a known-good login with guesses and the
        failure counter never fills.
        """
        for _ in range(LOGIN_LIMIT):
            _login(api_client, password="StrongPass123!")

        response = _login(api_client, password="StrongPass123!")
        assert response.status_code == 429

    def test_a_different_client_ip_has_its_own_budget(
        self, api_client: APIClient, user: User
    ) -> None:
        """
        One IP exhausting its budget must not lock everyone else out.

        This is the property NUM_PROXIES protects in production: without it every
        request behind the load balancer would share a single counter, and the
        first attacker to hit the limit would take login down for all users.
        """
        for _ in range(LOGIN_LIMIT + 1):
            _login(api_client, remote_addr="10.0.0.1")

        blocked = _login(api_client, remote_addr="10.0.0.1")
        assert blocked.status_code == 429

        other = _login(api_client, remote_addr="10.0.0.2")
        assert other.status_code == 401, "an unrelated client was caught by the throttle"


@pytest.mark.django_db
class TestRegisterThrottle:
    def test_signups_past_the_limit_return_429(self, api_client: APIClient) -> None:
        """Bulk account creation is the abuse this scope exists to stop."""
        for i in range(REGISTER_LIMIT):
            response = api_client.post(
                REGISTER_URL,
                {
                    "email": f"user{i}@example.com",
                    "username": f"user{i}",
                    "password": "StrongPass123!",
                    "password_confirm": "StrongPass123!",
                },
            )
            assert response.status_code == 201, f"signup {i + 1} was refused early"

        response = api_client.post(
            REGISTER_URL,
            {
                "email": "onemore@example.com",
                "username": "onemore",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
            },
        )
        assert response.status_code == 429
        assert not User.objects.filter(email="onemore@example.com").exists()
