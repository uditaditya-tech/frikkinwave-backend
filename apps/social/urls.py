from django.urls import path

from apps.social.views import (
    FollowersListView,
    FollowingListView,
    FollowView,
    PublicFollowersView,
    PublicFollowingView,
)

urlpatterns = [
    # Specific literal paths before the <username> catch-alls.
    path("follow/<str:username>/", FollowView.as_view(), name="follow"),
    path("following/", FollowingListView.as_view(), name="following-list"),
    path("followers/", FollowersListView.as_view(), name="followers-list"),
    path("<str:username>/following/", PublicFollowingView.as_view(), name="public-following"),
    path("<str:username>/followers/", PublicFollowersView.as_view(), name="public-followers"),
]
