"""Offline reconstruction of the notebook's missing fitted preparation.

Run from backend/: python -m apps.segments.predictors.churn.recover_preprocessing
Only transformers are fitted on the original checksum-verified training CSV.
The classifier, source data and notebook are never fitted, rewritten or rerun.
"""

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import re

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from ...services.fitted import FittedNumericPreparation, apply_fitted_numeric
from ...services.model_loader import load_model
from .contract import FEATURES

ROOT = Path(__file__).resolve().parents[5]
SOURCE = ROOT / "data_prep/raw/e-commerce-customer-churn/data_ecommerce_customer_churn.csv"
NOTEBOOK = ROOT / "data_prep/training/churn_prediction.ipynb"
OUTPUT = ROOT / "backend/ml_models/churn_preprocessing.json"
# Recorded original source checksum, also present in the transformation manifest.
SOURCE_SHA256 = "d570d2a822b68af0dfa40333407386e0f442994d62f5b3d505b97c1fb5ec6a75"


def _digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _saved_scaler_preview(notebook):
    preview = None
    auc = None
    for cell in notebook["cells"]:
        source = "".join(cell.get("source", []))
        for output in cell.get("outputs", []):
            if "scaler.fit_transform" in source:
                preview = "".join(output.get("data", {}).get("text/plain", []))
            if "Selected model:" in source:
                match = re.search(r"Selected model: Random Forest \(ROC-AUC = ([\d.]+)\)", "".join(output.get("text", [])))
                if match:
                    auc = float(match.group(1))
    if preview is None or auc is None:
        raise ValueError("Original saved scaler preview and selected-model AUC are required.")
    first, last = {}, {}
    for row in preview.splitlines():
        parts = row.split()
        if not parts or parts[0] not in {"0", "1", "2", "3", "4"}:
            continue
        if len(parts) == 5:
            first[int(parts[0])] = [float(v) for v in parts[1:]]
        elif len(parts) == 4:
            last[int(parts[0])] = [float(v) for v in parts[1:3]]
    if len(first) != 5 or len(last) != 5:
        raise ValueError("The saved training preview could not be verified.")
    return np.array([first[i] + last[i] for i in range(5)]), auc


def recover():
    if _digest(SOURCE) != SOURCE_SHA256:
        raise ValueError("The source does not match the recorded original training CSV.")
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    preview, recorded_auc = _saved_scaler_preview(notebook)
    raw = pd.read_csv(SOURCE)
    if raw.shape != (3941, 11):
        raise ValueError("Unexpected original source shape.")
    # Keep the exact original order: drop columns, impute, deduplicate including
    # the label/day column, drop day, scale, then split. Do not 'correct' training
    # leakage here: doing so would change this existing model's input space.
    clean = raw.drop(columns=["MaritalStatus", "NumberOfAddress", "PreferedOrderCat"]).copy()
    numeric = [key for key in clean.select_dtypes(include=[np.number]).columns if key != "Churn"]
    imputer = SimpleImputer(strategy="median")
    clean[numeric] = imputer.fit_transform(clean[numeric])
    clean = clean.drop_duplicates()
    if len(clean) != 3015:
        raise ValueError("The original deduplication result does not match the notebook.")
    inputs = clean.drop(columns=["DaySinceLastOrder", "Churn"])
    if tuple(inputs.columns) != FEATURES:
        raise ValueError("The original feature order does not match the classifier contract.")
    scaler = StandardScaler()
    scaled = pd.DataFrame(scaler.fit_transform(inputs), columns=FEATURES, index=inputs.index)
    np.testing.assert_allclose(scaled.head(5).to_numpy(), preview, rtol=0, atol=0.00000051)
    preparation = FittedNumericPreparation(
        FEATURES,
        tuple(float(imputer.statistics_[numeric.index(key)]) for key in FEATURES),
        tuple(float(v) for v in scaler.mean_),
        tuple(float(v) for v in scaler.scale_),
    )
    np.testing.assert_allclose(apply_fitted_numeric(inputs, preparation), scaled, rtol=1e-12, atol=1e-12)
    # Also verify filling before scaling for the original rows with nulls.
    reference = raw.loc[:, list(FEATURES)].fillna(dict(zip(FEATURES, preparation.medians)))
    np.testing.assert_allclose(apply_fitted_numeric(raw.loc[:, list(FEATURES)], preparation),
                               scaler.transform(reference), rtol=1e-12, atol=1e-12)
    classifier, model_digest = load_model("churn")
    _, test_inputs, _, test_labels = train_test_split(scaled, clean["Churn"], test_size=0.2, random_state=42, stratify=clean["Churn"])
    actual_auc = roc_auc_score(test_labels, classifier.predict_proba(test_inputs)[:, list(classifier.classes_).index(1)])
    if len(test_inputs) != 603 or round(actual_auc, 4) != recorded_auc:
        raise ValueError("The existing classifier no longer reproduces the notebook's recorded evaluation.")
    versions = {"python": platform.python_version(), **{key: version(key) for key in ("numpy", "pandas", "scikit-learn", "joblib")}}
    bundle = {
        "format": "churn_numeric_preparation/v1",
        "features": list(FEATURES),
        "medians": list(preparation.medians),
        "means": list(preparation.means),
        "scales": list(preparation.scales),
        "metadata": {
            "method": "reconstructed_from_checksum_verified_original_source",
            "classifier_sha256": model_digest,
            "source_sha256": SOURCE_SHA256,
            "notebook_sha256": _digest(NOTEBOOK),
            "versions": versions,
            "source_rows": len(raw), "scaler_rows": len(clean),
            "verification": {"saved_scaler_preview": True, "full_transform_parity": True,
                             "missing_value_parity": True, "evaluation_rows": len(test_inputs),
                             "reproduced_roc_auc": float(actual_auc), "saved_roc_auc": recorded_auc},
            "limitations": "Original preprocessing used the full data before splitting; model selection reused the evaluation partition. Recovery does not fix those evaluation limitations.",
        },
    }
    # Publish only after all checks pass. The .pkl remains unchanged.
    text = json.dumps(bundle, indent=2, allow_nan=False) + "\n"
    OUTPUT.write_text(text, encoding="utf-8")
    return bundle


if __name__ == "__main__":
    bundle = recover()
    print("Saved verified churn preprocessing:", OUTPUT)
    print("Reproduced saved ROC-AUC:", round(bundle["metadata"]["verification"]["reproduced_roc_auc"], 4))
