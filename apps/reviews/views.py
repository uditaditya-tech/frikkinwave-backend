"""
Views for the reviews app.

HTTP shell only: parse request → call service → return Response.
"""

import logging
from typing import Any, cast

from rest_framework import status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.reviews.serializers import ReviewCreateSerializer, ReviewReadSerializer
from apps.reviews.services import (
    DuplicateReviewError,
    NotEligibleError,
    SubjectNotFoundError,
    create_review,
    get_user_or_raise,
    list_reviews_for,
    rating_summary,
)
from apps.users.models import User

logger = logging.getLogger(__name__)


def _subject_not_found() -> Response:
    return Response(
        {"detail": "No user found with that username."}, status=status.HTTP_404_NOT_FOUND
    )


class ReviewCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-created_at"


class ReviewCreateView(APIView):
    """POST /api/reviews/ — leave a review (gated on a completed engagement)."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            review = create_review(
                author=cast(User, request.user),
                subject_username=serializer.validated_data["subject_username"],
                engagement_id=str(serializer.validated_data["engagement_id"]),
                rating=serializer.validated_data["rating"],
                comment=serializer.validated_data.get("comment", ""),
            )
        except SubjectNotFoundError:
            return _subject_not_found()
        except NotEligibleError:
            return Response(
                {"detail": "You can only review someone after a completed engagement with them."},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DuplicateReviewError:
            return Response(
                {"detail": "You have already reviewed this engagement."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(ReviewReadSerializer(review).data, status=status.HTTP_201_CREATED)


class ReviewListView(APIView):
    """GET /api/reviews/<username>/ — public list of reviews a user received."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, username: str, *args: Any, **kwargs: Any) -> Response:
        try:
            subject = get_user_or_raise(username=username)
        except SubjectNotFoundError:
            return _subject_not_found()
        queryset = list_reviews_for(subject=subject)
        paginator = ReviewCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(ReviewReadSerializer(page, many=True).data)


class ReviewSummaryView(APIView):
    """GET /api/reviews/<username>/summary/ — public {average_rating, count}."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request: Request, username: str, *args: Any, **kwargs: Any) -> Response:
        try:
            subject = get_user_or_raise(username=username)
        except SubjectNotFoundError:
            return _subject_not_found()
        return Response(rating_summary(subject=subject))
