from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


def health_check(request):  # type: ignore[no-untyped-def]
    return JsonResponse({"status": "ok"})


urlpatterns = [
    # Internal health check — exempt from SECURE_SSL_REDIRECT in production
    path("api/health/", health_check, name="health-check"),
    # Admin
    path("admin/", admin.site.urls),
    # Auth — specific paths before the simplejwt catch-alls
    path("api/auth/", include("apps.users.urls")),
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token-obtain-pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    # Musicians
    path("api/musicians/", include("apps.musicians.urls")),
    # Connections
    path("api/connections/", include("apps.connections.urls")),
    # Listings — gig & audition board
    path("api/listings/", include("apps.listings.urls")),
    # OpenAPI schema + docs (local / staging only — gated in production via SPECTACULAR_SETTINGS)
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
