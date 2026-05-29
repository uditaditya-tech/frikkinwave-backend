from django.urls import path

from apps.users.views import LogoutView, RegisterView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
]
