"""
Service layer for the bands app.

All business logic lives here. Views call services; services call models.
Cross-app access (username → user) goes through apps.users.services — never a
model import. apps.users.models.User is referenced only under TYPE_CHECKING.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError
from django.db.models import Q
from django.utils.text import slugify

from apps.bands.models import Band, BandMembership
from apps.users.services import get_user_ref

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)


class BandNotFoundError(Exception):
    """No band exists for the given slug, or it is not visible to the caller."""


class NotBandOwnerError(Exception):
    """A non-owner tried to mutate a band or its roster."""


class MemberNotFoundError(Exception):
    """No user exists for the given member username."""


class SelfInviteError(Exception):
    """An owner tried to invite themselves to their own band."""


class DuplicateMembershipError(Exception):
    """This user already has a membership row for this band."""


class NotPendingError(Exception):
    """An accept/decline was attempted on a membership that is not pending."""


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------


def create_band(*, owner: User, name: str, **fields: Any) -> Band:
    """Create a band owned by `owner`, deriving a unique slug from the name."""
    band = Band.objects.create(owner=owner, name=name, slug=_unique_slug(name), **fields)
    logger.info("band_created", extra={"band_id": str(band.id), "owner_id": str(owner.pk)})

    # Record a feed activity (cross-app service call, never a model import).
    from apps.social.services import Verb, record_activity

    record_activity(
        actor=owner,
        verb=Verb.CREATED_BAND,
        summary=band.name,
        target_type="band",
        target_id=str(band.id),
        target_slug=band.slug,
    )
    return band


def get_band(*, slug: str) -> Band | None:
    """Return a single active band by slug, or None."""
    return Band.objects.select_related("owner").filter(slug=slug, is_active=True).first()


def list_bands(
    *,
    city: str | None = None,
    country: str | None = None,
) -> QuerySet[Band]:
    """Return active bands, optionally filtered by city / country."""
    queryset = Band.objects.select_related("owner").filter(is_active=True)
    if city:
        queryset = queryset.filter(city__iexact=city)
    if country:
        queryset = queryset.filter(country__iexact=country)
    return queryset


def list_band_members(*, band: Band) -> QuerySet[BandMembership]:
    """Return the band's accepted member roster."""
    return (
        BandMembership.objects.select_related("member")
        .filter(band=band, status=BandMembership.Status.ACCEPTED)
        .order_by("created_at")
    )


def update_band(*, owner: User, slug: str, **fields: Any) -> Band:
    """
    Partially update a band. Only the owner may update it.

    Raises BandNotFoundError / NotBandOwnerError.
    """
    band = _get_owned_band(owner=owner, slug=slug)
    for name, value in fields.items():
        setattr(band, name, value)
    band.save(update_fields=[*fields.keys(), "updated_at"])
    logger.info("band_updated", extra={"band_id": str(band.id)})
    return band


def deactivate_band(*, owner: User, slug: str) -> None:
    """Soft-delete a band (is_active=False). Only the owner may do so."""
    band = _get_owned_band(owner=owner, slug=slug)
    band.is_active = False
    band.save(update_fields=["is_active", "updated_at"])
    logger.info("band_deactivated", extra={"band_id": str(band.id)})


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------


def invite_member(
    *, owner: User, slug: str, member_username: str, role: str = ""
) -> BandMembership:
    """
    Invite a user to the band (owner only). Creates a pending membership.

    Raises:
        BandNotFoundError — no active band with that slug.
        NotBandOwnerError — caller does not own the band.
        MemberNotFoundError — no such user.
        SelfInviteError — owner tried to invite themselves.
        DuplicateMembershipError — that user already has a membership row.
    """
    band = _get_owned_band(owner=owner, slug=slug)

    member = get_user_ref(username=member_username)
    if member is None:
        raise MemberNotFoundError
    if member.id == owner.pk:
        raise SelfInviteError

    try:
        # FK assigned by id — `member` is a DTO, not an ORM instance.
        membership = BandMembership.objects.create(band=band, member_id=member.id, role=role)
    except IntegrityError as exc:
        raise DuplicateMembershipError from exc

    # Emit once the row commits, so a rolled-back transaction never enqueues a
    # task pointing at a phantom row. Local import avoids a tasks ↔ services cycle.
    from apps.events.services import publish

    # Self-contained payload: the consumer is a separate service and must not
    # need a database read to send this.
    publish(
        topic="band.invite_created",
        payload={
            "recipient_email": member.email,
            "owner_username": owner.username,
            "band_name": band.name,
            "role": membership.role,
        },
    )

    logger.info(
        "band_invite_created",
        extra={"membership_id": str(membership.id), "band_id": str(band.id)},
    )
    return membership


def list_memberships(*, user: User) -> QuerySet[BandMembership]:
    """Return all membership rows where `user` is the member (their invites + bands)."""
    return BandMembership.objects.select_related("band", "band__owner", "member").filter(
        member=user
    )


def get_membership(*, user: User, membership_id: str) -> BandMembership | None:
    """
    Return a membership if `user` is a party to it (the member or the band owner),
    else None — so its existence is never leaked to outsiders.
    """
    return (
        BandMembership.objects.select_related("band", "band__owner", "member")
        .filter(id=membership_id)
        .filter(Q(member=user) | Q(band__owner=user))
        .first()
    )


def accept_membership(*, user: User, membership_id: str) -> BandMembership | None:
    """Accept a pending invite. Only the invited member may accept."""
    return _resolve_membership(
        user=user, membership_id=membership_id, new_status=BandMembership.Status.ACCEPTED
    )


def decline_membership(*, user: User, membership_id: str) -> BandMembership | None:
    """Decline a pending invite. Only the invited member may decline."""
    return _resolve_membership(
        user=user, membership_id=membership_id, new_status=BandMembership.Status.DECLINED
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_owned_band(*, owner: User, slug: str) -> Band:
    band = Band.objects.filter(slug=slug, is_active=True).first()
    if band is None:
        raise BandNotFoundError
    if band.owner_id != owner.pk:
        raise NotBandOwnerError
    return band


def _unique_slug(name: str) -> str:
    """Slugify `name` and disambiguate with a numeric suffix if already taken."""
    base = slugify(name) or "band"
    base = base[:120]
    candidate = base
    suffix = 2
    while Band.objects.filter(slug=candidate).exists():
        tail = f"-{suffix}"
        candidate = f"{base[: 120 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def _resolve_membership(
    *, user: User, membership_id: str, new_status: str
) -> BandMembership | None:
    membership = (
        BandMembership.objects.select_related("band", "band__owner", "member")
        .filter(id=membership_id, member=user)
        .first()
    )
    if membership is None:
        return None
    if membership.status != BandMembership.Status.PENDING:
        raise NotPendingError
    membership.status = new_status
    membership.save(update_fields=["status", "updated_at"])

    # Notify the owner only when the invite is accepted (decline is silent).
    if new_status == BandMembership.Status.ACCEPTED:
        from apps.events.services import publish

        publish(
            topic="band.invite_accepted",
            payload={
                "recipient_email": membership.band.owner.email,
                "member_username": membership.member.username,
                "band_name": membership.band.name,
            },
        )

    logger.info(
        "band_membership_resolved",
        extra={"membership_id": str(membership.id), "status": new_status},
    )
    return membership
