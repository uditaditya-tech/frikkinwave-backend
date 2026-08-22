"""
Root conftest — fixtures available to all apps.
"""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pytest_django.fixtures import SettingsWrapper
from rest_framework.test import APIClient

from apps.search.testing import FakeSearchClient
from apps.users.models import User


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db: None) -> User:
    return User.objects.create_user(
        email="user@example.com",
        username="testuser",
        password="StrongPass123!",
    )


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> FakeSearchClient:
    """
    Swap the search client for a spy, and configure a URL so the guards let calls
    through.

    Proves wiring only — that a filter is asked for, that a limit is passed, that
    an unreachable cluster degrades. Anything that is a claim about OpenSearch
    belongs in a test using `opensearch` instead.
    """
    from apps.search import services

    settings.OPENSEARCH_URL = "http://fake:9200"
    client = FakeSearchClient()
    monkeypatch.setattr(services, "get_search_client", lambda: client)
    return client


@pytest.fixture
def opensearch(settings: SettingsWrapper) -> Iterator[Any]:
    """
    Point the search service at a real cluster, in an index unique to this test.

    SKIPS when OPENSEARCH_TEST_URL is unset, so `pytest` works on a laptop with
    no container — tests/test_architecture.py asserts CI sets it, so skipping
    stays a local convenience rather than a way for these to stop running.

    A unique index per test rather than a shared one emptied between tests:
    emptying relies on a refresh landing before the next write, which is the kind
    of ordering that fails once in fifty runs and looks like a flake somewhere
    else entirely.
    """
    url = os.environ.get("OPENSEARCH_TEST_URL")
    if not url:
        pytest.skip("OPENSEARCH_TEST_URL is not set — no cluster to test against")

    from apps.search.client import get_search_client

    settings.OPENSEARCH_URL = url
    settings.OPENSEARCH_INDEX = f"test-profiles-{uuid.uuid4().hex}"
    get_search_client.cache_clear()

    client = get_search_client()
    try:
        yield client
    finally:
        client.delete_index()
        get_search_client.cache_clear()
