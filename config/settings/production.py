"""
Production settings (AWS EKS / Kubernetes).
All secrets come from environment variables — never hardcoded here.
"""

import os

from .base import *
from .base import env

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# Health checks reach the container using its own private IP as the Host header,
# which isn't in ALLOWED_HOSTS — Django would 400 every one of them. The pod IP
# is injected as POD_IP via the downward API (see the Helm chart's Deployments).
#
# This covers BOTH the ALB target-group health check (the load balancer
# controller uses ip-mode targets, so it hits the pod directly) and the kubelet
# liveness/readiness probes, which also use the pod IP as Host. Without it, no
# pod ever reaches Ready.
#
# Fails open: a missing value never blocks startup.
_pod_ip = os.environ.get("POD_IP")
if _pod_ip:
    ALLOWED_HOSTS += [_pod_ip]

# HTTPS enforcement
# The ALB terminates TLS and forwards over HTTP with X-Forwarded-Proto set.
# Trust that header so request.is_secure() is True for real HTTPS traffic —
# without this, SECURE_SSL_REDIRECT would loop forever behind the load balancer.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]  # ALB + kubelet health checks
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")

# Email (configure SMTP via env in production)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="smtp.sendgrid.net")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = True
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@frikkinwave.com")
