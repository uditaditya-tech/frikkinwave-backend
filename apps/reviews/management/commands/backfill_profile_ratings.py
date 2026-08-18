"""
Rebuild the denormalized rating rollup on every musician profile.

The rollup is maintained incrementally by a post-commit Celery task, which makes
it *eventually* consistent. This command is the reconciliation path: run it after
a backfill, a lost task, a restore from snapshot, or any time you suspect drift.

Safe to run any time — it recomputes from the reviews tables and is idempotent.

    python manage.py backfill_profile_ratings
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.reviews.services import propagate_rating_to_profile


class Command(BaseCommand):
    help = "Recompute rating_avg / rating_count on all musician profiles from reviews."

    def handle(self, *args: Any, **opts: Any) -> None:
        # Import locally: this is reconciliation tooling, not a runtime dependency
        # of the reviews service.
        from apps.musicians.models import MusicianProfile

        user_ids = MusicianProfile.objects.values_list("user_id", flat=True)
        total = 0
        for user_id in user_ids:
            propagate_rating_to_profile(subject_user_id=str(user_id))
            total += 1
        self.stdout.write(self.style.SUCCESS(f"Rebuilt rating rollup for {total} profiles."))
