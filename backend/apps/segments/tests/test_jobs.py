from datetime import timedelta
from unittest.mock import patch
import uuid

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
import pytest

from .test_audiences import audience_data, canonical_tables
from ..models import Customer, CustomerMembership, PreparationJob, SegmentPrediction
from ..services.jobs import claim, enqueue, process_batch, work_once


def test_queue_returns_immediately_deduplicates_and_is_tenant_scoped(audience_data):
    client, store, *_ = audience_data
    with patch('apps.segments.services.jobs.churn_batch', side_effect=AssertionError('HTTP ran inference')):
        first = client.post('/api/audiences/prepare/', {'clv_threshold': '100'}, format='json')
        second = client.post('/api/audiences/prepare/', {'clv_threshold': '100'}, format='json')
    assert first.status_code == second.status_code == 202
    assert len(first.data['jobs']) == 2
    assert PreparationJob.objects.filter(store_id=store).count() == 2
    assert client.post('/api/audiences/prepare/', {'clv_threshold': '200'}, format='json').status_code == 400
    assert len(client.get('/api/audiences/jobs/').data['jobs']) == 2
    from rest_framework.test import APIClient
    from ..authentication import issue_token
    other = APIClient()
    other.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_token(uuid.uuid4()))
    assert other.get('/api/audiences/jobs/').status_code == 404


def test_commits_batched_counts_and_resumes_without_rescoring(audience_data):
    client, store, customer, _ = audience_data
    for row in SegmentPrediction.objects.filter(store_id=store):
        row.result['prediction']['predicted_class'] = int(row.customer_id == customer)
        row.save(update_fields=['result'])
    original = {row.id: row.computed_at for row in SegmentPrediction.objects.filter(store_id=store)}
    enqueue(store, '100')
    with patch('apps.segments.services.jobs.churn_batch', side_effect=AssertionError('rescored saved prediction')):
        assert work_once(batch_size=1)
        first = PreparationJob.objects.get(store_id=store, segment_key='churn')
        assert first.processed == 1 and first.status == 'queued'
        while work_once(batch_size=1):
            pass
    jobs = PreparationJob.objects.filter(store_id=store)
    assert all(job.status == 'completed' and job.processed == 2 for job in jobs)
    assert client.get('/api/audiences/churn/customers/').data['count'] == 1
    assert PreparationJob.objects.get(store_id=store, segment_key='clv').unavailable == 2
    assert all(SegmentPrediction.objects.get(id=key).computed_at == value for key, value in original.items())


def test_failure_rolls_back_checkpoint_and_retry_resumes(audience_data):
    client, store, *_ = audience_data
    enqueue(store, '10')
    with patch('apps.segments.services.jobs.CustomerMembership.objects.bulk_create', side_effect=RuntimeError('private secret')):
        assert work_once()
    job = PreparationJob.objects.get(store_id=store, segment_key='churn')
    assert job.status == 'failed' and job.processed == 0 and job.cursor is None
    assert job.error_code == 'RuntimeError'
    enqueue(store, '10')
    while work_once():
        pass
    job.refresh_from_db()
    assert job.status == 'completed' and job.processed == 2


def test_member_counts_increase_after_each_committed_batch(audience_data):
    client, store, *_ = audience_data
    CustomerMembership.objects.filter(store_id=store).delete()
    for row in SegmentPrediction.objects.filter(store_id=store, segment_key='churn'):
        row.result['prediction']['predicted_class'] = 1
        row.save(update_fields=['result'])
    enqueue(store, '100')
    assert work_once(batch_size=1)
    job = PreparationJob.objects.get(store_id=store, segment_key='churn')
    assert job.processed == job.member_count == 1
    assert client.get('/api/audiences/churn/customers/').data['count'] == 1
    assert work_once(batch_size=1)
    job.refresh_from_db()
    assert job.processed == job.member_count == 2
    assert client.get('/api/audiences/churn/customers/').data['count'] == 2


def test_expired_lease_can_be_reclaimed_but_old_worker_cannot_write(audience_data):
    _, store, *_ = audience_data
    enqueue(store, '10')
    old = claim()
    PreparationJob.objects.filter(id=old.id).update(lease_until=timezone.now() - timedelta(seconds=1))
    current = claim()
    assert current.id == old.id and current.lease_token != old.lease_token
    process_batch(old)
    current.refresh_from_db()
    assert current.processed == 0
    process_batch(current)
    current.refresh_from_db()
    assert current.processed == 2


def test_database_round_trips_do_not_grow_per_customer_for_churn(audience_data):
    _, store, *_ = audience_data
    # All lack required data, so no model or registry access is necessary.
    SegmentPrediction.objects.filter(store_id=store).delete()
    from apps.analytics.tests.test_customer360 import insert
    for i in range(30):
        insert('customers', id=uuid.uuid4().hex, store_id=store.hex, source_customer_id=f'chr:{i}',
               created_at='2026-01-01', updated_at='2026-01-01')
    enqueue(store, '100')
    job = claim()
    with CaptureQueriesContext(connection) as queries:
        process_batch(job, 100)
    assert len(queries) < 30, [item['sql'][:80] for item in queries]
    job.refresh_from_db()
    assert job.processed == 32 and job.unavailable == 32
