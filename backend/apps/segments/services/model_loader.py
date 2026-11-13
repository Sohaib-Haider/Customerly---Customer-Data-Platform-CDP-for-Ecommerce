"""Load only the two trusted repository artifacts, lazily and without fitting."""

from functools import lru_cache
import hashlib
from importlib.metadata import version
from pathlib import Path
import platform
import warnings

import joblib
from sklearn.exceptions import InconsistentVersionWarning

from .results import SegmentError

MODEL_DIR = Path(__file__).resolve().parents[3] / "ml_models"
ARTIFACTS = {"churn": "churn_model.pkl", "clv": "clv_model.pkl"}


def load_model(segment):
    """No uploaded paths are accepted. Replacing a local file invalidates cache."""
    if segment not in ARTIFACTS:
        raise SegmentError("unknown_segment", "This segment has no configured predictor.")
    path = MODEL_DIR / ARTIFACTS[segment]
    try:
        stat = path.stat()
    except OSError:
        raise SegmentError("model_unavailable", "The configured model file is unavailable.") from None
    return _load_cached(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4)
def _load_cached(path, modified, size):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", InconsistentVersionWarning)
            artifact = joblib.load(path)
        with Path(path).open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except InconsistentVersionWarning:
        raise SegmentError("incompatible_environment", "The saved estimator requires its training scikit-learn version.") from None
    except Exception:
        raise SegmentError("model_load_failed", "The configured model could not be loaded.") from None
    return artifact, digest


def check_recorded_versions(versions):
    """The existing CLV bundle records exact versions; do not ignore mismatches."""
    if not isinstance(versions, dict):
        raise SegmentError("invalid_artifact", "The model has no usable environment metadata.")
    current = {"python": platform.python_version()}
    current.update({name: version(name) for name in ("numpy", "pandas", "scikit-learn", "joblib")})
    if any(versions.get(name) != value for name, value in current.items()):
        raise SegmentError("incompatible_environment", "Use the dependency versions recorded in the model metadata.")
