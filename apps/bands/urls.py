from django.urls import path

from apps.bands.views import (
    BandDetailView,
    BandInviteView,
    BandListCreateView,
    BandMembershipAcceptView,
    BandMembershipDeclineView,
    BandMembershipDetailView,
    BandMembershipListView,
)

urlpatterns = [
    path("", BandListCreateView.as_view(), name="band-list-create"),
    # Memberships — specific paths before the <slug> catch-all.
    path("memberships/", BandMembershipListView.as_view(), name="band-membership-list"),
    path(
        "memberships/<uuid:membership_id>/accept/",
        BandMembershipAcceptView.as_view(),
        name="band-membership-accept",
    ),
    path(
        "memberships/<uuid:membership_id>/decline/",
        BandMembershipDeclineView.as_view(),
        name="band-membership-decline",
    ),
    path(
        "memberships/<uuid:membership_id>/",
        BandMembershipDetailView.as_view(),
        name="band-membership-detail",
    ),
    path("<slug:slug>/invite/", BandInviteView.as_view(), name="band-invite"),
    path("<slug:slug>/", BandDetailView.as_view(), name="band-detail"),
]
