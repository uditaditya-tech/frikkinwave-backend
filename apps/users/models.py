"""
Custom User model for frikkinwave.

Design decisions:
- UUIDv7 primary key: time-ordered, index-friendly, safe to expose in URLs.
- Email as the login identifier (USERNAME_FIELD = "email").
- Username is a URL-safe slug (used in profile URLs, e.g. /u/slayer-riffs).
- AbstractBaseUser + PermissionsMixin gives full control without forking Django internals.
"""

import uuid

import uuid6
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


def _new_uuid() -> uuid.UUID:
    """
    Generate a UUIDv7 (time-ordered, index-friendly).
    Uses the uuid6 backport — upgrade to stdlib uuid.uuid7() when on Python 3.14+.
    """
    return uuid6.uuid7()


class UserManager(BaseUserManager["User"]):
    def create_user(
        self,
        email: str,
        username: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> "User":
        if not email:
            raise ValueError("Email is required.")
        if not username:
            raise ValueError("Username is required.")
        email = self.normalize_email(email)
        user: User = self.model(email=email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        username: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, username, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    email = models.EmailField(unique=True)
    username = models.SlugField(max_length=50, unique=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    objects: UserManager = UserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.email
