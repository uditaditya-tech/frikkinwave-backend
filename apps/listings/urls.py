from django.urls import path

from apps.listings.views import ListingDetailView, ListingListCreateView

urlpatterns = [
    path("", ListingListCreateView.as_view(), name="listing-list-create"),
    path("<uuid:listing_id>/", ListingDetailView.as_view(), name="listing-detail"),
]
