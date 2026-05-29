from django.urls import path

from apps.connections.views import (
    ContactRequestAcceptView,
    ContactRequestDeclineView,
    ContactRequestDetailView,
    ContactRequestListCreateView,
)

urlpatterns = [
    path("requests/", ContactRequestListCreateView.as_view(), name="contact-request-list-create"),
    path(
        "requests/<uuid:request_id>/accept/",
        ContactRequestAcceptView.as_view(),
        name="contact-request-accept",
    ),
    path(
        "requests/<uuid:request_id>/decline/",
        ContactRequestDeclineView.as_view(),
        name="contact-request-decline",
    ),
    path(
        "requests/<uuid:request_id>/",
        ContactRequestDetailView.as_view(),
        name="contact-request-detail",
    ),
]
