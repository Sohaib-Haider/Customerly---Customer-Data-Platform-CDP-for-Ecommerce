"""Bounded database reads and vectorized inference with the final PKL artifacts."""
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
from django.db import connection
from sklearn.ensemble import RandomForestClassifier
from sklearn.utils.validation import check_is_fitted

from .database import churn_inputs, db_uuid, purchase_lines
from .fitted import apply_fitted_numeric
from .logging import logged, progress
from .model_loader import load_model
from .results import PredictionResult, SegmentError, unavailable


@logged('batch.churn')
def churn_batch(customers):
    from ..predictors.churn.features import prepare_inputs
    from ..predictors.churn.contract import FEATURES
    from ..predictors.churn.preparation import load_preparation
    results, eligible = {}, []
    for customer in customers:
        frame, eligibility = prepare_inputs(churn_inputs(customer))
        if eligibility.eligible:
            eligible.append((customer.id, frame, eligibility))
        else:
            results[customer.id] = unavailable('churn', 'data_unavailable',
                'Customer inputs do not meet the churn requirements.', eligibility=eligibility)
    progress('batch.eligibility', 'Churn eligibility checked in memory.', eligible=len(eligible), unavailable=len(results))
    if not eligible:
        return results, None
    classifier, digest = load_model('churn')
    if not isinstance(classifier, RandomForestClassifier):
        raise SegmentError('invalid_artifact', 'Unexpected churn classifier.')
    check_is_fitted(classifier)
    if tuple(classifier.feature_names_in_) != FEATURES or list(classifier.classes_) != [0, 1]:
        raise SegmentError('invalid_artifact', 'Unexpected churn feature contract.')
    preparation, preparation_metadata, prep_digest = load_preparation(digest)
    frames = pd.concat([item[1] for item in eligible], ignore_index=True)
    prepared = apply_fitted_numeric(frames, preparation)
    progress('batch.inference.started', 'Running one vectorized churn model call for eligible customers.', count=len(eligible))
    labels = np.asarray(classifier.predict(prepared))
    probabilities = np.asarray(classifier.predict_proba(prepared), dtype=float)
    if (labels.shape != (len(eligible),) or not np.isin(labels, [0, 1]).all()
            or probabilities.shape != (len(eligible), 2) or not np.isfinite(probabilities).all()
            or (probabilities < 0).any() or (probabilities > 1).any()
            or not np.allclose(probabilities.sum(axis=1), 1)):
        raise SegmentError('invalid_prediction', 'Churn batch outputs are invalid.')
    metadata = {'artifact_sha256': digest, 'preprocessing_sha256': prep_digest,
        'preprocessing_available': True, 'preprocessing_method': preparation_metadata['method'],
        'model_status': 'notebook_snapshot_classifier', 'probability_calibrated': False,
        'evaluation_limitations': preparation_metadata['limitations']}
    for index, (customer_id, _, eligibility) in enumerate(eligible):
        results[customer_id] = PredictionResult('churn', 'predicted',
            {'predicted_class': int(labels[index]), 'churn_probability': float(probabilities[index, 1])},
            eligibility=eligibility, metadata=metadata)
    return results, digest


