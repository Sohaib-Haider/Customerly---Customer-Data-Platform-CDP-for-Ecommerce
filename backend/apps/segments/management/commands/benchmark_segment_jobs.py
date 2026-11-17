"""Exercise real batch reads/writes with an unconditional rollback."""
from decimal import Decimal
from time import perf_counter
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.segments.models import Customer, PreparationJob
from apps.segments.services.jobs import process_batch


class Command(BaseCommand):
    help = 'Benchmark one real batch for each model; all benchmark writes are rolled back.'

    def add_arguments(self, parser):
        parser.add_argument('--store-id', type=uuid.UUID, required=True)
        parser.add_argument('--batch-size', type=int, default=100)

    def handle(self, *args, **options):
        size, store_id = options['batch_size'], options['store_id']
        if not 1 <= size <= 200:
            raise CommandError('Batch size must be 1–200.')
        if PreparationJob.objects.filter(store_id=store_id, status__in=['queued', 'running']).exists():
            raise CommandError('Wait for active preparation to finish before benchmarking.')
        first = list(Customer.objects.filter(store_id=store_id).order_by('id').values_list('id', flat=True)[:size])
        if not first:
            raise CommandError('Selected store has no customers.')
        reports = []
        for key in ('churn', 'clv'):
            with transaction.atomic():
                job = PreparationJob.objects.create(store_id=store_id, segment_key=key, status='running',
                    clv_threshold=Decimal('0'), total=len(first), upper_bound=first[-1],
                    lease_token=uuid.uuid4(), lease_until=timezone.now())
                start = perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    process_batch(job, size)
                job.refresh_from_db()
                reports.append(f'{key}: customers={job.processed}, predicted={job.predicted}, '
                    f'unavailable={job.unavailable}, reused={job.reused}, '
                    f'database_statements={len(queries)}, seconds={perf_counter() - start:.2f}')
                transaction.set_rollback(True)
        for report in reports:
            self.stdout.write(report)
        self.stdout.write('All benchmark predictions, memberships and jobs were rolled back. No business data changed.')
