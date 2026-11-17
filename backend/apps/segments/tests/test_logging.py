import logging
import re

import pytest

from apps.segments.services.logging import logged, logger, progress


@pytest.fixture
def segment_logs(monkeypatch, caplog):
    monkeypatch.setattr(logger, 'propagate', True)
    caplog.set_level(logging.INFO, logger='apps.segments')
    return caplog


def test_nested_steps_share_trace_and_do_not_log_input_values(segment_logs):
    @logged('inner')
    def inner(values):
        progress('eligibility.checked', 'Required inputs checked.')
        return 42

    @logged('outer')
    def outer(values):
        return inner(values)

    assert outer({'email': 'private@example.com', 'revenue': 'SECRET_AMOUNT'}) == 42
    assert 'outer.started' in segment_logs.text and 'inner.finished' in segment_logs.text
    assert 'private@example.com' not in segment_logs.text and 'SECRET_AMOUNT' not in segment_logs.text
    ids = re.findall(r'run=([a-f0-9]{12})', segment_logs.text)
    assert ids and len(set(ids)) == 1


def test_unexpected_failure_is_logged_without_exception_message(segment_logs):
    @logged('model_test')
    def fail():
        raise RuntimeError('postgresql://secret-password@example.com')

    with pytest.raises(RuntimeError):
        fail()
    assert 'model_test.failed' in segment_logs.text and 'error_type=RuntimeError' in segment_logs.text
    assert 'secret-password' not in segment_logs.text
    assert 'model_test.finished' not in segment_logs.text


def test_churn_missing_inputs_explains_why_model_did_not_run(segment_logs):
    from apps.segments.predictors.churn.predictor import predict
    assert predict({}).status == 'unavailable'
    assert 'churn.eligibility.started' in segment_logs.text
    assert 'churn.eligibility.unavailable' in segment_logs.text
    assert 'churn.inference.started' not in segment_logs.text


def test_clv_currency_mismatch_explains_skip(segment_logs):
    from apps.segments.predictors.clv.predictor import predict
    assert predict([], '2026-01-01', currency='USD').status == 'unavailable'
    assert 'clv.currency.unavailable' in segment_logs.text
    assert 'clv.inference.started' not in segment_logs.text
