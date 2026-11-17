"""Durable, leased background preparation. No model work runs in HTTP requests."""
from datetime import timedelta
from decimal import Decimal
import hashlib
import uuid

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from ..models import Customer, CustomerMembership, PreparationJob, SegmentPrediction
from .batch_prediction import churn_batch, clv_batch
from .catalog import get_definition, registry_matches
from .logging import after_commit, logged, progress
from .results import SegmentError

LEASE_MINUTES = 15


def serialize(job):
    return {name: getattr(job, name) for name in ('id', 'segment_key', 'status', 'total', 'processed',
        'predicted', 'unavailable', 'reused', 'member_count', 'error_code', 'created_at', 'updated_at')}


def latest_jobs(store_id):
    return [serialize(job) for key in ('churn', 'clv')
            if (job := PreparationJob.objects.filter(store_id=store_id, segment_key=key).order_by('-created_at').first())]


@logged('jobs.enqueue')
def enqueue(store_id, threshold):
    threshold = Decimal(threshold)
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            lock = int.from_bytes(hashlib.sha256(f'preparation-enqueue:{store_id}'.encode()).digest()[:8], 'big', signed=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock])
        active = list(PreparationJob.objects.filter(store_id=store_id, status__in=['queued', 'running']))
        if any(job.clv_threshold != threshold for job in active):
            raise ValidationError({'detail': 'Preparation is already running with a different CLV cutoff. Wait for it to finish.'})
        customers = Customer.objects.filter(store_id=store_id)
        total = customers.count()
        upper = customers.order_by('-id').values_list('id', flat=True).first()
        for key in ('churn', 'clv'):
            if any(job.segment_key == key for job in active):
                continue
            previous = PreparationJob.objects.filter(store_id=store_id, segment_key=key).order_by('-created_at').first()
            if previous and previous.status == 'failed' and previous.clv_threshold == threshold:
                previous.status, previous.error_code = 'queued', ''
                previous.lease_token = previous.lease_until = None
                previous.save(update_fields=['status', 'error_code', 'lease_token', 'lease_until', 'updated_at'])
                progress('jobs.resumed', 'Failed preparation queued from its last committed batch.', segment=key)
            else:
                PreparationJob.objects.create(store_id=store_id, segment_key=key,
                    clv_threshold=threshold, total=total, upper_bound=upper)
        after_commit('jobs.queued', 'Durable preparation jobs committed; background workers can claim them.')
    return latest_jobs(store_id)


def claim():
    now = timezone.now()
    with transaction.atomic():
        rows = PreparationJob.objects.filter(Q(status='queued') | Q(status='running', lease_until__lt=now)).order_by('created_at')
        rows = rows.select_for_update(skip_locked=True) if connection.vendor == 'postgresql' else rows.select_for_update()
        job = rows.first()
        if job is None:
            return None
        job.status, job.lease_token = 'running', uuid.uuid4()
        job.lease_until = now + timedelta(minutes=LEASE_MINUTES)
        job.save(update_fields=['status', 'lease_token', 'lease_until', 'updated_at'])
    return job


def _lock_customers(job, ids):
    if connection.vendor != 'postgresql' or not ids:
        return
    locks = [int.from_bytes(hashlib.sha256(f'segments:{job.store_id}:{job.segment_key}:{person}'.encode()).digest()[:8],
        'big', signed=True) for person in sorted(ids, key=str)]
    with connection.cursor() as cursor:
        # Match explicit run/ingestion locks, but acquire the whole batch in one round trip.
        cursor.execute('SELECT pg_advisory_xact_lock(value) FROM unnest(%s::bigint[]) WITH ORDINALITY AS t(value, position) ORDER BY position', [locks])


