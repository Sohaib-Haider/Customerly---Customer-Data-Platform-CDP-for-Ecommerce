"""Production configuration; required deployment values must be supplied."""

from .base import *  # noqa: F403

if not SECRET_KEY or SECRET_KEY.startswith("django-insecure-"):  # noqa: F405
    raise ImproperlyConfigured("Set a private DJANGO_SECRET_KEY for production.")  # noqa: F405
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("Set explicit DJANGO_ALLOWED_HOSTS for production.")  # noqa: F405
if not DATABASE_URL:  # noqa: F405
    raise ImproperlyConfigured("Set DATABASE_URL for the shared PostgreSQL database.")  # noqa: F405

DEBUG = False
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
