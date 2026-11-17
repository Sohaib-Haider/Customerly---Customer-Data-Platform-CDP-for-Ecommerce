from unittest.mock import patch

from .test_audiences import audience_data, canonical_tables


def test_refresh_is_authenticated_and_bounded(audience_data):
    from rest_framework.test import APIClient
    client, store, *_ = audience_data
    assert APIClient().post('/api/segments/refresh/', {}, format='json').status_code == 401
    with patch('apps.segments.services.backfill.fill_missing', return_value={'processed': 25, 'remaining': True}) as run:
        response = client.post('/api/segments/refresh/', {}, format='json')
    assert response.status_code == 200
    assert response.data['remaining'] is True
    run.assert_called_once_with(store)


def test_saved_predictions_are_not_rerun_and_repeated_refresh_is_noop(audience_data):
    from apps.segments.services.backfill import fill_missing
    from apps.segments.models import SegmentPrediction
    client, store, *_ = audience_data
    original = list(SegmentPrediction.objects.filter(store_id=store).values('id', 'result', 'computed_at'))
    # Both churn customers already have predictions. CLV has no declared GBP
    # history, so it records unavailable rather than inventing data/currency.
    with patch('apps.segments.services.backfill.execute_customer') as predict:
        result = fill_missing(store, limit=1)
        assert result['processed'] == 1 and result['remaining'] is True
        result = fill_missing(store)
        assert result['processed'] == 1 and result['remaining'] is False
        result = fill_missing(store)
        assert result['processed'] == 0 and result['remaining'] is False
        predict.assert_not_called()
    for row in original:
        saved = SegmentPrediction.objects.get(id=row['id'])
        assert saved.result == row['result'] and saved.computed_at == row['computed_at']


def test_missing_customer_calls_predictor_once_and_never_foreign_store(audience_data):
    from apps.segments.services.backfill import fill_missing
    from apps.segments.models import SegmentPrediction
    client, store, customer, definitions = audience_data
    SegmentPrediction.objects.filter(store_id=store, customer_id=customer, segment_key='churn').delete()

    def save(key, tenant, person, **options):
        assert tenant == store and person == customer and key == 'churn'
        SegmentPrediction.objects.create(store_id=tenant, customer_id=person,
            segment_id=definitions[key], segment_key=key, status='predicted',
            result={'prediction': {'churn_probability': 0.8}})

    with patch('apps.segments.services.backfill.execute_customer', side_effect=save) as predict:
        assert fill_missing(store)['processed'] == 3
        assert fill_missing(store)['processed'] == 0
        predict.assert_called_once()
