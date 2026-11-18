"""Shared backend settings. No database connection is opened during import."""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env", override=False)


def env_list(name, default=""):
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.mapping.apps.MappingConfig",
    "apps.preprocessing.apps.PreprocessingConfig",
    "apps.segments.apps.SegmentsConfig",
    "apps.analytics.apps.AnalyticsConfig",
    "apps.ingestion.apps.IngestionConfig",
    "apps.users.apps.UsersConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL:
    try:
        database = dj_database_url.parse(DATABASE_URL, conn_max_age=0)
    except (ValueError, KeyError):
        raise ImproperlyConfigured("DATABASE_URL must be a valid PostgreSQL connection URL.") from None
    if database["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("DATABASE_URL must use PostgreSQL for the shared database.")
    database.setdefault("OPTIONS", {}).setdefault("sslmode", "require")
    database["OPTIONS"].setdefault("connect_timeout", 10)
    DATABASES = {"default": database}
else:
    # Allows checks and HTTP startup without silently creating a local database.
    DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
INGESTION_PRIVATE_ROOT = Path(os.getenv("INGESTION_PRIVATE_ROOT", "").strip() or BASE_DIR / "private_uploads")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Domain APIs will define their authorization in a later implementation step.
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # Sign-up / sign-in / reset endpoints, per client IP.
    "DEFAULT_THROTTLE_RATES": {"auth": os.getenv("AUTH_THROTTLE_RATE", "30/minute")},
}

# Accounts (apps.users). Google sign-in uses an OAuth "Web application" client id
# from Google Cloud Console (a public value); leave empty to offer only passwords.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
AUTH_COOKIE_SECURE = True  # the stay-signed-in cookie is HTTPS-only outside local development

# Password-reset emails. Links point at the frontend.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:5173").strip()
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Customerly <no-reply@customerly.local>")
if os.getenv("EMAIL_HOST", "").strip():
    # Real email, e.g. Gmail: EMAIL_HOST=smtp.gmail.com and a Google app password.
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("EMAIL_HOST").strip()
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
    EMAIL_TIMEOUT = 15
else:
    # No email account configured: emails (with their links) are printed in the Django terminal.
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"segments": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler"},
                 "segments_console": {"class": "logging.StreamHandler", "formatter": "segments"}},
    "loggers": {"apps.segments": {"handlers": ["segments_console"], "level": "INFO", "propagate": False}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
