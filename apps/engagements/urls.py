from django.urls import path

from apps.engagements.views import (
    EngagementAcceptView,
    EngagementCompleteView,
    EngagementDeclineView,
    EngagementDetailView,
    EngagementListCreateView,
)

urlpatterns = [
    path("", EngagementListCreateView.as_view(), name="engagement-list-create"),
    path(
        "<uuid:engagement_id>/accept/",
        EngagementAcceptView.as_view(),
        name="engagement-accept",
    ),
    path(
        "<uuid:engagement_id>/decline/",
        EngagementDeclineView.as_view(),
        name="engagement-decline",
    ),
    path(
        "<uuid:engagement_id>/complete/",
        EngagementCompleteView.as_view(),
        name="engagement-complete",
    ),
    path("<uuid:engagement_id>/", EngagementDetailView.as_view(), name="engagement-detail"),
]