def clv_histories(store_id, customers):
    """Read source declarations, cutoffs and bounded purchase history in bulk."""
    from apps.ingestion.models import Source
    currencies = dict(Source.objects.filter(store_id=store_id).values_list('namespace', 'currency'))
    currencies['ret'] = 'GBP'
    candidates = [c for c in customers if currencies.get(c.source_customer_id.split(':', 1)[0]) == 'GBP']
    histories, cutoffs = defaultdict(list), {}
    if not candidates:
        return histories, cutoffs
    ids = [db_uuid(c.id) for c in candidates]
    placeholders = ','.join(['%s'] * len(ids))
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT customer_id, MAX(order_date) FROM orders WHERE store_id=%s AND customer_id IN ({placeholders}) GROUP BY customer_id', [db_uuid(store_id), *ids])
        import uuid
        for customer_id, last in cursor.fetchall():
            if last is not None:
                cutoff = (date.fromisoformat(last) if isinstance(last, str) else last) + timedelta(days=1)
                cutoffs[uuid.UUID(str(customer_id))] = cutoff
        if not cutoffs:
            return histories, cutoffs
        conditions, params = [], [db_uuid(store_id)]
        for customer_id, cutoff in cutoffs.items():
            conditions.append('(o.customer_id=%s AND o.order_date >= %s AND o.order_date < %s)')
            params.extend([db_uuid(customer_id), cutoff - timedelta(days=288), cutoff])
        cursor.execute('''SELECT o.customer_id, o.source_order_id, p.source_product_id, o.order_date,
            i.source_timestamp, i.quantity, i.unit_price, i.sales_value, p.store_id
            FROM orders o JOIN order_items i ON i.order_id=o.id JOIN products p ON p.id=i.product_id
            WHERE o.store_id=%s AND (''' + ' OR '.join(conditions) + ''')
            ORDER BY o.customer_id, o.order_date, o.source_order_id, i.id LIMIT 100001''', params)
        rows = cursor.fetchall()
    if len(rows) > 100000:
        raise SegmentError('history_batch_limit', 'Purchase-history batch is too large; reduce worker batch size.')
    for row in rows:
        histories[uuid.UUID(str(row[0]))].append(row[1:])
    return histories, cutoffs


@logged('batch.clv')
def clv_batch(store_id, customers):
    from ..predictors.clv.features import prepare_features
    from ..predictors.clv.contract import FEATURES, POLICY
    from ..predictors.clv.predictor import _validate_artifact
    from .eligibility import check_eligibility
    histories, cutoffs = clv_histories(store_id, customers)
    results, eligible = {}, []
    for customer in customers:
        cutoff = cutoffs.get(customer.id)
        if cutoff is None:
            results[customer.id] = unavailable('clv', 'data_unavailable', 'CLV requires declared GBP purchase history.')
            continue
        try:
            prepared = prepare_features(purchase_lines(customer, histories[customer.id]), cutoff)
            if prepared.frame.empty:
                results[customer.id] = unavailable('clv', 'data_unavailable', 'No valid purchase history.')
                continue
            eligibility = check_eligibility(prepared.frame.iloc[0].to_dict(), POLICY,
                history_counts={'valid_purchase_invoices': prepared.valid_invoices})
            if not eligibility.eligible:
                results[customer.id] = unavailable('clv', 'data_unavailable', 'CLV requirements not met.', eligibility=eligibility)
            else:
                eligible.append((customer.id, prepared, eligibility))
        except SegmentError as exc:
            results[customer.id] = unavailable('clv', exc.code, str(exc))
    progress('batch.eligibility', 'CLV eligibility checked in memory.', eligible=len(eligible), unavailable=len(results))
    if not eligible:
        return results, cutoffs, None
    artifact, digest = load_model('clv')
    pipeline, model_metadata = _validate_artifact(artifact)
    frame = pd.concat([item[1].frame.loc[:, list(FEATURES)] for item in eligible], ignore_index=True)
    progress('batch.inference.started', 'Running one vectorized CLV pipeline call for eligible customers.', count=len(eligible))
    raw = np.asarray(pipeline.predict(frame), dtype=float)
    if raw.shape != (len(eligible),) or not np.isfinite(raw).all():
        raise SegmentError('invalid_prediction', 'CLV batch outputs are invalid.')
    for index, (customer_id, prepared, eligibility) in enumerate(eligible):
        results[customer_id] = PredictionResult('clv', 'predicted',
            {'predicted_revenue': float(max(0, raw[index])), 'currency': 'GBP', 'horizon_days': 90},
            eligibility=eligibility, metadata={'artifact_sha256': digest, 'currency': 'GBP', 'history_days': 288,
                'horizon_days': 90, 'trained_at_utc': model_metadata.get('trained_at_utc'),
                'model_status': model_metadata.get('status', 'not_recorded'),
                'observation_date': prepared.observation_date, 'history_lines': prepared.history_lines,
                'valid_invoices': prepared.valid_invoices, 'excluded_lines': prepared.excluded_lines})
    return results, cutoffs, digest
