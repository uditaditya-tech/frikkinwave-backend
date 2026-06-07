from django.urls import path

from apps.venues.views import VenueDetailView, VenueListCreateView

urlpatterns = [
    path("", VenueListCreateView.as_view(), name="venue-list-create"),
    path("<slug:slug>/", VenueDetailView.as_view(), name="venue-detail"),
]
