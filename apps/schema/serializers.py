"""
Serializers that exist only to describe responses to OpenAPI.

A PLATFORM package — no models, not in INSTALLED_APPS, the same category as
`apps/events`. It is shared across every domain app, which is allowed precisely
because it carries no domain concepts: a `{"detail": ...}` body and a pagination
envelope belong to the HTTP layer, not to bands or listings.

**Nothing here is ever instantiated by a view.** Every endpoint in this project
is a plain `APIView` that builds its own response, so drf-spectacular has no
serializer to infer from and publishes the path with no body at all — an
endpoint a generated client can call and cannot read. These give it something
accurate to publish.

The cost is that the description and the behaviour are only as aligned as
someone kept them. A declaration that drifts from reality is worse than no
declaration, because a client trusts the schema. Keep them honest.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from rest_framework import serializers


class DetailSerializer(serializers.Serializer[Any]):
    """
    The error body every endpoint in this project returns: `{"detail": "..."}`.

    One shape, seven or so messages. DRF's own exception handler emits the same
    key, so this covers both the hand-written 404s and the framework's 401/403.
    """

    detail = serializers.CharField(help_text="Human-readable explanation of the failure.")


@cache
def cursor_paginated(
    item_serializer: type[serializers.BaseSerializer[Any]],
) -> type[serializers.Serializer[Any]]:
    """
    Wrap an item serializer in this project's cursor-pagination envelope.

    Declaring `XSerializer(many=True)` for a paginated list would be a **lie**:
    the client receives `{"next": ..., "previous": ..., "results": [...]}`, and a
    generated client built against a bare array fails on the first response.

    Note there is no `count`. `CursorPagination` does not compute one — that is
    PageNumberPagination — and inventing the field would be the same lie in a
    subtler form.

    Cached so the same item serializer always yields the same class. Without it
    every call site would mint a distinct type and drf-spectacular would emit
    near-duplicate components (`BandCursorPage`, `BandCursorPage2`, ...).
    """
    name = f"{item_serializer.__name__.removesuffix('Serializer')}CursorPage"
    return type(
        name,
        (serializers.Serializer,),
        {
            "next": serializers.URLField(
                allow_null=True,
                help_text="Cursor URL for the next page, or null on the last page.",
            ),
            "previous": serializers.URLField(
                allow_null=True,
                help_text="Cursor URL for the previous page, or null on the first.",
            ),
            "results": item_serializer(many=True),
        },
    )
