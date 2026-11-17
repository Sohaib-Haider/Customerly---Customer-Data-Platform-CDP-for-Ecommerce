from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from apps.ingestion.models import Batch
from apps.ingestion.storage import storage


class Command(BaseCommand):
    help = "Delete private CSV content from completed batches older than the retention period; keep audit records."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-days", type=int, default=30)

    def handle(self, *args, **options):
        if options["older_than_days"] < 1:
            raise CommandError("Retention must be at least one day.")
        count = 0
        for batch_id in Batch.objects.filter(status="completed", updated_at__lt=timezone.now() - timedelta(days=options["older_than_days"])).values_list("id", flat=True):
            with transaction.atomic():
                batch = Batch.objects.select_for_update().get(id=batch_id)
                for file in batch.files.exclude(storage_name=""):
                    storage().delete(file.storage_name)
                    file.storage_name = ""
                    file.save(update_fields=["storage_name"])
                    count += 1
        self.stdout.write(f"Removed {count} private CSV files; retained database audit records.")
