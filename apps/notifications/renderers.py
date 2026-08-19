"""
Notification copy.

Each renderer turns a **self-contained** set of primitive fields into a subject
and body. Nothing here touches the ORM, and nothing imports another app — that
is the whole point of the extraction: the producing app publishes the facts, and
this service owns how they are worded and delivered.

Two consequences worth stating:

* The email reflects the state at the moment the event was published, not
  whatever the row looks like when the worker gets around to it. That is more
  correct than re-reading: "X accepted your request" should describe the
  acceptance that happened, even if X changes their username a second later.
* Adding a channel (push, SMS, a digest) is a change here only. Producers do not
  learn about it, which is the property that makes this a real boundary rather
  than a relocated function call.

The `kind` strings match the event topics, so a topic can be traced to its copy
by grep.
"""

from __future__ import annotations

from collections.abc import Callable

Rendered = tuple[str, str]  # (subject, body)

_SIGNOFF = "\n\nLog in to frikkinwave to accept or decline."


def _contact_request_created(*, sender_username: str, message: str = "") -> Rendered:
    body = f"{sender_username} wants to connect with you on frikkinwave."
    if message:
        body += f'\n\nThey said:\n"{message}"'
    body += _SIGNOFF
    return f"{sender_username} wants to connect on frikkinwave", body


def _contact_request_accepted(*, accepter_username: str, accepter_email: str) -> Rendered:
    # Reveal-on-accept: the accepter's address is disclosed only once they have
    # agreed to the connection.
    body = (
        f"{accepter_username} accepted your contact request on frikkinwave.\n\n"
        f"You can now reach them at: {accepter_email}"
    )
    return f"{accepter_username} accepted your contact request", body


def _engagement_requested(
    *,
    requester_username: str,
    proposed_date: str = "",
    rate_offer: str = "",
    message: str = "",
) -> Rendered:
    # Every optional field defaults to empty and is omitted rather than rendered
    # as "None" — proposed_date and rate_offer are both nullable/blank upstream.
    body = f"{requester_username} wants to hire you for session work on frikkinwave."
    if proposed_date:
        body += f"\n\nProposed date: {proposed_date}"
    if rate_offer:
        body += f"\nRate offered: {rate_offer}"
    if message:
        body += f'\n\nThey said:\n"{message}"'
    body += _SIGNOFF
    return f"{requester_username} wants to hire you on frikkinwave", body


def _engagement_accepted(*, musician_username: str, musician_email: str) -> Rendered:
    body = (
        f"{musician_username} accepted your hire request on frikkinwave.\n\n"
        f"You can now reach them at: {musician_email}"
    )
    return f"{musician_username} accepted your hire request", body


def _listing_application_created(
    *, applicant_username: str, listing_title: str, message: str = ""
) -> Rendered:
    body = f'{applicant_username} applied to your listing "{listing_title}" on frikkinwave.'
    if message:
        body += f'\n\nThey said:\n"{message}"'
    body += _SIGNOFF
    return f'New application for "{listing_title}"', body


def _listing_application_accepted(
    *, author_username: str, author_email: str, listing_title: str
) -> Rendered:
    body = (
        f'{author_username} accepted your application to "{listing_title}" on frikkinwave.\n\n'
        f"You can now reach them at: {author_email}"
    )
    return f'Your application to "{listing_title}" was accepted', body


def _band_invite_created(*, owner_username: str, band_name: str, role: str) -> Rendered:
    body = f'{owner_username} invited you to join "{band_name}" on frikkinwave.'
    body += f"\n\nRole: {role}"
    body += _SIGNOFF
    return f'You\'re invited to join "{band_name}"', body


def _band_invite_accepted(*, member_username: str, band_name: str) -> Rendered:
    body = f'{member_username} accepted your invitation to join "{band_name}" on frikkinwave.'
    return f'{member_username} joined "{band_name}"', body


# kind -> renderer. Keys mirror the event topics in apps/events/registry.py.
RENDERERS: dict[str, Callable[..., Rendered]] = {
    "contact_request.created": _contact_request_created,
    "contact_request.accepted": _contact_request_accepted,
    "engagement.requested": _engagement_requested,
    "engagement.accepted": _engagement_accepted,
    "listing.application_created": _listing_application_created,
    "listing.application_accepted": _listing_application_accepted,
    "band.invite_created": _band_invite_created,
    "band.invite_accepted": _band_invite_accepted,
}
