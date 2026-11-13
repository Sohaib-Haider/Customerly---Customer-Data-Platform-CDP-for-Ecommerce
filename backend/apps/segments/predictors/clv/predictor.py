"""CLV inference using the project's final saved fitted artifact."""

import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_is_fitted

from ...services.eligibility import check_eligibility
from ...services.model_loader import check_recorded_versions, load_model
from ...services.results import PredictionResult, SegmentError, unavailable
from .contract import CURRENCY, EXCLUDED_CODES, FEATURES, HISTORY_DAYS, HORIZON_DAYS, POLICY, PREDICTION_POLICY
from .features import prepare_features


def _validate_artifact(artifact):
    try:
        metadata = artifact["metadata"]
        pipeline = artifact["pipeline"]
        compatible = (
            tuple(artifact["features"]) == FEATURES
            and metadata["currency"] == CURRENCY
            and metadata["history_days"] == HISTORY_DAYS
            and metadata["horizon_days"] == HORIZON_DAYS
            and set(metadata["excluded_stock_codes"]) == EXCLUDED_CODES
            and metadata["target"] == "positive_purchase_revenue_next_90_days"
            and metadata["history_boundary"] == "[cutoff-history_days, cutoff)"
            and metadata["date_grain"] == "source-local calendar days with midnight cutoffs"
            and artifact["prediction_policy"] == PREDICTION_POLICY
            and isinstance(pipeline, Pipeline)
            and tuple(pipeline.feature_names_in_) == FEATURES
        )
        if not compatible:
            raise ValueError
        check_is_fitted(pipeline)
    except (KeyError, TypeError, AttributeError, ValueError):
        raise SegmentError("invalid_artifact", "The CLV artifact does not match the implemented training contract.") from None
    check_recorded_versions(metadata.get("versions"))
    return pipeline, metadata


def predict(purchases, observation_date, *, currency):
    if currency != CURRENCY:
        return unavailable("clv", "unsupported_currency", "This model supports GBP inputs only; currency conversion is not implemented.")
    try:
        artifact, digest = load_model("clv")
        pipeline, model_metadata = _validate_artifact(artifact)
        metadata = {
            "artifact_sha256": digest,
            "model_status": model_metadata.get("status", "not_recorded"),
            "trained_at_utc": model_metadata.get("trained_at_utc"),
            "currency": CURRENCY,
            "history_days": HISTORY_DAYS,
            "horizon_days": HORIZON_DAYS,
        }
        prepared = prepare_features(purchases, observation_date)
    except SegmentError as exc:
        return unavailable("clv", exc.code, str(exc))

    metadata.update({"observation_date": prepared.observation_date, "history_lines": prepared.history_lines,
                     "valid_invoices": prepared.valid_invoices, "excluded_lines": prepared.excluded_lines})
    # With no history, check that requirement without inventing feature values.
    if prepared.frame.empty:
        from ...services.eligibility import EligibilityPolicy, HistoryRequirement

        eligibility = check_eligibility({}, EligibilityPolicy(histories=(HistoryRequirement("valid_purchase_invoices"),)),
                                        history_counts={"valid_purchase_invoices": 0})
    else:
        eligibility = check_eligibility(prepared.frame.iloc[0].to_dict(), POLICY,
                                        history_counts={"valid_purchase_invoices": prepared.valid_invoices})
    if not eligibility.eligible:
        return unavailable("clv", "data_unavailable", "Customer inputs do not meet the CLV requirements.", eligibility=eligibility, metadata=metadata)
    try:
        raw = np.asarray(pipeline.predict(prepared.frame.loc[:, list(FEATURES)]), dtype=float)
    except (ValueError, TypeError):
        return unavailable("clv", "prediction_failed", "The saved pipeline could not score the prepared inputs.", eligibility=eligibility, metadata=metadata)
    if raw.shape != (1,) or not np.isfinite(raw).all():
        return unavailable("clv", "invalid_prediction", "The saved pipeline returned an invalid prediction.", eligibility=eligibility, metadata=metadata)
    return PredictionResult("clv", "predicted", {"predicted_revenue": float(np.maximum(0.0, raw[0])),
        "currency": CURRENCY, "horizon_days": HORIZON_DAYS}, eligibility=eligibility, metadata=metadata)
