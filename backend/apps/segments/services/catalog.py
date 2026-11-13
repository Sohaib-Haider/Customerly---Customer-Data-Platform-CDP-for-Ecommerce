"""Explicit registration of the two implemented predictors and local artifacts."""

import uuid
import json
from datetime import datetime, timezone as datetime_timezone

from django.db import connection, transaction
from psycopg.types.json import Jsonb

from ..models import SegmentDefinition
from .model_loader import load_model
from .database import db_uuid

DEFINITIONS = {
    "churn": {"name": "Predicted Churn-Risk", "description": "Customer snapshot churn classification."},
    "clv": {"name": "Future High-Value / CLV", "description": "Predicted positive purchase revenue over the next 90 days in GBP."},
}


def initialize_catalog():
    """Run explicitly by an administrator, never as a side effect of a GET."""
    from ..predictors.churn.contract import FEATURES as churn_features
    from ..predictors.churn.preparation import load_preparation
    from ..predictors.clv.contract import FEATURES as clv_features
    from ..predictors.clv.predictor import _validate_artifact

    registered = []
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [571082010])
        for key, spec in DEFINITIONS.items():
            artifact, digest = load_model(key)
            if key == "churn":
                _, preparation_metadata, _ = load_preparation(digest)
                metrics = {"artifact_sha256": digest, "recovered_preprocessing": preparation_metadata["verification"]}
                metrics["trained_at_source"] = "artifact_file_mtime_estimate"
                from .model_loader import MODEL_DIR

                trained_at = datetime.fromtimestamp((MODEL_DIR / "churn_model.pkl").stat().st_mtime, tz=datetime_timezone.utc)
                required = {"model_inputs": list(churn_features), "preparation": "saved_training_statistics"}
            else:
                _, metadata = _validate_artifact(artifact)
                metrics = {"artifact_sha256": digest, "training_status": metadata["status"],
                           "recorded_metrics": metadata.get("held_out_configuration_metrics", {})}
                trained_at = datetime.fromisoformat(metadata["trained_at_utc"])
                required = {"model_inputs": list(clv_features), "history_days": 288,
                            "horizon_days": 90, "currency": "GBP", "minimum_valid_invoices": 1}
            definition, _ = SegmentDefinition.objects.get_or_create(name=spec["name"], defaults={
                "id": uuid.uuid5(uuid.NAMESPACE_URL, "customerly:segment:" + key),
                "description": spec["description"], "segment_type": "ml", "required_fields": required,
            })
            # Existing definitions retain their metadata; never silently overwrite a teammate's configuration.
            version = "sha256-" + digest[:24]
            with connection.cursor() as cursor:
                segment_id = db_uuid(definition.id)
                cursor.execute("UPDATE ml_models SET is_active = FALSE WHERE segment_id = %s", [segment_id])
                cursor.execute("""
                        INSERT INTO ml_models (segment_id, version, model_path, trained_at, metrics, is_active)
                        VALUES (%s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (segment_id, version) DO UPDATE SET
                            model_path = EXCLUDED.model_path, metrics = EXCLUDED.metrics, is_active = TRUE
                    """, [segment_id, version, "backend/ml_models/" + key + "_model.pkl", trained_at,
                          Jsonb(metrics) if connection.vendor == "postgresql" else json.dumps(metrics)])
            registered.append({"key": key, "id": str(definition.id), "version": version})
    return registered


def get_definition(key):
    if key not in DEFINITIONS:
        raise KeyError(key)
    return SegmentDefinition.objects.get(name=DEFINITIONS[key]["name"])


def registry_matches(definition, digest):
    with connection.cursor() as cursor:
        cursor.execute("SELECT version, metrics FROM ml_models WHERE segment_id = %s AND is_active", [db_uuid(definition.id)])
        rows = cursor.fetchall()
    if len(rows) != 1:
        return False
    metrics = json.loads(rows[0][1]) if isinstance(rows[0][1], str) else rows[0][1]
    return rows[0][0] == "sha256-" + digest[:24] and metrics.get("artifact_sha256") == digest
