"""
Profile endpoint tests — create, retrieve, update.

Coverage:
  - Happy path for each endpoint
  - At least one negative path per endpoint
"""

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import Genre, Instrument, MusicianInstrument, MusicianProfile
from apps.users.models import User

PROFILE_URL = "/api/musicians/profile/"
PROFILE_ME_URL = "/api/musicians/profile/me/"
PROFILES_URL = "/api/musicians/profiles/"


def _auth(api_client: APIClient, user: User) -> APIClient:
    """Log in and attach Bearer token to the client."""
    resp = api_client.post(
        "/api/auth/token/",
        {"email": user.email, "password": "StrongPass123!"},
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


def _make_profile(
    suffix: str,
    *,
    city: str = "Mumbai",
    country: str = "India",
    is_available: bool = True,
    instruments: list[Instrument] | None = None,
    genres: list[Genre] | None = None,
) -> MusicianProfile:
    """Create a user + profile with a unique identity for list/filter tests."""
    owner = User.objects.create_user(
        email=f"{suffix}@example.com",
        username=f"user-{suffix}",
        password="StrongPass123!",
    )
    profile = MusicianProfile.objects.create(
        user=owner,
        bio=f"bio {suffix}",
        city=city,
        country=country,
        is_available=is_available,
    )
    for inst in instruments or []:
        MusicianInstrument.objects.create(profile=profile, instrument=inst)
    if genres:
        profile.genres.set(genres)
    return profile


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateProfile:
    def test_success_returns_201(self, api_client: APIClient, user: User) -> None:
        _auth(api_client, user)
        response = api_client.post(PROFILE_URL, {"bio": "Guitarist", "city": "Delhi"})
        assert response.status_code == 201
        assert response.data["bio"] == "Guitarist"
        assert response.data["city"] == "Delhi"
        assert MusicianProfile.objects.filter(user=user).exists()

    def test_with_instruments_and_genres(
        self,
        api_client: APIClient,
        user: User,
        instrument: Instrument,
        genre: Genre,
    ) -> None:
        _auth(api_client, user)
        payload = {
            "instruments": [{"instrument": str(instrument.id), "proficiency": "advanced"}],
            "genres": [str(genre.id)],
        }
        response = api_client.post(PROFILE_URL, payload, format="json")
        assert response.status_code == 201
        assert len(response.data["instruments"]) == 1
        assert response.data["instruments"][0]["proficiency"] == "advanced"
        assert len(response.data["genres"]) == 1

    def test_duplicate_returns_409(
        self, api_client: APIClient, user: User, profile: MusicianProfile
    ) -> None:
        _auth(api_client, user)
        response = api_client.post(PROFILE_URL, {"bio": "Second profile"})
        assert response.status_code == 409

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        response = api_client.post(PROFILE_URL, {"bio": "No token"})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Retrieve (me)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetrieveProfile:
    def test_success_returns_own_profile(
        self, api_client: APIClient, user: User, profile: MusicianProfile
    ) -> None:
        _auth(api_client, user)
        response = api_client.get(PROFILE_ME_URL)
        assert response.status_code == 200
        assert str(response.data["user_id"]) == str(user.id)
        assert response.data["bio"] == profile.bio

    def test_no_profile_returns_404(self, api_client: APIClient, user: User) -> None:
        _auth(api_client, user)
        response = api_client.get(PROFILE_ME_URL)
        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        response = api_client.get(PROFILE_ME_URL)
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Update (me)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateProfile:
    def test_scalar_fields_updated(
        self, api_client: APIClient, user: User, profile: MusicianProfile
    ) -> None:
        _auth(api_client, user)
        response = api_client.patch(PROFILE_ME_URL, {"city": "Bangalore", "is_available": False})
        assert response.status_code == 200
        assert response.data["city"] == "Bangalore"
        assert response.data["is_available"] is False
        # Fields not in payload are unchanged
        assert response.data["bio"] == profile.bio

    def test_instruments_replaced(
        self,
        api_client: APIClient,
        user: User,
        profile: MusicianProfile,
        instrument: Instrument,
    ) -> None:
        _auth(api_client, user)
        payload = {"instruments": [{"instrument": str(instrument.id), "proficiency": "beginner"}]}
        response = api_client.patch(PROFILE_ME_URL, payload, format="json")
        assert response.status_code == 200
        assert len(response.data["instruments"]) == 1
        assert response.data["instruments"][0]["proficiency"] == "beginner"

    def test_genres_replaced(
        self,
        api_client: APIClient,
        user: User,
        profile: MusicianProfile,
        genre: Genre,
    ) -> None:
        _auth(api_client, user)
        payload = {"genres": [str(genre.id)]}
        response = api_client.patch(PROFILE_ME_URL, payload, format="json")
        assert response.status_code == 200
        assert len(response.data["genres"]) == 1
        assert response.data["genres"][0]["name"] == genre.name

    def test_omitted_instruments_unchanged(
        self,
        api_client: APIClient,
        user: User,
        profile: MusicianProfile,
        instrument: Instrument,
    ) -> None:
        """PATCH without 'instruments' key must not clear existing instruments."""
        MusicianInstrument.objects.create(
            profile=profile,
            instrument=instrument,
            proficiency=MusicianInstrument.Proficiency.ADVANCED,
        )
        _auth(api_client, user)
        # PATCH only bio — instruments must be untouched
        response = api_client.patch(PROFILE_ME_URL, {"bio": "Updated bio"})
        assert response.status_code == 200
        assert len(response.data["instruments"]) == 1

    def test_no_profile_returns_404(self, api_client: APIClient, user: User) -> None:
        _auth(api_client, user)
        response = api_client.patch(PROFILE_ME_URL, {"bio": "Updated"})
        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        response = api_client.patch(PROFILE_ME_URL, {"bio": "Updated"})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# List + filter (public discovery feed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListProfiles:
    def test_unauthenticated_allowed(self, api_client: APIClient) -> None:
        _make_profile("a")
        response = api_client.get(PROFILES_URL)
        assert response.status_code == 200

    def test_unfiltered_returns_all(self, api_client: APIClient) -> None:
        _make_profile("a")
        _make_profile("b")
        response = api_client.get(PROFILES_URL)
        assert response.status_code == 200
        assert len(response.data["results"]) == 2

    def test_filter_by_city_case_insensitive(self, api_client: APIClient) -> None:
        _make_profile("delhi", city="Delhi")
        _make_profile("mumbai", city="Mumbai")
        response = api_client.get(PROFILES_URL, {"city": "delhi"})
        assert response.status_code == 200
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["city"] == "Delhi"

    def test_filter_by_country(self, api_client: APIClient) -> None:
        _make_profile("in", country="India")
        _make_profile("us", country="USA")
        response = api_client.get(PROFILES_URL, {"country": "USA"})
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["country"] == "USA"

    def test_filter_by_instrument_slug(self, api_client: APIClient, instrument: Instrument) -> None:
        other = Instrument.objects.create(name="Drums", slug="drums")
        _make_profile("guitarist", instruments=[instrument])
        _make_profile("drummer", instruments=[other])
        response = api_client.get(PROFILES_URL, {"instrument": instrument.slug})
        assert len(response.data["results"]) == 1

    def test_filter_by_genre_slug(self, api_client: APIClient, genre: Genre) -> None:
        other = Genre.objects.create(name="Rock", slug="rock")
        _make_profile("jazzcat", genres=[genre])
        _make_profile("rocker", genres=[other])
        response = api_client.get(PROFILES_URL, {"genre": genre.slug})
        assert len(response.data["results"]) == 1

    def test_filter_by_available(self, api_client: APIClient) -> None:
        _make_profile("free", is_available=True)
        _make_profile("busy", is_available=False)
        response = api_client.get(PROFILES_URL, {"available": "true"})
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["is_available"] is True

    def test_combined_city_and_instrument(
        self, api_client: APIClient, instrument: Instrument
    ) -> None:
        # Matches both filters.
        _make_profile("match", city="Pune", instruments=[instrument])
        # Right city, wrong instrument.
        other = Instrument.objects.create(name="Bass", slug="bass")
        _make_profile("wrong-inst", city="Pune", instruments=[other])
        # Right instrument, wrong city.
        _make_profile("wrong-city", city="Delhi", instruments=[instrument])
        response = api_client.get(PROFILES_URL, {"city": "Pune", "instrument": instrument.slug})
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["city"] == "Pune"

    def test_pagination_next_cursor_present(self, api_client: APIClient) -> None:
        for i in range(21):  # page_size is 20
            _make_profile(f"p{i}")
        response = api_client.get(PROFILES_URL)
        assert response.status_code == 200
        assert len(response.data["results"]) == 20
        assert response.data["next"] is not None
