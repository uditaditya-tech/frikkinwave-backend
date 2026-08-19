"""
build_embedding_text stays in the musicians app.

Composing the text needs the profile's instruments and genres, so it belongs
with that data. Only its OUTPUT crosses to the search service, carried on the
`profile.updated` event — which is why search never has to read these tables.

The dedupe in search.services.index_profile compares this text to what was last
embedded, so it must stay DETERMINISTIC: same profile, same string, or every
save costs an OpenAI call.
"""

import pytest

from apps.musicians import services
from apps.musicians.models import Genre, Instrument, MusicianProfile


@pytest.mark.django_db
class TestBuildEmbeddingText:
    def test_includes_bio_location_instruments_genres(
        self, profile: MusicianProfile, instrument: Instrument, genre: Genre
    ) -> None:
        profile.musician_instruments.create(instrument=instrument, proficiency="advanced")
        profile.genres.add(genre)

        text = services.build_embedding_text(profile)

        assert "I play lead guitar." in text
        assert "Mumbai" in text
        assert "Electric Guitar (advanced)" in text
        assert "Jazz" in text

    def test_is_deterministic(
        self, profile: MusicianProfile, instrument: Instrument, genre: Genre
    ) -> None:
        """The re-embed skip depends on this; drift here costs money silently."""
        profile.musician_instruments.create(instrument=instrument, proficiency="advanced")
        profile.genres.add(genre)

        assert services.build_embedding_text(profile) == services.build_embedding_text(profile)
