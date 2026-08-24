"""
Who can read the OpenAPI schema and the Swagger UI.

`test_schema.py` next door checks that the published document is complete and
truthful. This file checks the other half: that it is not handed to anyone who
asks in production.

**These tests are the reason the gate is a subclass rather than a setting.**
drf-spectacular's own `SERVE_PERMISSIONS` is bound as a class attribute when its
views module is imported, so `override_settings` cannot move it and nothing here
could prove the gate is closed. The finding that prompted this was a URLconf
comment claiming a protection that did not exist — an unverifiable replacement
would have been the same bug with better wording.

Two properties, and both are needed: that the gate *works* when the setting says
to close it, and that production actually *sets* it that way. A working gate
wired to a default of True protects nothing.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from rest_framework.test import APIClient

from apps.users.models import User

SCHEMA_URL = "/api/schema/"
DOCS_URL = "/api/docs/"

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PRODUCTION_SETTINGS = REPO_ROOT / "config" / "settings" / "production.py"


@pytest.fixture
def staff_user(db: None) -> User:
    return User.objects.create_user(
        email="staff@example.com",
        username="staffuser",
        password="StrongPass123!",
        is_staff=True,
    )


@pytest.mark.django_db
class TestSchemaClosed:
    """Production posture."""

    @pytest.fixture(autouse=True)
    def _closed(self, settings: pytest.FixtureRequest) -> None:
        settings.SCHEMA_IS_PUBLIC = False  # type: ignore[attr-defined]

    @pytest.mark.parametrize("url", [SCHEMA_URL, DOCS_URL])
    def test_anonymous_callers_are_refused(self, api_client: APIClient, url: str) -> None:
        response = api_client.get(url)
        assert response.status_code in (401, 403), (
            f"{url} served the API surface to an anonymous caller"
        )

    @pytest.mark.parametrize("url", [SCHEMA_URL, DOCS_URL])
    def test_an_ordinary_signed_in_user_is_refused(
        self, api_client: APIClient, user: User, url: str
    ) -> None:
        """
        Authentication is not the bar — staff is.

        Every registered user would otherwise qualify, which on an open-signup
        service is the same as publishing it: an attacker registers first.
        """
        api_client.force_authenticate(user=user)
        assert api_client.get(url).status_code == 403

    @pytest.mark.parametrize("url", [SCHEMA_URL, DOCS_URL])
    def test_staff_still_get_through(
        self, api_client: APIClient, staff_user: User, url: str
    ) -> None:
        """The gate must not lock out the person who maintains the thing."""
        api_client.force_authenticate(user=staff_user)
        assert api_client.get(url).status_code == 200


@pytest.mark.django_db
class TestSchemaOpen:
    """Local and CI posture — the documented `GET /api/docs/` smoke check."""

    @pytest.fixture(autouse=True)
    def _open(self, settings: pytest.FixtureRequest) -> None:
        settings.SCHEMA_IS_PUBLIC = True  # type: ignore[attr-defined]

    @pytest.mark.parametrize("url", [SCHEMA_URL, DOCS_URL])
    def test_anonymous_callers_are_served(self, api_client: APIClient, url: str) -> None:
        assert api_client.get(url).status_code == 200


def test_production_defaults_to_closed() -> None:
    """
    The deployed config must default to closed, not merely be closable.

    Asserted against the settings source rather than by importing it, the same
    way `test_infrastructure.py` asserts on Helm and Terraform files: importing
    `production` needs a live environment (ALLOWED_HOSTS, CORS_ALLOWED_ORIGINS)
    and would leave a second settings module resident in the interpreter for
    every test that ran afterwards.
    """
    source = PRODUCTION_SETTINGS.read_text()
    match = re.search(
        r"SCHEMA_IS_PUBLIC\s*=\s*env\.bool\(\s*[\"']DJANGO_SCHEMA_IS_PUBLIC[\"']\s*,\s*"
        r"default\s*=\s*(True|False)\s*\)",
        source,
    )
    assert match, "production.py no longer sets SCHEMA_IS_PUBLIC from DJANGO_SCHEMA_IS_PUBLIC"
    assert match.group(1) == "False", (
        "production.py defaults the OpenAPI schema to PUBLIC — it must default to closed"
    )
