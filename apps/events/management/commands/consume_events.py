"""
Run a Kafka consumer group (KAFKA.md stage 4).

    python manage.py consume_events --group search

One process per group, one Deployment per process. This is what replaces
`celery -A config worker --queues=<queue>`.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.events.consumer import SubscriptionsNotFound, run


class Command(BaseCommand):
    help = "Consume events for one consumer group until terminated."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--group",
            required=True,
            help="Consumer group, which is also the app label declaring the "
            "subscriptions (e.g. 'search' -> apps/search/consumers.py).",
        )
        parser.add_argument(
            "--max-messages",
            type=int,
            default=None,
            help="Stop after N messages. For smoke-testing a deployment; the "
            "normal mode is to run until SIGTERM.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        group = options["group"]
        try:
            handled = run(group=group, max_messages=options["max_messages"])
        except SubscriptionsNotFound as exc:
            # A misspelled group would otherwise start, subscribe to nothing, and
            # sit there looking healthy forever.
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"consumed {handled} message(s)"))
