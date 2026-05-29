from django.urls import path

from apps.musicians.views import (
    ProfileCreateView,
    ProfileListView,
    ProfileMeView,
    ProfilePublicView,
)

urlpatterns = [
    path("profiles/", ProfileListView.as_view(), name="profile-list"),
    path("profiles/<slug:username>/", ProfilePublicView.as_view(), name="profile-public"),
    path("profile/", ProfileCreateView.as_view(), name="profile-create"),
    path("profile/me/", ProfileMeView.as_view(), name="profile-me"),
]
