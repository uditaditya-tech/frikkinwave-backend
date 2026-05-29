from django.urls import path

from apps.musicians.views import ProfileCreateView, ProfileListView, ProfileMeView

urlpatterns = [
    path("profiles/", ProfileListView.as_view(), name="profile-list"),
    path("profile/", ProfileCreateView.as_view(), name="profile-create"),
    path("profile/me/", ProfileMeView.as_view(), name="profile-me"),
]
