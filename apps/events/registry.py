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
    # connections
    "contact_request.created": "connections.notify_new_contact_request",
    "contact_request.accepted": "connections.notify_contact_request_accepted",
    # engagements
    "engagement.requested": "engagements.notify_new_engagement_request",
    "engagement.accepted": "engagements.notify_engagement_request_accepted",
    # listings
    "listing.application_created": "listings.notify_new_application",
    "listing.application_accepted": "listings.notify_application_accepted",
    # bands
    "band.invite_created": "bands.notify_band_invite",
    "band.invite_accepted": "bands.notify_band_invite_accepted",
    # social
    "follow.created": "social.backfill_feed",
    "follow.removed": "social.prune_feed",
    "activity.recorded": "social.fan_out_activity",
    # musicians
    "profile.updated": "musicians.generate_profile_embedding",
    # reviews
    "review.created": "reviews.propagate_profile_rating",
}
