from datetime import date
from copy import copy
from unittest.mock import patch
import uuid

import pytest

from .test_database_api import records
from ..services.batch_prediction import churn_batch, clv_batch
from ..services.database import churn_inputs, clv_purchases
from ..predictors.churn.predictor import predict as churn_predict
from ..predictors.clv.predictor import predict as clv_predict
from ..services.jobs import enqueue, work_once
from ..models import CustomerMembership, PreparationJob, SegmentPrediction


def test_vectorized_churn_matches_final_single_row_model(records):
    second = copy(records['churn'])
    second.id = uuid.uuid4()
    people = [records['churn'], second, records['retail']]
    expected = churn_predict(churn_inputs(people[0]))
    from ..services.model_loader import load_model
    classifier, _ = load_model('churn')
    with patch.object(classifier, 'predict', wraps=classifier.predict) as prediction:
        results, digest = churn_batch(people)
        prediction.assert_called_once()
    assert digest and results[people[0].id].status == 'predicted'
    assert results[people[0].id].prediction == pytest.approx(expected.prediction)
    assert results[second.id].prediction == pytest.approx(expected.prediction)
    assert results[people[2].id].status == 'unavailable'


def test_vectorized_clv_matches_training_preparation_and_single_row_model(records):
    customer = records['retail']
    results, cutoffs, digest = clv_batch(records['store'].id, [customer, records['churn']])
    assert cutoffs[customer.id] == date(2011, 12, 2)
    expected = clv_predict(clv_purchases(customer, cutoffs[customer.id]), cutoffs[customer.id], currency='GBP')
    assert digest and results[customer.id].prediction == pytest.approx(expected.prediction)
    assert results[records['churn'].id].status == 'unavailable'


def test_batch_clv_keeps_missing_unit_price_guard(records):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('UPDATE order_items SET unit_price=NULL')
    results, _, digest = clv_batch(records['store'].id, [records['retail']])
    assert digest is None
    assert results[records['retail'].id].status == 'unavailable'
    assert results[records['retail'].id].reasons[0].code == 'missing_unit_price'


def test_worker_uses_both_final_artifacts_and_persists_results_and_memberships(records):
    enqueue(records['store'].id, '0')
    while work_once():
        pass
    assert PreparationJob.objects.filter(store=records['store'], status='completed').count() == 2
    assert SegmentPrediction.objects.filter(store=records['store'], status='predicted').count() == 2
    from ..services.catalog import get_definition
    members = CustomerMembership.objects.filter(store=records['store'], segment=get_definition('clv'))
    assert members.count() == 1 and members.first().customer_id == records['retail'].id
