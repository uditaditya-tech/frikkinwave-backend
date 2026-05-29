# Shared fixtures (api_client, user) live in the root conftest.py.

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
