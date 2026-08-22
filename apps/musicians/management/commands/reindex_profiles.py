"""
Rebuild the OpenSearch profile index from Postgres.

**This is not a backfill you run once.** The index is derived data living
outside the database, and unlike RDS it has no snapshot that a rebuild restores
— tear the stack down and it comes back with a healthy, empty cluster. Search
then returns nothing while every health check stays green, which is the worst
shape a failure can take. So this belongs in the deploy path, not in a runbook
someone remembers.

It writes to the search service directly rather than publishing events. That is
the point: reconciliation must work when the event path is exactly what you
cannot trust. Publishing a `profile.updated` per profile would also mean the
rebuild only completes if the relay and the consumer group are both healthy, and
would put thousands of rows through the outbox to say things the index could
have been told directly.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.musicians.models import MusicianProfile
from apps.musicians.services import build_search_payload
from apps.search.services import index_profile, prune_stale

DEFAULT_BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Rebuild the search index from the profile tables. Use --prune to drop orphans."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--prune",
            action="store_true",
            help=(
                "After a complete pass, delete index documents this run did not "
                "write — profiles deleted straight from the database, which emit "
                "no event. Skipped automatically if the pass did not finish."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"Rows fetched per query (default {DEFAULT_BATCH_SIZE}).",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        prune: bool = opts["prune"]
        batch_size: int = opts["batch_size"]

        # An empty URL is a SUPPORTED state everywhere else: the request path
        # treats "no cluster" and "cluster down" alike and degrades to empty
        # results, and index_profile quietly no-ops. That is right for a user
        # request and wrong here.
        #
        # This command exists solely to write to the cluster, and it runs as a
        # deploy hook. Without this check a missing OPENSEARCH_URL would print
        # "Indexed 42 profiles.", exit 0, and hand back a green deploy over an
        # index that was never written — the exact invisible failure the hook
        # was added to prevent.
        if not settings.OPENSEARCH_URL:
            raise CommandError(
                "OPENSEARCH_URL is empty — there is no cluster to rebuild into. "
                "Refusing to report success over an index nothing was written to."
            )

        # Taken BEFORE the first write. Anything still carrying a stamp older
        # than this at the end is something the rebuild never saw.
        started = timezone.now()

        queryset = MusicianProfile.objects.prefetch_related(
            "musician_instruments__instrument",
            "genres",
        ).order_by("created_at")

        indexed = 0
        for profile in queryset.iterator(chunk_size=batch_size):
            index_profile(**build_search_payload(profile))
            indexed += 1
            if indexed % batch_size == 0:
                self.stdout.write(f"  indexed {indexed}...")

        self.stdout.write(self.style.SUCCESS(f"Indexed {indexed} profiles."))

        if not prune:
            self.stdout.write("Skipping prune (pass --prune to remove documents with no profile).")
            return

        # Only reached when the loop completed. A partial pass that raised would
        # have left this unrun, which is the whole reason it lives after the
        # loop rather than in a finally.
        deleted = prune_stale(older_than=started)
        self.stdout.write(self.style.SUCCESS(f"Pruned {deleted} orphaned documents."))
