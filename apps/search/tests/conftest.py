"""
Fixtures for search tests.

The two that matter — `fake_search` (spy client) and `opensearch` (real cluster,
unique index, skips without OPENSEARCH_TEST_URL) — live in the root conftest,
because the musicians rebuild tests need them too. The double itself is
`apps.search.testing.FakeSearchClient`.

What is left here is domain data. It builds MusicianProfile rows, which looks
like the coupling this extraction removed — it is not. The *service* needs only
ids and plain fields. Profiles appear because the pipeline tests exercise the
seam end to end, and testing a seam requires both sides.
"""

from __future__ import annotations

import pytest

from apps.musicians.models import Genre, Instrument, MusicianProfile
from apps.users.models import User


@pytest.fixture
def instrument(db: None) -> Instrument:
    return Instrument.objects.create(name="Electric Guitar", slug="electric-guitar")


@pytest.fixture
def genre(db: None) -> Genre:
    return Genre.objects.create(name="Jazz", slug="jazz")


@pytest.fixture
def profile(user: User) -> MusicianProfile:
    return MusicianProfile.objects.create(
        user=user,
        bio="I play lead guitar.",
        city="Mumbai",
        country="India",
        is_available=True,
    )
