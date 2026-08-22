"""
Rebuild-from-Postgres tests.

The index is derived data outside the database with no snapshot of its own, so
this command is what stands between a teardown and a healthy cluster serving
nothing. The prune half is tested against a real cluster, because
`delete_by_query` over a date range is a claim about OpenSearch rather than
about our code.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.musicians.models import Genre, Instrument, MusicianProfile
from apps.search import services as search_services
from apps.users.models import User


def _profile(suffix: str, **kwargs: Any) -> MusicianProfile:
    user = User.objects.create_user(
        email=f"{suffix}@example.com", username=f"user-{suffix}", password="StrongPass123!"
    )
    return MusicianProfile.objects.create(user=user, **kwargs)


def _run(*args: str) -> str:
    out = StringIO()
    call_command("reindex_profiles", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestRebuildWiring:
    """Spy-level: what the command sends, without needing a cluster."""

    def test_indexes_every_profile(self, fake_search: Any) -> None:
        a = _profile("a", bio="drummer")
        b = _profile("b", bio="bassist")

        output = _run()

        assert set(fake_search.indexed) == {str(a.id), str(b.id)}
        assert "Indexed 2 profiles." in output

    def test_sends_the_same_payload_the_event_path_sends(
        self, fake_search: Any, django_capture_on_commit_callbacks: Any
    ) -> None:
        """
        Two routes to one index. If they composed payloads separately they could
        drift, and a profile would be indexed differently depending on whether a
        rebuild or a live save touched it last.
        """
        instrument = Instrument.objects.create(name="Cello", slug="cello")
        genre = Genre.objects.create(name="Baroque", slug="baroque")
        profile = _profile("c", bio="cellist", city="Kolkata", country="India")
        profile.musician_instruments.create(instrument=instrument, proficiency="advanced")
        profile.genres.add(genre)

        _run()
        from_rebuild = dict(fake_search.indexed[str(profile.id)])

        fake_search.indexed.clear()
        with django_capture_on_commit_callbacks(execute=True):
            from apps.musicians.services import update_profile

            update_profile(profile=profile, data={})
        from_event = dict(fake_search.indexed[str(profile.id)])

        # indexed_at is the writer's clock, so it legitimately differs.
        from_rebuild.pop("indexed_at")
        from_event.pop("indexed_at")
        assert from_rebuild == from_event

    def test_prune_is_opt_in(self, fake_search: Any) -> None:
        """
        A prune after a partial pass deletes everything the pass never reached,
        so it must be asked for rather than assumed.
        """
        _profile("a", bio="x")

        assert "Skipping prune" in _run()
        assert fake_search.delete_queries == []

        assert "Pruned" in _run("--prune")
        # Conflicts must be survivable: the rebuild rewrites every document
        # immediately before this sweep scans them, so "abort" would fail the
        # command as a matter of routine.
        assert fake_search.delete_queries[-1]["conflicts"] == "proceed"


@pytest.mark.django_db
class TestRefusesToLieAboutSuccess:
    def test_no_cluster_configured_is_an_error_not_a_no_op(self, settings: Any) -> None:
        """
        Everywhere else an empty OPENSEARCH_URL means "degrade quietly". Here it
        means the deploy hook is about to report a successful rebuild of an index
        it never wrote to — which is the invisible failure the hook exists to
        prevent, reproduced by the hook itself.
        """
        settings.OPENSEARCH_URL = ""
        _profile("a", bio="x")

        with pytest.raises(CommandError, match="OPENSEARCH_URL is empty"):
            _run()


@pytest.mark.django_db
class TestRebuildAgainstARealCluster:
    def test_rebuilds_an_empty_index(self, opensearch: Any) -> None:
        profile = _profile("real", bio="slide guitar and dusty blues")

        _run()
        opensearch.refresh()

        assert [h[0] for h in search_services.search(query="slide guitar")] == [profile.id]

    def test_prune_removes_a_document_whose_profile_is_gone(self, opensearch: Any) -> None:
        """
        The deletion path that no event covers. There is no delete endpoint, so
        profiles leave through admin, a cascade, or the demo seeder's --reset —
        none of which publish anything.
        """
        keep = _profile("keep", bio="trumpet player")
        orphan_id = uuid.uuid4()
        search_services.index_profile(
            profile_id=str(orphan_id),
            bio="ghost of a deleted profile",
            instruments=[],
            genres=[],
            city="",
            country="",
            is_available=True,
        )
        opensearch.refresh()
        assert search_services.search(query="ghost")  # present before the rebuild

        _run("--prune")
        opensearch.refresh()

        assert search_services.search(query="ghost") == []
        assert [h[0] for h in search_services.search(query="trumpet")] == [keep.id]

    def test_prune_keeps_documents_the_rebuild_just_wrote(self, opensearch: Any) -> None:
        """
        The failure mode worth guarding: a watermark comparison that is off by an
        equals sign deletes the entire index every time it runs.
        """
        _profile("a", bio="fiddle")
        _profile("b", bio="banjo")

        _run("--prune")
        opensearch.refresh()

        assert len(search_services.search(query="fiddle OR banjo")) >= 1
        assert search_services.search(query="fiddle")
        assert search_services.search(query="banjo")

    def test_prune_survives_rewriting_the_documents_it_is_about_to_scan(
        self, opensearch: Any
    ) -> None:
        """
        Regression: this crashed the command, and only showed up when run by
        hand against real data.

        delete_by_query searches, then deletes each hit by the version it saw.
        A rebuild rewrites every profile immediately beforehand, so the search
        can match a document from a not-yet-refreshed segment whose version has
        since moved on — and OpenSearch's default is to abort the whole
        operation with a 409. The sweep then takes the deploy down with it.

        The sequence below is the one that failed: index once, plant an orphan,
        rebuild again. The second rebuild rewrites the profile and then prunes.
        """
        _profile("a", bio="viola")
        _run("--prune")

        search_services.index_profile(
            profile_id=str(uuid.uuid4()),
            bio="ghost",
            instruments=[],
            genres=[],
            city="",
            country="",
            is_available=True,
        )
        opensearch.refresh()

        _run("--prune")  # must not raise
        opensearch.refresh()

        assert search_services.search(query="ghost") == []
        assert search_services.search(query="viola")

    def test_a_second_rebuild_is_idempotent(self, opensearch: Any) -> None:
        _profile("a", bio="oboe")

        _run("--prune")
        _run("--prune")
        opensearch.refresh()

        assert len(search_services.search(query="oboe")) == 1


@pytest.mark.django_db
class TestPruneService:
    def test_a_future_watermark_would_delete_everything(self, opensearch: Any) -> None:
        """
        Documents the sharp edge rather than hiding it: prune_stale is only safe
        after a complete pass, and this is what "unsafe" looks like.
        """
        _profile("a", bio="clarinet")
        _run()
        opensearch.refresh()

        deleted = search_services.prune_stale(older_than=timezone.now() + timedelta(hours=1))

        assert deleted == 1
        assert search_services.search(query="clarinet") == []