@logged('jobs.batch')
def process_batch(job, batch_size=100):
    definition = get_definition(job.segment_key)
    customers = Customer.objects.filter(store_id=job.store_id)
    if job.upper_bound is None:
        customers = customers.none()
    else:
        customers = customers.filter(id__lte=job.upper_bound)
    if job.cursor:
        customers = customers.filter(id__gt=job.cursor)
    page = list(customers.order_by('id')[:batch_size])
    ids = [person.id for person in page]
    existing = {row.customer_id: row for row in SegmentPrediction.objects.filter(
        store_id=job.store_id, segment=definition, customer_id__in=ids)}
    missing = [person for person in page if person.id not in existing]
    progress('jobs.batch.loaded', 'Customer inputs and saved predictions read in bulk.', segment=job.segment_key,
        count=len(page), missing=len(missing), reused=len(existing))
    results, cutoffs, digest = {}, {}, None
    if missing:
        if job.segment_key == 'churn':
            results, digest = churn_batch(missing)
        else:
            results, cutoffs, digest = clv_batch(job.store_id, missing)
        if digest and not registry_matches(definition, digest):
            raise SegmentError('model_registry_mismatch', 'Final artifact must match the active model registry.')
    now = timezone.now()
    with transaction.atomic():
        current = PreparationJob.objects.select_for_update().get(id=job.id)
        if current.lease_token != job.lease_token or current.status != 'running':
            progress('jobs.lease.lost', 'Another worker owns this job; computed batch will not be saved.')
            return
        _lock_customers(job, ids)
        # Re-read after acquiring the same locks used by explicit model runs.
        existing = {row.customer_id: row for row in SegmentPrediction.objects.filter(
            store_id=job.store_id, segment=definition, customer_id__in=ids)}
        new = [SegmentPrediction(store_id=job.store_id, customer_id=person.id, segment=definition,
            segment_key=job.segment_key, status=results[person.id].status,
            result=results[person.id].as_dict(), observation_date=cutoffs.get(person.id))
            for person in missing if person.id not in existing]
        SegmentPrediction.objects.bulk_create(new, batch_size=100)
        rows = {**existing, **{row.customer_id: row for row in new}}
        included, excluded, changed = [], [], []
        predicted = unavailable = 0
        for person in page:
            row = rows[person.id]
            if row.status != 'predicted':
                unavailable += 1
                continue
            predicted += 1
            prediction = row.result.get('prediction') or {}
            if job.segment_key == 'churn':
                if prediction.get('predicted_class') not in (0, 1):
                    continue
                member = prediction['predicted_class'] == 1
                score = Decimal(str(prediction['churn_probability']))
            else:
                if prediction.get('predicted_revenue') is None:
                    continue
                member = Decimal(str(prediction['predicted_revenue'])) >= job.clv_threshold
                score = None
            if member:
                included.append(CustomerMembership(store_id=job.store_id, customer_id=person.id,
                    segment=definition, score=score, assigned_at=now))
            else:
                excluded.append(person.id)
            row.result['membership_policy'] = {'requested': True, 'updated': True,
                'rule': 'predicted_class_1' if job.segment_key == 'churn' else 'predicted_revenue_at_least',
                'threshold': None if job.segment_key == 'churn' else str(job.clv_threshold)}
            row.result['membership'] = {'updated': True, 'is_member': member}
            changed.append(row)
        CustomerMembership.objects.bulk_create(included, batch_size=100, update_conflicts=True,
            unique_fields=['store', 'customer', 'segment'], update_fields=['score', 'assigned_at'])
        if excluded:
            CustomerMembership.objects.filter(store_id=job.store_id, segment=definition, customer_id__in=excluded).delete()
        if changed:
            SegmentPrediction.objects.bulk_update(changed, ['result'], batch_size=100)
        current.processed += len(page)
        current.predicted += predicted
        current.unavailable += unavailable
        current.reused += len(existing)
        current.member_count = CustomerMembership.objects.filter(store_id=job.store_id, segment=definition).count()
        if page:
            current.cursor = page[-1].id
        # One extra empty batch is unnecessary when fewer than batch_size were read.
        current.status = 'completed' if len(page) < batch_size else 'queued'
        if current.status == 'completed':
            current.total = current.processed
        current.lease_token = current.lease_until = None
        current.error_code = ''
        current.save()
        after_commit('jobs.batch.committed', 'Bulk predictions, memberships and progress committed together.',
            job_id=job.id, segment=job.segment_key, batch=len(page), processed=current.processed,
            total=current.total, members=current.member_count, status=current.status)


def work_once(batch_size=100):
    job = claim()
    if job is None:
        return False
    try:
        process_batch(job, batch_size)
    except Exception as exc:
        code = exc.code if isinstance(exc, SegmentError) else type(exc).__name__
        PreparationJob.objects.filter(id=job.id, lease_token=job.lease_token).update(status='failed',
            error_code=code, lease_token=None, lease_until=None, updated_at=timezone.now())
        progress('jobs.failed', 'Batch rolled back; retry resumes from the last committed checkpoint.',
            job_id=job.id, segment=job.segment_key, reason=code)
    return True
