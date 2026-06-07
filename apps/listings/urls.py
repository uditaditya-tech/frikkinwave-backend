from django.urls import path

from apps.listings.views import (
    ListingApplicationAcceptView,
    ListingApplicationDeclineView,
    ListingApplicationDetailView,
    ListingApplicationListView,
    ListingApplyView,
    ListingDetailView,
    ListingListCreateView,
)

urlpatterns = [
    path("", ListingListCreateView.as_view(), name="listing-list-create"),
    # Applications — specific string paths before the <uuid:listing_id> catch-all.
    path("applications/", ListingApplicationListView.as_view(), name="listing-application-list"),
    path(
        "applications/<uuid:application_id>/accept/",
        ListingApplicationAcceptView.as_view(),
        name="listing-application-accept",
    ),
    path(
        "applications/<uuid:application_id>/decline/",
        ListingApplicationDeclineView.as_view(),
        name="listing-application-decline",
    ),
    path(
        "applications/<uuid:application_id>/",
        ListingApplicationDetailView.as_view(),
        name="listing-application-detail",
    ),
    path("<uuid:listing_id>/apply/", ListingApplyView.as_view(), name="listing-apply"),
    path("<uuid:listing_id>/", ListingDetailView.as_view(), name="listing-detail"),
]
