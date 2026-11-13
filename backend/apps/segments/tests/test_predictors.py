"""Prediction checks use existing artifacts, synthetic lines, and no fitting."""

import ast
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

from apps.segments.predictors.churn.contract import FEATURES as CHURN_FEATURES
from apps.segments.predictors.churn.features import prepare_inputs
from apps.segments.predictors.churn.preparation import load_preparation
from apps.segments.services.fitted import apply_fitted_numeric
from apps.segments.predictors.clv.contract import FEATURES, HISTORY_DAYS
from apps.segments.predictors.clv.features import PurchaseLine, prepare_features
from apps.segments.services import model_loader
from apps.segments.services.model_loader import check_recorded_versions, load_model
from apps.segments.services.results import SegmentError
from apps.segments.services.runner import run_segment

ROOT = Path(__file__).resolve().parents[4]
CUTOFF = "2011-12-09"


def line(invoice="0001", product="00123", timestamp="2011-12-01", quantity=1, price="10.50"):
    return PurchaseLine(invoice, product, timestamp, quantity, price)


@pytest.fixture
def notebook_functions():
    """Execute only the original pure preparation functions, never notebook cells.

    This compares against independent training code without training a model,
    downloading data, reading raw datasets, or executing notebook side effects.
    """
    notebook = json.loads((ROOT / "data_prep/training/clv_prediction.ipynb").read_text(encoding="utf-8"))
    namespace = {"pd": pd, "np": np, "Decimal": Decimal, "HISTORY_DAYS": HISTORY_DAYS,
                 "FEATURES": list(FEATURES)}
    from decimal import InvalidOperation

    namespace["InvalidOperation"] = InvalidOperation
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        tree = ast.parse("".join(cell["source"]))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in {"money", "prepare_purchases", "customer_features"}:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<training-preparation>", "exec"), namespace)
    return namespace


def notebook_frame(lines, cutoff, functions):
    raw = pd.DataFrame([{
        "Customer ID": "synthetic-customer", "Invoice": x.invoice_id,
        "StockCode": x.product_code, "InvoiceDate": x.timestamp,
        "Quantity": str(x.quantity), "Price": str(x.unit_price),
    } for x in lines])
    prepared = functions["prepare_purchases"](raw, "Synthetic reference")
    return functions["customer_features"](prepared, cutoff).reset_index(drop=True)


def test_feature_and_prediction_parity_with_existing_notebook(notebook_functions):
    start = (pd.Timestamp(CUTOFF) - pd.Timedelta(days=HISTORY_DAYS)).date().isoformat()
    lines = [
        line(timestamp=start, quantity=2, price="0.1234"),
        line(timestamp=start, product="00234", price="2.5678"),
        line("0002", timestamp=start, price="3.00"),  # same-day invoice gap
        line("0003", timestamp="2011-10-01", quantity=3, price="8.51"),
        line("0003", timestamp="2011-10-01", quantity=3, price="8.51"),  # repeated line retained
        line("0004", timestamp="2011-12-08", product="POST", price="1.25"),
        line("C0005"), line("0006", product="M"), line("0007", quantity=0),
        line("0008", price="-1"), line("0009", quantity="1.5"),
        line("0010", timestamp=CUTOFF),  # cutoff excluded
        line("0011", timestamp="2010-01-01"),
    ]
    before = deepcopy(lines)
    actual = prepare_features(lines, CUTOFF).frame
    expected = notebook_frame(lines, CUTOFF, notebook_functions)
    pd.testing.assert_frame_equal(actual, expected, check_exact=False, rtol=1e-10, atol=1e-8)
    assert lines == before
    pipeline = load_model("clv")[0]["pipeline"]
    with patch.object(RandomForestRegressor, "fit", side_effect=AssertionError("No retraining")):
        result = run_segment("clv", purchases=lines, observation_date=CUTOFF, currency="GBP")
        reference = float(np.maximum(0, pipeline.predict(expected))[0])
    assert result.status == "predicted"
    assert result.prediction["predicted_revenue"] == pytest.approx(reference, rel=1e-12, abs=1e-8)
    assert result.prediction["horizon_days"] == 90
    assert result.prediction["currency"] == "GBP"
    assert result.metadata["model_status"] == "experimental_baseline_not_beaten"
    assert len(result.metadata["artifact_sha256"]) == 64
    json.dumps(result.as_dict(), allow_nan=False)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_interval_features_preserve_missingness_and_same_day_zeros(count, notebook_functions):
    lines = [line(str(i), timestamp="2011-12-01") for i in range(count)]
    actual = prepare_features(lines, CUTOFF).frame
    pd.testing.assert_frame_equal(actual, notebook_frame(lines, CUTOFF, notebook_functions))
    assert pd.isna(actual.iloc[0]["avg_days_between_orders"]) == (count < 2)
    assert pd.isna(actual.iloc[0]["purchase_regularity"]) == (count < 3)
    result = run_segment("clv", purchases=lines, observation_date=CUTOFF, currency="GBP")
    assert result.status == "predicted"
    assert ("purchase_regularity" in result.eligibility.allowed_missing) == (count < 3)


