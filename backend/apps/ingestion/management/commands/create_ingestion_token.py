import uuid
from django.conf import settings
from django.core import signing
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from apps.segments.authentication import TOKEN_SALT


class Command(BaseCommand):
    help = "Issue an expiring token for an existing store user; output is a secret."

    def add_arguments(self, parser):
        parser.add_argument("--store-id", required=True, type=uuid.UUID)
        parser.add_argument("--user-id", required=True, type=uuid.UUID)

    def handle(self, *args, **options):
        if not settings.SECRET_KEY or settings.SECRET_KEY.startswith("django-insecure-"):
            raise CommandError("Configure a private DJANGO_SECRET_KEY first.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT role FROM store_users WHERE id = %s AND store_id = %s", [str(options["user_id"]), str(options["store_id"])])
            row = cursor.fetchone()
        if row is None or row[0] not in {"owner", "admin", "member"}:
            raise CommandError("User must belong to the store with an authorized role.")
        self.stdout.write(signing.dumps({"store_id": str(options["store_id"]), "store_user_id": str(options["user_id"]),
                                         "purpose": "segments"}, salt=TOKEN_SALT))
