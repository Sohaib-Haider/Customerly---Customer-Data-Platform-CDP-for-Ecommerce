"""Inference with the unchanged classifier and verified original statistics."""

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.utils.validation import check_is_fitted

from ...services.fitted import apply_fitted_numeric

from ...services.model_loader import load_model
from ...services.results import PredictionResult, SegmentError, unavailable
from .contract import FEATURES
from .features import prepare_inputs
from .preparation import load_preparation
from ...services.logging import logged, progress


@logged('churn.predict')
def predict(values):
    progress('churn.eligibility.started', 'Checking required churn inputs.')
    frame, eligibility = prepare_inputs(values)
    if not eligibility.eligible:
        progress('churn.eligibility.unavailable', 'Required churn inputs are missing or invalid; model will not run.')
        return unavailable("churn", "data_unavailable", "Customer inputs do not meet the churn requirements.", eligibility=eligibility)
    try:
        progress('churn.eligibility.passed', 'Churn inputs are eligible; loading the final classifier.')
        classifier, digest = load_model("churn")
        if not isinstance(classifier, RandomForestClassifier):
            raise SegmentError("invalid_artifact", "The churn artifact is not the expected classifier.")
        check_is_fitted(classifier)
        if tuple(classifier.feature_names_in_) != FEATURES or list(classifier.classes_) != [0, 1]:
            raise SegmentError("invalid_artifact", "The classifier does not match the implemented input contract.")
        preparation, preparation_metadata, preparation_digest = load_preparation(digest)
        progress('churn.preparation.loaded', 'Verified original fitted imputer and scaler are available.')
    except SegmentError as exc:
        progress('churn.model.unavailable', 'Churn model or fitted preparation cannot be used.', reason=exc.code)
        return unavailable("churn", exc.code, str(exc), eligibility=eligibility)
    except (ValueError, AttributeError):
        progress('churn.model.invalid', 'Churn artifact has an invalid fitted input contract.')
        return unavailable("churn", "invalid_artifact", "The classifier has no valid fitted input contract.", eligibility=eligibility)
    metadata = {
        "artifact_sha256": digest, "preprocessing_sha256": preparation_digest,
        "preprocessing_available": True, "preprocessing_method": preparation_metadata["method"],
        "model_status": "notebook_snapshot_classifier",
        "evaluation_limitations": preparation_metadata["limitations"],
        "probability_calibrated": False,
    }
    try:
        progress('churn.preparation.started', 'Applying training-time imputation and scaling in memory; database values stay unchanged.')
        prepared = apply_fitted_numeric(frame, preparation)
        progress('churn.inference.started', 'Running the saved classifier and churn probability prediction.')
        predicted = np.asarray(classifier.predict(prepared))
        probabilities = np.asarray(classifier.predict_proba(prepared), dtype=float)
    except (ValueError, TypeError):
        progress('churn.inference.failed', 'Classifier could not predict from prepared inputs.')
        return unavailable("churn", "prediction_failed", "The saved classifier could not score the prepared inputs.", eligibility=eligibility, metadata=metadata)
    if (predicted.shape != (1,) or predicted[0] not in (0, 1) or probabilities.shape != (1, 2)
            or not np.isfinite(probabilities).all() or (probabilities < 0).any()
            or (probabilities > 1).any() or not np.isclose(probabilities.sum(), 1.0)):
        progress('churn.inference.invalid', 'Classifier returned an invalid output shape or probability.')
        return unavailable("churn", "invalid_prediction", "The saved classifier returned an invalid prediction.", eligibility=eligibility, metadata=metadata)
    progress('churn.inference.completed', 'Classifier output and probabilities passed validation.')
    return PredictionResult("churn", "predicted", {
        "predicted_class": int(predicted[0]),
        "churn_probability": float(probabilities[0, list(classifier.classes_).index(1)]),
    }, eligibility=eligibility, metadata=metadata)
