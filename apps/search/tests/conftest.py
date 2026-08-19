"""
Fixtures for search tests.

These build MusicianProfile rows, which looks like the coupling this extraction
just removed — it is not. The *service* under test needs only a profile id and a
composed string. Profiles appear here because the pipeline tests exercise the
seam end to end: profile saved → event published → relayed → indexed. Testing
the seam requires both sides.

The pure unit tests in test_service.py touch no musicians model at all, which is
the property that matters.
"""

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
