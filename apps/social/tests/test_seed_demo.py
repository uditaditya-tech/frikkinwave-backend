"""
Smoke test for the Phase 5 demo-seed command.

Runs a tiny seed and asserts it populates every Phase 5 surface and is re-runnable
via --reset.

Two test-only wrinkles (the command itself is built for prod, where neither
applies):
  - The command's feed fan-out is emitted via transaction.on_commit; under the
    pytest `db` transaction those callbacks only fire inside
    django_capture_on_commit_callbacks(execute=True). In prod there is no wrapping
    transaction, so they fire immediately.
  - The command swaps in the dummy email backend for its process; override_settings
    here restores EMAIL_BACKEND afterwards so the change can't leak into other tests.
Reference catalogues (instruments/genres) are absent here — the command must
tolerate empty catalogues (profiles still seed).
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.reviews.models import Review
from apps.social.models import Activity, FeedEntry, Follow
from apps.users.models import User

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


@pytest.mark.django_db
class TestSeedDemoPhase5:
    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_seed_populates_all_surfaces(self, django_capture_on_commit_callbacks: object) -> None:
        # 4 users, 1 forward neighbour → each reviewed by 2 others.
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            call_command("seed_demo_phase5", "--count", "4", "--neighbours", "1")

        demo_users = User.objects.filter(username__startswith="demo-")
        assert demo_users.count() == 4

        # Follow graph: everyone follows everyone else → 4 * 3 edges.
        assert Follow.objects.count() == 12

        # Activities (one listing each + bands on every 3rd) fanned into inboxes.
        assert Activity.objects.count() >= 4
        someone = demo_users.first()
        assert someone is not None
        assert FeedEntry.objects.filter(owner=someone).exists()

        # Bidirectional reviews: 4 users * 1 neighbour = 4 engagements * 2 = 8 reviews.
        assert Review.objects.count() == 8
        for u in demo_users:
            assert Review.objects.filter(subject=u).count() == 2

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_reseed_without_reset_errors(self) -> None:
        call_command("seed_demo_phase5", "--count", "3", "--neighbours", "1")
        with pytest.raises(CommandError):
            call_command("seed_demo_phase5", "--count", "3", "--neighbours", "1")

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_reset_wipes_and_reseeds(self) -> None:
        call_command("seed_demo_phase5", "--count", "3", "--neighbours", "1")
        first_ids = set(
            User.objects.filter(username__startswith="demo-").values_list("id", flat=True)
        )
        call_command("seed_demo_phase5", "--count", "3", "--neighbours", "1", "--reset")
        second_ids = set(
            User.objects.filter(username__startswith="demo-").values_list("id", flat=True)
        )
        assert len(second_ids) == 3
        assert first_ids.isdisjoint(second_ids)  # fresh rows, old ones gone