def test_future_values_do_not_change_features():
    lines = [line()]
    expected = prepare_features(lines, CUTOFF).frame
    actual = prepare_features(lines + [line("future", timestamp=CUTOFF, price="999999")], CUTOFF).frame
    pd.testing.assert_frame_equal(actual, expected)


@pytest.mark.parametrize("lines", [[], [line(timestamp="2009-01-01")], [line(timestamp=CUTOFF)], [line("C1")]])
def test_insufficient_history_is_not_a_zero_prediction(lines):
    with patch.object(RandomForestRegressor, "predict", side_effect=AssertionError("Must skip")):
        result = run_segment("clv", purchases=lines, observation_date=CUTOFF, currency="GBP")
    assert result.status == "unavailable"
    assert result.prediction is None
    assert result.eligibility.issues[0].code == "insufficient_history"


def test_final_clv_model_predicts_without_opt_in():
    result = run_segment("clv", purchases=[line()], observation_date=CUTOFF, currency="GBP")
    assert result.status == "predicted"
    assert result.prediction is not None


def test_supported_currency_is_required():
    with patch.object(RandomForestRegressor, "predict", side_effect=AssertionError("Must skip")):
        assert run_segment("clv", purchases=[line()], observation_date=CUTOFF, currency="USD").reasons[0].code == "unsupported_currency"
        assert run_segment("clv", purchases=[line()], observation_date=CUTOFF).reasons[0].code == "unsupported_currency"


@pytest.mark.parametrize("observation", [None, 123, "bad", "2011-12-09T12:00:00", "2011-12-09T00:00:00Z"])
def test_observation_date_must_be_explicit_and_source_local(observation):
    result = run_segment("clv", purchases=[line()], observation_date=observation, currency="GBP")
    assert result.reasons[0].code == "invalid_observation_date"


def test_conflicting_invoice_dates_are_not_silently_aggregated():
    result = run_segment("clv", purchases=[line(), line(timestamp="2011-12-02")],
                         observation_date=CUTOFF, currency="GBP")
    assert result.reasons[0].code == "conflicting_invoice"


@pytest.mark.parametrize("record", [line(quantity=True), line(quantity="nan"), line(price="Infinity"),
                                     line(timestamp=None), line(product=""), line(invoice=123)])
def test_invalid_lines_are_excluded_and_reported(record):
    prepared = prepare_features([record, line("valid")], CUTOFF)
    assert prepared.history_lines == 1
    assert sum(prepared.excluded_lines.values()) == 1


def test_churn_input_order_and_valid_false():
    values = dict(zip(reversed(CHURN_FEATURES), [10, False, 3, 4, 5, 6]))
    frame, eligible = prepare_inputs(values)
    assert eligible.eligible
    assert tuple(frame.columns) == CHURN_FEATURES
    assert frame["Complain"].iloc[0] == 0


def test_churn_uses_saved_preprocessing_and_unchanged_classifier():
    values = dict(zip(CHURN_FEATURES, [15, 29, 4, 3, False, Decimal("143.32")]))
    classifier, digest = load_model("churn")
    preparation, _, _ = load_preparation(digest)
    expected_frame = apply_fitted_numeric(prepare_inputs(values)[0], preparation)
    expected_class = int(classifier.predict(expected_frame)[0])
    expected_probability = float(classifier.predict_proba(expected_frame)[0, 1])
    with patch.object(RandomForestClassifier, "fit", side_effect=AssertionError("No classifier retraining")):
        result = run_segment("churn", values=values)
    assert result.status == "predicted"
    assert result.eligibility.eligible
    assert result.prediction == {"predicted_class": expected_class, "churn_probability": expected_probability}
    assert result.metadata["preprocessing_available"] is True
    json.dumps(result.as_dict(), allow_nan=False)


def test_churn_missing_numeric_values_use_original_medians():
    values = dict(zip(CHURN_FEATURES, [None, None, 4, 3, False, Decimal("143.32")]))
    result = run_segment("churn", values=values)
    assert result.status == "predicted"
    assert result.eligibility.allowed_missing == ("Tenure", "WarehouseToHome")
    unavailable = run_segment("churn", values={key: None for key in CHURN_FEATURES})
    assert unavailable.status == "unavailable"
    assert not unavailable.eligibility.eligible


def test_churn_still_fails_closed_without_verified_preparation():
    values = dict(zip(CHURN_FEATURES, [15, 29, 4, 3, False, 143.32]))
    with patch("apps.segments.predictors.churn.predictor.load_preparation", side_effect=SegmentError("model_preparation_unavailable", "Saved preprocessing is unavailable.")), \
         patch.object(RandomForestClassifier, "predict", side_effect=AssertionError("Must not score raw values")):
        result = run_segment("churn", values=values)
    assert result.status == "unavailable"
    assert result.reasons[0].code == "model_preparation_unavailable"


