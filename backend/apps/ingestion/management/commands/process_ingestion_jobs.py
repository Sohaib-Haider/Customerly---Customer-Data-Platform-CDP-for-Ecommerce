import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from apps.ingestion.jobs import run_jobs


class Command(BaseCommand):
    help = "Resume durable pending prediction work (run with --watch as a worker)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--watch", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            from django.core.management.base import CommandError
            raise CommandError("Limit must be between 1 and 1000.")
        while True:
            count = run_jobs(limit=options["limit"], retry_failed=options["retry_failed"])
            self.stdout.write(f"Processed {count} prediction jobs.")
            if not options["watch"]:
                break
            close_old_connections()
            time.sleep(5)
