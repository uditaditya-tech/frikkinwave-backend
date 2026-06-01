"""
Tests for the public lookup-table endpoints:
  GET /api/musicians/instruments/
  GET /api/musicians/genres/
"""

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import Genre, Instrument


@pytest.mark.django_db
class TestInstrumentList:
    def test_lists_instruments_unauthenticated(self, api_client: APIClient) -> None:
        Instrument.objects.create(name="Bass Guitar", slug="bass-guitar")
        Instrument.objects.create(name="Drums", slug="drums")

        response = api_client.get("/api/musicians/instruments/")

        assert response.status_code == 200
        assert len(response.data) == 2
        # Name-ordered (Instrument.Meta.ordering)
        names = [row["name"] for row in response.data]
        assert names == ["Bass Guitar", "Drums"]
        assert set(response.data[0].keys()) == {"id", "name", "slug"}

    def test_empty_catalogue_returns_empty_list(self, api_client: APIClient) -> None:
        response = api_client.get("/api/musicians/instruments/")
        assert response.status_code == 200
        assert response.data == []


@pytest.mark.django_db
class TestGenreList:
    def test_lists_genres_unauthenticated(self, api_client: APIClient) -> None:
        Genre.objects.create(name="Rock", slug="rock")
        Genre.objects.create(name="Blues", slug="blues")

        response = api_client.get("/api/musicians/genres/")

        assert response.status_code == 200
        names = [row["name"] for row in response.data]
        assert names == ["Blues", "Rock"]
        assert set(response.data[0].keys()) == {"id", "name", "slug"}
