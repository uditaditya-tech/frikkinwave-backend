"""
Outbox relay — the component that makes delivery guaranteed.

    python manage.py relay_outbox              # one pass, then exit
    python manage.py relay_outbox --loop       # run forever (the Deployment)

`--loop` is what replaces the Celery nudge. `publish()` no longer dispatches
anything: a synchronous Kafka produce inside the request would add up to
KAFKA_FLUSH_TIMEOUT to every user-facing request during a broker outage. A
dedicated process polling the outbox gets sub-second latency with none of that
in the request path.

The single-pass mode remains for running it by hand. In `--loop` mode the
relay also serves Prometheus gauges for its own backlog on
EVENT_RELAY_METRICS_PORT — a stalled relay is invisible to every Kafka-side
alert, because nothing reaches the broker to be lagged on.
"""

from __future__ import annotations

import logging
import pathlib
import signal
import time
from types import FrameType
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from prometheus_client import Gauge, start_http_server

from apps.events.services import DEFAULT_BATCH, outbox_lag_snapshot, relay_pending

logger = logging.getLogger(__name__)

# Module-level by necessity: prometheus_client registers each metric in a global
# registry, so building these inside handle() would raise Duplicated timeseries
# on any second call (which the tests do make).
OUTBOX_OLDEST = Gauge(
    "frikkinwave_outbox_oldest_seconds",
    "Age of the oldest unpublished, still-retryable outbox event.",
)
OUTBOX_PENDING = Gauge(
    "frikkinwave_outbox_pending",
    "Unpublished outbox events, including exhausted ones.",
)
OUTBOX_EXHAUSTED = Gauge(
    "frikkinwave_outbox_exhausted",
    "Events past MAX_ATTEMPTS. These never retry and need a human.",
)


def _touch_heartbeat() -> None:
    """
    Record that the poll loop is still turning.

    This is the ONLY thing in the codebase that writes to local disk, and it is
    a deliberate exception to the stateless rule in CLAUDE.md rather than a
    breach of it. That rule exists so no *state* lives on a pod that can be
    killed at any moment. This file is the opposite: it is liveness evidence
    that MUST die with the pod, is never read by anything but this pod's own
    kubelet probe, and carries no data.

    The alternative — an HTTP health server inside the relay — means running a
    web server in a process whose entire job is a database poll.

    Failures are swallowed. A relay that cannot write to /tmp should keep
    relaying events; the probe failing and restarting it is the correct outcome,
    not an exception thrown out of the loop.
    """
    try:
        pathlib.Path(settings.EVENT_RELAY_HEARTBEAT_FILE).touch()
    except OSError:  # pragma: no cover - defensive
        logger.warning("relay_heartbeat_write_failed")


def _publish_metrics() -> None:
    """
    Export the outbox lag reading as gauges.

    Same query `check_outbox_lag` runs, via the same service function — but as a
    metric rather than a Job exit code, so it is graphable, alertable, and does
    not depend on a CronJob whose failures nobody watches.

    Failures are swallowed for the same reason the heartbeat's are: a relay that
    cannot measure itself must keep relaying events. Losing the metric costs a
    stale gauge; raising here would cost delivery.
    """
    try:
        snapshot = outbox_lag_snapshot()
    except Exception:  # pragma: no cover - defensive
        logger.warning("relay_metrics_snapshot_failed")
        return

    OUTBOX_OLDEST.set(snapshot.oldest_age_seconds)
    OUTBOX_PENDING.set(snapshot.pending)
    OUTBOX_EXHAUSTED.set(snapshot.exhausted)


class Command(BaseCommand):
    help = "Dispatch pending transactional-outbox events to Kafka."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--limit", type=int, default=DEFAULT_BATCH)
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Poll continuously instead of running one pass. This is the "
            "production mode; see the relay Deployment in the Helm chart.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=None,
            help="Seconds between polls when idle (default: EVENT_RELAY_INTERVAL).",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        if not opts["loop"]:
            count = relay_pending(limit=opts["limit"])
            self.stdout.write(self.style.SUCCESS(f"Relayed {count} event(s)."))
            return

        interval = opts["interval"] or settings.EVENT_RELAY_INTERVAL
        stopping = {"now": False}

        def _stop(signum: int, frame: FrameType | None) -> None:
            # Kubernetes sends SIGTERM on every rollout. Finish the batch in
            # flight rather than abandoning events mid-dispatch; they would be
            # redelivered, but redelivery is a cost, not a freebie.
            logger.info("relay_shutdown_requested", extra={"signal": signum})
            stopping["now"] = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        # Loop mode only. The single-pass mode runs by hand and in Jobs, where
        # binding a port would be a failure for no benefit — and two relays run
        # on one host would collide on it.
        port = settings.EVENT_RELAY_METRICS_PORT
        start_http_server(port)

        logger.info("relay_started", extra={"interval": interval, "metrics_port": port})
        while not stopping["now"]:
            # Touch the heartbeat BEFORE the work, so the file tracks "the loop
            # is turning" rather than "the last pass succeeded" — a relay that
            # is up but failing every produce is a different problem, and the
            # outbox-lag check is what catches that one.
            _touch_heartbeat()
            # Before the work, like the heartbeat: the gauges then describe the
            # backlog the pass is about to attack, so a scrape landing mid-pass
            # never reports a drained outbox that is still draining.
            _publish_metrics()
            try:
                relayed = relay_pending(limit=opts["limit"])
            except Exception:
                # Never let the loop die. A database blip or a broker outage is
                # exactly when the relay is most needed, and a crashed process
                # would stop delivering entirely until Kubernetes restarted it.
                logger.exception("relay_pass_failed")
                relayed = 0

            # Sleep only when there was nothing to do. A full batch means more
            # is waiting, so go straight round again rather than rate-limiting
            # the drain of a backlog.
            if relayed < opts["limit"]:
                time.sleep(interval)

        logger.info("relay_stopped")
