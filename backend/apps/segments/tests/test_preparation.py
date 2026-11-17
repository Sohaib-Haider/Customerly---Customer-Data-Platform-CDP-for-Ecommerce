from decimal import Decimal

from .test_audiences import audience_data, canonical_tables
from apps.segments.models import CustomerMembership, SegmentPrediction
from apps.segments.services.preparation import assign_saved


def test_prepare_requires_authentication_and_explicit_clv_cutoff(audience_data):
    from rest_framework.test import APIClient
    client, *_ = audience_data
    assert APIClient().post('/api/audiences/prepare/', {}, format='json').status_code == 401
    assert client.post('/api/audiences/prepare/', {}, format='json').status_code == 400


def test_assigns_classifier_label_without_rescoring_and_filters_list(audience_data):
    client, store, customer, _ = audience_data
    rows = list(SegmentPrediction.objects.filter(store_id=store, segment_key='churn'))
    for row in rows:
        row.result['prediction']['predicted_class'] = 1 if row.customer_id == customer else 0
        row.save(update_fields=['result'])
    timestamps = {row.id: row.computed_at for row in rows}
    response = client.post('/api/audiences/prepare/', {'phase': 'churn', 'clv_threshold': '100'}, format='json')
    assert response.status_code == 202
    from apps.segments.services.jobs import work_once
    while work_once():
        pass
    assert CustomerMembership.objects.filter(store_id=store).count() == 1
    members = client.get('/api/audiences/churn/customers/').json()
    assert members['count'] == 1 and members['results'][0]['customer_id'] == str(customer)
    assert all(SegmentPrediction.objects.get(id=pk).computed_at == value for pk, value in timestamps.items())


def test_clv_uses_selected_revenue_cutoff_and_excludes_other_store(audience_data):
    client, store, customer, definitions = audience_data
    SegmentPrediction.objects.create(store_id=store, customer_id=customer, segment_id=definitions['clv'],
        segment_key='clv', status='predicted', result={'prediction': {'predicted_revenue': 200, 'currency': 'GBP'}})
    assign_saved(store, 'clv', Decimal('150'))
    assert client.get('/api/audiences/clv/customers/').json()['count'] == 1
    assign_saved(store, 'clv', Decimal('250'))
    assert client.get('/api/audiences/clv/customers/').json()['count'] == 0
    assert CustomerMembership.objects.exclude(store_id=store).count() == 1
