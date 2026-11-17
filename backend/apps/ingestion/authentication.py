"""Use a signed store-user claim; never accept uploader identity from a body."""
from dataclasses import dataclass
import uuid
from django.db import connection
from rest_framework.exceptions import AuthenticationFailed
from apps.segments.authentication import SegmentTokenAuthentication


@dataclass(frozen=True)
class IngestionPrincipal:
    store_id: uuid.UUID
    store_user_id: uuid.UUID
    is_authenticated: bool = True


class IngestionAuthentication(SegmentTokenAuthentication):
    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None
        principal, payload = authenticated
        try:
            user_id = uuid.UUID(payload["store_user_id"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AuthenticationFailed("Use a store-user token issued with create_ingestion_token.") from None
        with connection.cursor() as cursor:
            cursor.execute("SELECT role FROM store_users WHERE id = %s AND store_id = %s", [str(user_id), str(principal.store_id)])
            row = cursor.fetchone()
        if row is None or row[0] not in {"owner", "admin", "member"}:
            raise AuthenticationFailed("The uploader no longer belongs to this store.")
        return IngestionPrincipal(principal.store_id, user_id), payload
