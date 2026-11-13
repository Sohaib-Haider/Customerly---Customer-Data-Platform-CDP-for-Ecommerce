"""Store-scoped service tokens for the API until platform login is integrated."""

from dataclasses import dataclass
import uuid

from django.conf import settings
from django.core import signing
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

TOKEN_SALT = "customerly.segments.service-access.v1"


@dataclass(frozen=True)
class SegmentPrincipal:
    store_id: uuid.UUID
    is_authenticated: bool = True


def issue_token(store_id):
    if not settings.SECRET_KEY or settings.SECRET_KEY.startswith("django-insecure-"):
        raise ValueError("Configure a private DJANGO_SECRET_KEY before issuing segment access tokens.")
    return signing.dumps({"store_id": str(uuid.UUID(str(store_id))), "purpose": "segments"}, salt=TOKEN_SALT)


class SegmentTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0].lower() != b"bearer" or len(parts[1]) > 4096:
            raise AuthenticationFailed("Provide a valid segment Bearer token.")
        if settings.SECRET_KEY.startswith("django-insecure-"):
            raise AuthenticationFailed("Segment API authentication has not been configured.")
        try:
            payload = signing.loads(parts[1].decode("ascii"), salt=TOKEN_SALT,
                                    max_age=getattr(settings, "SEGMENTS_TOKEN_MAX_AGE", 3600))
            if payload.get("purpose") != "segments":
                raise ValueError
            principal = SegmentPrincipal(uuid.UUID(payload["store_id"]))
        except (signing.BadSignature, ValueError, TypeError, KeyError, AttributeError, UnicodeDecodeError):
            raise AuthenticationFailed("The segment access token is invalid or expired.") from None
        return principal, payload

    def authenticate_header(self, request):
        return "Bearer"
