"""
Topic -> consumer registry.

The relay dispatches **by task name**, never by importing a handler. Nothing here
imports another app, and the shape mirrors a real broker: a producer publishes to
a topic, and whoever subscribes to that topic handles it in its own process.

When services are extracted, this dict becomes Kafka consumer-group subscriptions
and the payloads become the message schemas — unchanged (MICROSERVICES.md §4).

A payload's keys are passed straight through as the handler's kwargs, so every
payload must match its handler's signature exactly.
"""

from __future__ import annotations

# topic -> Celery task name
EVENT_HANDLERS: dict[str, str] = {
    # notifications — extracted service (own queue, own Deployment).
    #
    # These topics used to point at notify_* tasks inside each producing app,
    # which took an id and re-read the row. The payloads now carry the facts the
    # email needs, so the consumer touches no models and no other app's code.
    "contact_request.created": "notifications.contact_request_created",
    "contact_request.accepted": "notifications.contact_request_accepted",
    "engagement.requested": "notifications.engagement_requested",
    "engagement.accepted": "notifications.engagement_accepted",
    "listing.application_created": "notifications.listing_application_created",
    "listing.application_accepted": "notifications.listing_application_accepted",
    "band.invite_created": "notifications.band_invite_created",
    "band.invite_accepted": "notifications.band_invite_accepted",
    # social
    "follow.created": "social.backfill_feed",
    "follow.removed": "social.prune_feed",
    "activity.recorded": "social.fan_out_activity",
    # search — extracted service (own queue, own Deployment).
    # The payload carries the composed embedding text and the availability flag,
    # so the consumer never reads a musicians table.
    "profile.updated": "search.index_profile",
    # reviews
    "review.created": "reviews.propagate_profile_rating",
}
