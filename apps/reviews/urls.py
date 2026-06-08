from django.urls import path

from apps.reviews.views import ReviewCreateView, ReviewListView, ReviewSummaryView

urlpatterns = [
    path("", ReviewCreateView.as_view(), name="review-create"),
    # Specific suffix before the <username> catch-all.
    path("<str:username>/summary/", ReviewSummaryView.as_view(), name="review-summary"),
    path("<str:username>/", ReviewListView.as_view(), name="review-list"),
]
