from django.urls import path

from apps.musicians.views import (
    CompatibilityView,
    GenreListView,
    InstrumentListView,
    ProfileCoachView,
    ProfileCreateView,
    ProfileListView,
    ProfileMeView,
    ProfilePublicView,
    ProfileSearchView,
)

urlpatterns = [
    path("instruments/", InstrumentListView.as_view(), name="instrument-list"),
    path("genres/", GenreListView.as_view(), name="genre-list"),
    path("search/", ProfileSearchView.as_view(), name="profile-search"),
    path("compatibility/<slug:username>/", CompatibilityView.as_view(), name="compatibility"),
    path("profiles/", ProfileListView.as_view(), name="profile-list"),
    path("profiles/<slug:username>/", ProfilePublicView.as_view(), name="profile-public"),
    path("profile/", ProfileCreateView.as_view(), name="profile-create"),
    path("profile/coach/", ProfileCoachView.as_view(), name="profile-coach"),
    path("profile/me/", ProfileMeView.as_view(), name="profile-me"),
]
