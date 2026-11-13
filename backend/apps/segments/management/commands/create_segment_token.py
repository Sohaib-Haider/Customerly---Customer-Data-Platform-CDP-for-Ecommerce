from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from ...authentication import issue_token
from ...models import Store


class Command(BaseCommand):
    help = "Issue a short-lived store-scoped Bearer token. Treat the output as a secret."

    def add_arguments(self, parser):
        parser.add_argument("--store-id", required=True)

    def handle(self, *args, **options):
        try:
            store = Store.objects.get(id=options["store_id"])
            token = issue_token(store.id)
        except (ValueError, ValidationError, Store.DoesNotExist):
            raise CommandError("Supply an existing store UUID and a configured private DJANGO_SECRET_KEY.") from None
        self.stdout.write(token)
