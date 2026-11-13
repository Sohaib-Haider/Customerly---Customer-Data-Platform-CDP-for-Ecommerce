"""Read the verified saved statistics; never reconstruct or fit during scoring."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path

from ...services.fitted import FittedNumericPreparation

from ...services import model_loader
from ...services.model_loader import check_recorded_versions
from ...services.results import SegmentError
from .contract import FEATURES


def load_preparation(classifier_digest):
    path = model_loader.MODEL_DIR / "churn_preprocessing.json"
    try:
        stat = path.stat()
    except OSError:
        raise SegmentError("model_preparation_unavailable", "Verified saved churn preprocessing is unavailable.") from None
    return _load(str(path), stat.st_mtime_ns, stat.st_size, classifier_digest)


@lru_cache(maxsize=4)
def _load(path, modified, size, classifier_digest):
    try:
        payload = Path(path).read_bytes()
        bundle = json.loads(payload)
        metadata = bundle["metadata"]
        if (bundle["format"] != "churn_numeric_preparation/v1"
                or tuple(bundle["features"]) != FEATURES
                or metadata["classifier_sha256"] != classifier_digest
                or metadata["method"] != "reconstructed_from_checksum_verified_original_source"
                or not all(metadata["verification"][key] is True for key in (
                    "saved_scaler_preview", "full_transform_parity", "missing_value_parity"))):
            raise ValueError
        preparation = FittedNumericPreparation(FEATURES, tuple(bundle["medians"]), tuple(bundle["means"]), tuple(bundle["scales"]))
    except (OSError, ValueError, KeyError, TypeError):
        raise SegmentError("invalid_preparation", "Saved churn preprocessing does not match the classifier or verified contract.") from None
    check_recorded_versions(metadata.get("versions"))
    return preparation, metadata, hashlib.sha256(payload).hexdigest()
