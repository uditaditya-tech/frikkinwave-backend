"""
Indexing pipeline tests — the musicians → search seam.

The two apps are connected by nothing but an event, and this is what proves the
connection still works: profile save → outbox publish → inline relay → consumer
→ service. API-level tests wrap the request in
`django_capture_on_commit_callbacks` because the publish happens on commit.

These use the spy client rather than a cluster. What is under test is the seam —
that the producer publishes the facts the consumer needs, under the keys the
consumer reads. Whether OpenSearch then ranks them well is test_relevance.py's
problem, and keeping the two apart means a broken payload gives a clear failure
here instead of a confusing empty result set there.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import Genre, Instrument, MusicianProfile
from apps.search import consumers
from apps.search.tests.conftest import FakeSearchClient
from apps.users.models import User

PASSWORD = "StrongPass123!"
PROFILE_URL = "/api/musicians/profile/"
PROFILE_ME_URL = "/api/musicians/profile/me/"


def _auth(api_client: APIClient, user: User) -> APIClient:
    resp = api_client.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


@pytest.mark.django_db
class TestIndexingPipeline:
    def test_creating_a_profile_indexes_it(
        self,
        api_client: APIClient,
        user: User,
        fake_search: FakeSearchClient,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        _auth(api_client, user)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(PROFILE_URL, {"bio": "Drummer for hire", "city": "Pune"})

        assert response.status_code == 201
        profile = MusicianProfile.objects.get(user=user)
        document = fake_search.indexed[str(profile.id)]
        assert document["bio"] == "Drummer for hire"
        assert document["city"] == "Pune"

    def test_the_payload_carries_instruments_and_genres_by_name(
        self,
        api_client: APIClient,
        user: User,
        instrument: Instrument,
        genre: Genre,
        fake_search: FakeSearchClient,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        """
        Names, not ids. The search service must never have to resolve an id
        against a musicians table to find out what it means — that lookup is
        exactly the coupling the extraction removed.
        """
        _auth(api_client, user)
        with django_capture_on_commit_callbacks(execute=True):
            api_client.post(
                PROFILE_URL,
                {
                    "bio": "Session player",
                    "instruments": [{"instrument": str(instrument.id), "proficiency": "advanced"}],
                    "genres": [str(genre.id)],
                },
                format="json",
            )

        profile = MusicianProfile.objects.get(user=user)
        document = fake_search.indexed[str(profile.id)]
        assert document["instruments"] == ["Electric Guitar"]
        assert document["genres"] == ["Jazz"]

    def test_updating_a_bio_reindexes(
        self,
        api_client: APIClient,
        user: User,
        fake_search: FakeSearchClient,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        _auth(api_client, user)
        with django_capture_on_commit_callbacks(execute=True):
            api_client.post(PROFILE_URL, {"bio": "original", "city": "Pune"})
        with django_capture_on_commit_callbacks(execute=True):
            api_client.patch(PROFILE_ME_URL, {"bio": "totally new bio"})

        profile = MusicianProfile.objects.get(user=user)
        assert fake_search.indexed[str(profile.id)]["bio"] == "totally new bio"

    def test_toggling_availability_reindexes(
        self,
        api_client: APIClient,
        user: User,
        fake_search: FakeSearchClient,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        """
        It has to: is_available is a filter field in the index, so a stale copy
        keeps offering a musician who has said they are busy.

        This used to be the case the pipeline deliberately skipped, because
        re-embedding unchanged text cost an OpenAI call. Indexing has no such
        cost, so the skip — and the stored copy of the previous text it needed —
        are both gone.
        """
        _auth(api_client, user)
        with django_capture_on_commit_callbacks(execute=True):
            api_client.post(PROFILE_URL, {"bio": "steady", "city": "Pune"})
        with django_capture_on_commit_callbacks(execute=True):
            api_client.patch(PROFILE_ME_URL, {"is_available": False})

        profile = MusicianProfile.objects.get(user=user)
        assert fake_search.indexed[str(profile.id)]["is_available"] is False


class TestConsumerContract:
    """
    The handler reads the payload strictly. A missing key is a producer that
    changed shape, and the useful outcome is a loud failure that reaches the
    dead-letter topic — not a blank document that makes the profile unfindable
    while reporting success.
    """

    def test_a_missing_key_raises(self, fake_search: FakeSearchClient) -> None:
        with pytest.raises(KeyError):
            consumers.index_profile(
                profile_id="00000000-0000-0000-0000-000000000000",
                bio="no instruments key here",
            )

    def test_a_complete_payload_indexes(self, fake_search: FakeSearchClient) -> None:
        consumers.index_profile(
            profile_id="00000000-0000-0000-0000-000000000000",
            bio="b",
            instruments=["Guitar"],
            genres=["Rock"],
            city="Goa",
            country="India",
            is_available=True,
        )
        assert "00000000-0000-0000-0000-000000000000" in fake_search.indexed
