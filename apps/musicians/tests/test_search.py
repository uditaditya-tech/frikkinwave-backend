"""
Search endpoint tests.

The search service is stubbed at the seam: these assert on what the *endpoint*
does with ids and scores — hydration, ordering, the response shape, the
degradation contract. Whether OpenSearch returns good ids for a given query is
apps/search/tests/test_relevance.py's job, against a real cluster.

Stubbing the service rather than `search_profiles` itself is deliberate: it
leaves the hydration path — the id → profile lookup and the missing-profile
branch — genuinely under test.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import MusicianProfile
from apps.search import services as search_services
from apps.users.models import User

SEARCH_URL = "/api/musicians/search/"


def _profile(suffix: str, *, available: bool = True) -> MusicianProfile:
    user = User.objects.create_user(
        email=f"{suffix}@example.com", username=f"user-{suffix}", password="StrongPass123!"
    )
    return MusicianProfile.objects.create(user=user, bio=f"bio {suffix}", is_available=available)


class StubSearch:
    """Stands in for apps.search.services.search: returns canned hits, records calls."""

    def __init__(self) -> None:
        self.hits: list[tuple[uuid.UUID, float]] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, *, query: str, limit: int = 20, available_only: bool = False
    ) -> list[tuple[uuid.UUID, float]]:
        self.calls.append({"query": query, "limit": limit, "available_only": available_only})
        return self.hits[:limit]


@pytest.fixture
def stub_search(monkeypatch: pytest.MonkeyPatch) -> StubSearch:
    """
    Patch the attribute on the search services module.

    search_profiles imports that module inside the function body and looks the
    name up at call time, so replacing the attribute here is what the caller
    actually resolves.
    """
    stub = StubSearch()
    monkeypatch.setattr(search_services, "search", stub)
    return stub


@pytest.mark.django_db
class TestSearch:
    def test_returns_profiles_in_the_order_search_ranked_them(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        a, b, c = _profile("a"), _profile("b"), _profile("c")
        stub_search.hits = [(b.id, 4.2), (a.id, 2.0), (c.id, 0.5)]

        response = api_client.get(SEARCH_URL, {"q": "jazz drummer"})

        assert response.status_code == 200
        assert response.data["query"] == "jazz drummer"
        ids = [r["id"] for r in response.data["results"]]
        assert ids == [str(b.id), str(a.id), str(c.id)]

    def test_each_result_carries_its_score(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        a = _profile("a")
        stub_search.hits = [(a.id, 4.25)]

        response = api_client.get(SEARCH_URL, {"q": "x"})

        assert response.data["results"][0]["score"] == 4.25

    def test_a_hit_whose_profile_is_gone_is_skipped(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        """
        A real state, not an impossible one: the index is a separate store with
        nothing cascading into it, so it can outlive a profile.
        """
        alive = _profile("alive")
        stub_search.hits = [(uuid.uuid4(), 9.0), (alive.id, 1.0)]

        response = api_client.get(SEARCH_URL, {"q": "x"})

        assert [r["id"] for r in response.data["results"]] == [str(alive.id)]

    def test_limit_reaches_the_service_and_is_capped(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        api_client.get(SEARCH_URL, {"q": "x", "limit": "2"})
        assert stub_search.calls[-1]["limit"] == 2

        api_client.get(SEARCH_URL, {"q": "x", "limit": "999"})
        assert stub_search.calls[-1]["limit"] == 50  # clamped, not rejected

        api_client.get(SEARCH_URL, {"q": "x", "limit": "abc"})
        assert stub_search.calls[-1]["limit"] == 20  # garbage falls back to the default

    def test_available_flag_reaches_the_service(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        api_client.get(SEARCH_URL, {"q": "x", "available": "true"})
        assert stub_search.calls[-1]["available_only"] is True

        api_client.get(SEARCH_URL, {"q": "x"})
        assert stub_search.calls[-1]["available_only"] is False

    def test_blank_query_returns_400(self, api_client: APIClient) -> None:
        assert api_client.get(SEARCH_URL, {"q": "   "}).status_code == 400
        assert api_client.get(SEARCH_URL).status_code == 400

    def test_unauthenticated_allowed(self, api_client: APIClient, stub_search: StubSearch) -> None:
        stub_search.hits = [(_profile("a").id, 1.0)]
        assert api_client.get(SEARCH_URL, {"q": "x"}).status_code == 200

    def test_no_cluster_returns_empty_not_an_error(self, api_client: APIClient) -> None:
        """
        Nothing is stubbed here: settings blank OPENSEARCH_URL under pytest, so
        this runs the real degradation path end to end. An upstream failure must
        never 500 a user request.
        """
        _profile("a")

        response = api_client.get(SEARCH_URL, {"q": "x"})

        assert response.status_code == 200
        assert response.data["results"] == []


@pytest.mark.django_db
class TestTheDocumentedShapeMatchesTheRealOne:
    """
    ProfileSearchResponseSerializer exists only to give drf-spectacular a shape
    to publish; the view builds its dict by hand and never instantiates it.

    That gap is the whole risk. Nothing at runtime makes the declaration and the
    response agree, so a key added to one and not the other would ship a schema
    that lies — and a lying schema is worse than the missing one it replaced,
    because a generated client trusts it.
    """

    def test_the_response_has_exactly_the_documented_top_level_keys(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        from apps.musicians.serializers import ProfileSearchResponseSerializer

        stub_search.hits = [(_profile("a").id, 1.5)]

        response = api_client.get(SEARCH_URL, {"q": "x"})

        declared = set(ProfileSearchResponseSerializer().fields)
        assert set(response.data) == declared, (
            f"The endpoint returns {sorted(response.data)} but the schema "
            f"advertises {sorted(declared)}."
        )

    def test_each_result_has_exactly_the_documented_fields(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        from apps.musicians.serializers import ProfileSearchResultSerializer

        stub_search.hits = [(_profile("a").id, 1.5)]

        response = api_client.get(SEARCH_URL, {"q": "x"})

        declared = set(ProfileSearchResultSerializer().fields)
        assert set(response.data["results"][0]) == declared

    def test_the_documented_limits_are_the_ones_enforced(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        """
        The schema quotes SEARCH_MAX_LIMIT and SEARCH_QUERY_MAX_LEN in its
        parameter descriptions. If the constants move and the behaviour follows,
        the prose silently stops being true — so assert the behaviour against
        the same constants the description interpolates.
        """
        from apps.musicians.views import SEARCH_MAX_LIMIT, SEARCH_QUERY_MAX_LEN

        api_client.get(SEARCH_URL, {"q": "x", "limit": str(SEARCH_MAX_LIMIT + 500)})
        assert stub_search.calls[-1]["limit"] == SEARCH_MAX_LIMIT

        over = api_client.get(SEARCH_URL, {"q": "a" * (SEARCH_QUERY_MAX_LEN + 1)})
        assert over.status_code == 400
        at_limit = api_client.get(SEARCH_URL, {"q": "a" * SEARCH_QUERY_MAX_LEN})
        assert at_limit.status_code == 200