def test_churn_saved_statistics_are_bound_to_classifier():
    with pytest.raises(SegmentError, match="does not match"):
        load_preparation("incorrect-classifier-digest")


def test_churn_reconstruction_matches_saved_notebook_preview():
    from apps.segments.predictors.churn.recover_preprocessing import _saved_scaler_preview

    notebook = json.loads((ROOT / "data_prep/training/churn_prediction.ipynb").read_text(encoding="utf-8"))
    preview, auc = _saved_scaler_preview(notebook)
    preparation, metadata, _ = load_preparation(load_model("churn")[1])
    first_rows = pd.DataFrame([
        [15, 29, 4, 3, 0, 143.32], [7, 25, 4, 1, 0, 129.29],
        [27, 13, 3, 1, 0, 168.54], [20, 25, 4, 3, 0, 230.27],
        [30, 15, 4, 4, 0, 322.17],
    ], columns=CHURN_FEATURES)
    np.testing.assert_allclose(apply_fitted_numeric(first_rows, preparation), preview, rtol=0, atol=0.00000051)
    assert round(metadata["verification"]["reproduced_roc_auc"], 4) == auc


def test_missing_churn_data_skips_loading():
    with patch("apps.segments.predictors.churn.predictor.load_model", side_effect=AssertionError("Must skip")):
        result = run_segment("churn", values={})
    assert not result.eligibility.eligible
    assert len(result.eligibility.issues) == 6


def test_model_file_missing_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(model_loader, "MODEL_DIR", tmp_path)
    result = run_segment("clv", purchases=[line()], observation_date=CUTOFF, currency="GBP")
    assert result.reasons[0].code == "model_unavailable"


def test_malformed_contract_is_reported():
    artifact, digest = load_model("clv")
    invalid = {**artifact, "features": list(reversed(FEATURES))}
    with patch("apps.segments.predictors.clv.predictor.load_model", return_value=(invalid, digest)):
        result = run_segment("clv", purchases=[line()], observation_date=CUTOFF, currency="GBP")
    assert result.reasons[0].code == "invalid_artifact"


def test_version_mismatch_is_not_ignored():
    versions = {**load_model("clv")[0]["metadata"]["versions"], "scikit-learn": "0.0"}
    with pytest.raises(SegmentError, match="dependency versions"):
        check_recorded_versions(versions)


@pytest.mark.parametrize("raw, expected", [(-5.0, 0.0), (25.0, 25.0)])
def test_saved_nonnegative_prediction_policy(raw, expected):
    pipeline = load_model("clv")[0]["pipeline"]
    with patch.object(pipeline, "predict", return_value=np.array([raw])):
        result = run_segment("clv", purchases=[line()], observation_date=CUTOFF, currency="GBP")
    assert result.status == "predicted"
    assert result.prediction["predicted_revenue"] == expected


@pytest.mark.parametrize("raw", [np.array([np.nan]), np.array([np.inf]), np.array([1., 2.])])
def test_invalid_model_outputs_are_not_returned_as_predictions(raw):
    pipeline = load_model("clv")[0]["pipeline"]
    with patch.object(pipeline, "predict", return_value=raw):
        result = run_segment("clv", purchases=[line()], observation_date=CUTOFF, currency="GBP")
    assert result.status == "unavailable"
    assert result.reasons[0].code == "invalid_prediction"


def test_runner_rejects_unknown_segment_and_wrong_input_shape():
    assert run_segment("unknown").reasons[0].code == "unknown_segment"
    assert run_segment("churn", values=[]).reasons[0].code == "invalid_input"
    assert run_segment("clv", purchases=[{}], observation_date=CUTOFF, currency="GBP").reasons[0].code == "invalid_purchase_input"


def test_imports_do_not_load_models():
    # Reloading modules must not perform I/O or eagerly load a cached artifact.
    import importlib
    from apps.segments.predictors.churn import predictor as churn
    from apps.segments.predictors.clv import predictor as clv

    with patch.object(model_loader.joblib, "load", side_effect=AssertionError("Unexpected load")):
        importlib.reload(churn)
        importlib.reload(clv)


def test_prediction_does_not_mutate_inputs_or_access_database():
    values = dict(zip(CHURN_FEATURES, [15, 29, 4, 3, False, Decimal("143.32")]))
    purchases = [line()]
    original_values, original_purchases = deepcopy(values), deepcopy(purchases)
    with patch("django.db.backends.base.base.BaseDatabaseWrapper.cursor", side_effect=AssertionError("No database access")):
        churn = run_segment("churn", values=values)
        clv = run_segment("clv", purchases=purchases, observation_date=CUTOFF, currency="GBP")
    assert churn.status == clv.status == "predicted"
    assert values == original_values
    assert purchases == original_purchases
