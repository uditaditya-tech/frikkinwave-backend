from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from apps.schema.views import GatedSchemaView, GatedSwaggerView
from apps.users.views import ThrottledTokenObtainPairView, ThrottledTokenRefreshView


def health_check(request):  # type: ignore[no-untyped-def]
    return JsonResponse({"status": "ok"})


urlpatterns = [
    # Internal health check — exempt from SECURE_SSL_REDIRECT in production
    path("api/health/", health_check, name="health-check"),
    # Admin — path is configurable via DJANGO_ADMIN_PATH; login is rate limited
    # by apps.users.middleware.AdminLoginThrottleMiddleware.
    path(settings.ADMIN_PATH, admin.site.urls),
    # Auth — specific paths before the simplejwt catch-alls
    path("api/auth/", include("apps.users.urls")),
    path("api/auth/token/", ThrottledTokenObtainPairView.as_view(), name="token-obtain-pair"),
    path("api/auth/token/refresh/", ThrottledTokenRefreshView.as_view(), name="token-refresh"),
    # Musicians
    path("api/musicians/", include("apps.musicians.urls")),
    # Connections
    path("api/connections/", include("apps.connections.urls")),
    # Listings — gig & audition board
    path("api/listings/", include("apps.listings.urls")),
    # Bands — group entities + member rosters
    path("api/bands/", include("apps.bands.urls")),
    # Engagements — session-musician hire-intent marketplace
    path("api/engagements/", include("apps.engagements.urls")),
    # Venues — user-owned venue profiles
    path("api/venues/", include("apps.venues.urls")),
    # Social — follow graph
    path("api/social/", include("apps.social.urls")),
    # Reviews — ratings + reviews (gated on completed engagements)
    path("api/reviews/", include("apps.reviews.urls")),
    # OpenAPI schema + docs. Public only where SCHEMA_IS_PUBLIC is set (local,
    # CI); staff-only in production. The gate is in apps/schema/views.py, not in
    # SPECTACULAR_SETTINGS — see that module for why.
    path("api/schema/", GatedSchemaView.as_view(), name="schema"),
    path("api/docs/", GatedSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
