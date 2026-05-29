from django.urls import path

from apps.musicians.views import ProfileCreateView, ProfileMeView

urlpatterns = [
    path("profile/", ProfileCreateView.as_view(), name="profile-create"),
    path("profile/me/", ProfileMeView.as_view(), name="profile-me"),
]
