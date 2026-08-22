"""
The published OpenAPI schema is a contract, so it is tested like one.

Two different properties, and the distinction matters:

COMPLETENESS is what `test_schema_generates_cleanly` checks. Every endpoint in
this project is a plain APIView, which drf-spectacular cannot introspect — it
logs "unable to guess serializer" and publishes the path with NO body. That is
the worst failure mode available, because the endpoint still appears in the
schema and a generated client gets a method that returns nothing describable.
For most of this project's life 44 of them were in that state.

TRUTHFULNESS is what the rest check, on a sample. A declaration that drifts from
what the view returns is worse than no declaration at all: a missing schema makes
a client author go and read the code, while a wrong one is believed. Nothing at
runtime keeps `@extend_schema(responses=...)` honest, so the agreement has to be
asserted where it is cheap to assert.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
import yaml
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.users.models import User


def _schema() -> dict[str, Any]:
    out = io.StringIO()
    call_command("spectacular", "--format", "openapi", stdout=out)
    parsed: dict[str, Any] = yaml.safe_load(out.getvalue())
    return parsed


def test_schema_generates_cleanly() -> None:
    """
    No errors, no warnings.

    Both are load-bearing. An error means a view was SKIPPED and publishes no
    body. A warning is usually a name collision resolved with a hash suffix —
    `Status7baEnum`, `bandsRetrieve2` — which is a valid schema and unusable
    from a generated client, because nothing says which `bandsRetrieve` is the
    list and which is the detail.

    If this fails after adding a view, annotate it with @extend_schema rather
    than relaxing the assertion.
    """
    from drf_spectacular.drainage import GENERATOR_STATS

    _schema()

    assert not GENERATOR_STATS._error_cache, (
        "Schema generation produced errors — some endpoint publishes no response "
        f"body: {list(GENERATOR_STATS._error_cache)[:5]}"
    )
    assert not GENERATOR_STATS._warn_cache, (
        "Schema generation produced warnings — usually a collision resolved with "
        f"a hash suffix: {list(GENERATOR_STATS._warn_cache)[:5]}"
    )


def test_every_path_documents_a_success_response() -> None:
    """A path with no 2xx is a path a client cannot consume."""
    schema = _schema()
    missing = [
        f"{method.upper()} {path}"
        for path, ops in schema["paths"].items()
        for method, op in ops.items()
        if method in {"get", "post", "patch", "put", "delete"}
        and not any(code.startswith("2") for code in op.get("responses", {}))
    ]
    assert not missing, f"No 2xx response documented for: {missing}"


def test_paginated_lists_document_the_envelope_not_a_bare_array() -> None:
    """
    Cursor-paginated endpoints return `{next, previous, results}`. Declaring the
    item serializer with many=True would publish a bare array — a client built
    against it fails on the first response it ever receives.
    """
    schema = _schema()
    pages = [name for name in schema["components"]["schemas"] if name.endswith("CursorPage")]
    assert pages, "No pagination envelopes in the schema at all."
    for name in pages:
        props = schema["components"]["schemas"][name]["properties"]
        assert set(props) == {"next", "previous", "results"}, (
            f"{name} does not match the CursorPagination envelope: {sorted(props)}"
        )
        # CursorPagination computes no total; publishing `count` would invent one.
        assert "count" not in props


@pytest.mark.django_db
class TestTheSchemaTellsTheTruth:
    """
    Spot-checks against real responses. Not exhaustive — the point is to catch
    the class of mistake, not every instance.
    """

    def test_the_error_body_is_the_shape_the_schema_publishes(self, api_client: APIClient) -> None:
        from apps.schema.serializers import DetailSerializer

        response = api_client.get("/api/musicians/profiles/nobody-by-that-name/")

        assert response.status_code == 404
        assert set(response.data) == set(DetailSerializer().fields)

    def test_a_paginated_list_really_returns_the_declared_envelope(
        self, api_client: APIClient
    ) -> None:
        from apps.musicians.serializers import MusicianProfileReadSerializer
        from apps.schema.serializers import cursor_paginated

        response = api_client.get("/api/musicians/profiles/")

        assert response.status_code == 200
        declared = set(cursor_paginated(MusicianProfileReadSerializer)().fields)
        assert set(response.data) == declared

    def test_registration_really_returns_a_token_pair(self, api_client: APIClient) -> None:
        """The surprising one: register returns tokens, not the created user."""
        from apps.users.serializers import TokenPairSerializer

        response = api_client.post(
            "/api/auth/register/",
            {
                "email": "schema-check@example.com",
                "username": "schema-check",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
            },
        )

        assert response.status_code == 201
        assert set(response.data) == set(TokenPairSerializer().fields)
        assert User.objects.filter(email="schema-check@example.com").exists()
