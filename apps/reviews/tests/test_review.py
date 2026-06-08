"""Tests for ratings + reviews — gated on a completed engagement."""

import pytest
from rest_framework.test import APIClient

from apps.engagements.models import EngagementRequest
from apps.reviews.models import Review
from apps.reviews.tests.conftest import auth, make_engagement, make_user
from apps.users.models import User


@pytest.mark.django_db
class TestCreateReview:
    def test_requester_reviews_musician(
        self, api_client: APIClient, requester: User, musician: User
    ) -> None:
        engagement = make_engagement(requester, musician)
        client = auth(api_client, requester)
        resp = client.post(
            "/api/reviews/",
            {
                "subject_username": musician.username,
                "engagement_id": str(engagement.id),
                "rating": 5,
                "comment": "Killer session player.",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["rating"] == 5
        assert resp.data["subject_username"] == musician.username
        assert Review.objects.filter(author=requester, subject=musician).exists()

    def test_review_is_bidirectional(
        self, api_client: APIClient, requester: User, musician: User
    ) -> None:
        # Both parties can review each other for the same completed engagement.
        engagement = make_engagement(requester, musician)
        auth(api_client, musician)
        resp = api_client.post(
            "/api/reviews/",
            {
                "subject_username": requester.username,
                "engagement_id": str(engagement.id),
                "rating": 4,
                "comment": "Clear brief, paid on time.",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert Review.objects.filter(author=musician, subject=requester).exists()

    def test_no_engagement_is_forbidden(
        self, api_client: APIClient, requester: User, musician: User
    ) -> None:
        client = auth(api_client, requester)
        resp = client.post(
            "/api/reviews/",
            {
                "subject_username": musician.username,
                "engagement_id": "00000000-0000-0000-0000-000000000000",
                "rating": 5,
            },
            format="json",
        )
        assert resp.status_code == 403
        assert not Review.objects.exists()

    def test_uncompleted_engagement_is_forbidden(
        self, api_client: APIClient, requester: User, musician: User
    ) -> None:
        engagement = make_engagement(requester, musician, status=EngagementRequest.Status.ACCEPTED)
        client = auth(api_client, requester)
        resp = client.post(
            "/api/reviews/",
            {
                "subject_username": musician.username,
                "engagement_id": str(engagement.id),
                "rating": 5,
            },
            format="json",
        )
        assert resp.status_code == 403

    def test_third_party_cannot_be_reviewed_via_others_engagement(
        self, api_client: APIClient, requester: User, musician: User
    ) -> None:
        # requester completed with musician, but tries to review an unrelated user.
        engagement = make_engagement(requester, musician)
        stranger = make_user("stranger")
        client = auth(api_client, requester)
        resp = client.post(
            "/api/reviews/",
            {
                "subject_username": stranger.username,
                "engagement_id": str(engagement.id),
                "rating": 1,
            },
            format="json",
        )
        assert resp.status_code == 403

    def test_duplicate_review_conflicts(
        self, api_client: APIClient, requester: User, musician: User
    ) -> None:
        engagement = make_engagement(requester, musician)
        client = auth(api_client, requester)
        body = {
            "subject_username": musician.username,
            "engagement_id": str(engagement.id),
            "rating": 5,
        }
        assert client.post("/api/reviews/", body, format="json").status_code == 201
        assert client.post("/api/reviews/", body, format="json").status_code == 409

    def test_subject_not_found(self, api_client: APIClient, requester: User) -> None:
        client = auth(api_client, requester)
        resp = client.post(
            "/api/reviews/",
            {
                "subject_username": "nobody",
                "engagement_id": "00000000-0000-0000-0000-000000000000",
                "rating": 5,
            },
            format="json",
        )
        assert resp.status_code == 404

    @pytest.mark.parametrize("bad_rating", [0, 6, -1])
    def test_rating_out_of_range_is_400(
        self, api_client: APIClient, requester: User, musician: User, bad_rating: int
    ) -> None:
        engagement = make_engagement(requester, musician)
        client = auth(api_client, requester)
        resp = client.post(
            "/api/reviews/",
            {
                "subject_username": musician.username,
                "engagement_id": str(engagement.id),
                "rating": bad_rating,
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_create_requires_auth(self, api_client: APIClient) -> None:
        resp = api_client.post("/api/reviews/", {}, format="json")
        assert resp.status_code == 401


@pytest.mark.django_db
class TestReadReviews:
    def test_public_list_of_received_reviews(
        self, api_client: APIClient, requester: User, musician: User
    ) -> None:
        engagement = make_engagement(requester, musician)
        Review.objects.create(
            author=requester,
            subject=musician,
            rating=5,
            context_type=Review.Context.ENGAGEMENT,
            context_id=engagement.id,
        )
        resp = api_client.get(f"/api/reviews/{musician.username}/")
        assert resp.status_code == 200
        assert len(resp.data["results"]) == 1
        assert resp.data["results"][0]["author_username"] == requester.username

    def test_summary_average_and_count(
        self, api_client: APIClient, requester: User, musician: User
    ) -> None:
        e1 = make_engagement(requester, musician)
        other = make_user("other")
        e2 = make_engagement(other, musician)
        Review.objects.create(
            author=requester,
            subject=musician,
            rating=5,
            context_type=Review.Context.ENGAGEMENT,
            context_id=e1.id,
        )
        Review.objects.create(
            author=other,
            subject=musician,
            rating=2,
            context_type=Review.Context.ENGAGEMENT,
            context_id=e2.id,
        )
        resp = api_client.get(f"/api/reviews/{musician.username}/summary/")
        assert resp.status_code == 200
        assert resp.data["count"] == 2
        assert resp.data["average_rating"] == 3.5

    def test_summary_empty(self, api_client: APIClient, musician: User) -> None:
        resp = api_client.get(f"/api/reviews/{musician.username}/summary/")
        assert resp.status_code == 200
        assert resp.data == {"average_rating": None, "count": 0}

    def test_read_unknown_user_404(self, api_client: APIClient) -> None:
        assert api_client.get("/api/reviews/nobody/").status_code == 404
        assert api_client.get("/api/reviews/nobody/summary/").status_code == 404
