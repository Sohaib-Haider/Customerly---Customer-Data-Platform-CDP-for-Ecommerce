import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ...models import Customer, SegmentDefinition, Store
from ...serializers import RunSerializer
from ...services.execution import execute_customer


class Command(BaseCommand):
    help = "Score an explicit list of store customers and persist results, or use --dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--segment", choices=["churn", "clv"], required=True)
        parser.add_argument("--store-id", required=True)
        parser.add_argument("--customer-id", action="append", required=True)
        parser.add_argument("--observation-date")
        parser.add_argument("--currency", choices=["GBP"])
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--update-memberships", action="store_true")
        parser.add_argument("--membership-threshold")

    def handle(self, *args, **options):
        try:
            store_id = uuid.UUID(options["store_id"])
        except ValueError:
            raise CommandError("Supply a valid store UUID.") from None
        data = {"customer_ids": options["customer_id"], "persist": not options["dry_run"],
                "update_memberships": options["update_memberships"]}
        for key in ("observation_date", "currency", "membership_threshold"):
            if options[key] is not None:
                data[key] = options[key]
        serializer = RunSerializer(data=data, context={"segment": options["segment"]})
        if not serializer.is_valid():
            raise CommandError(str(serializer.errors))
        validated = dict(serializer.validated_data)
        ids = validated.pop("customer_ids")
        if not Store.objects.filter(id=store_id).exists() or Customer.objects.filter(store_id=store_id, id__in=ids).count() != len(ids):
            raise CommandError("Every customer must exist in the selected store.")
        try:
            with transaction.atomic():
                results = [execute_customer(options["segment"], store_id, customer_id, **validated) for customer_id in sorted(ids, key=str)]
        except (ValueError, SegmentDefinition.DoesNotExist) as exc:
            raise CommandError("Check segment initialization and request options: " + str(exc)) from None
        self.stdout.write(json.dumps({"count": len(results), "results": results}, default=str))
