"""HTTP-safe adapters around the existing mapper, reviewer and apply engine."""
import hashlib
import json
from pathlib import Path
import tempfile
import time

from django.conf import settings
from django.core import signing
from rest_framework.exceptions import ValidationError

from core.api import read_csv_upload
from .apply import apply_approved
from .mapper import map_csv
from .models import MappingResult
from .review import apply_decisions, ReviewError
from .schema_catalog import get_catalog

STATE_SALT = "customerly.mapping.review.v1"
STATE_TTL = 3600


def encode_state(payload):
    return signing.dumps(payload, salt=STATE_SALT, compress=True)


def load_state(state, store_id):
    try:
        payload = signing.loads(state, salt=STATE_SALT, max_age=STATE_TTL)
        if payload["store_id"] != str(store_id) or payload["expires_at"] <= time.time():
            raise ValueError
        result = MappingResult.from_dict(payload["document"])
        for mapping in result.mappings:
            if mapping.has_target and get_catalog().validate(mapping.target_table, mapping.target_column) is None:
                raise ValueError
        return payload, result
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        raise ValidationError({"state": "Mapping state is invalid, expired, or belongs to another store. Remap the file."}) from None


def response_state(payload, result):
    payload = {**payload, "document": result.to_dict()}
    return {"document": payload["document"], "state": encode_state(payload),
            "expires_at": payload["expires_at"], "persisted": False}


def propose(store_id, upload, *, use_llm=True, kind=None):
    if not settings.SECRET_KEY or settings.SECRET_KEY.startswith("django-insecure-"):
        raise ValidationError("Configure a private DJANGO_SECRET_KEY before using mapping review.")
    raw, _, _ = read_csv_upload(upload)
    with tempfile.TemporaryDirectory(prefix="customerly-mapping-") as directory:
        path = Path(directory) / "upload.csv"
        path.write_bytes(raw)
        result = map_csv(path, use_llm=use_llm, kind_override=kind)
    result.source_file = Path(upload.name).name
    payload = {"store_id": str(store_id), "sha256": hashlib.sha256(raw).hexdigest(),
               "expires_at": time.time() + STATE_TTL}
    return response_state(payload, result)


def review(store_id, state, **decisions):
    payload, result = load_state(state, store_id)
    decisions["set_targets"] = [(item["source_column"], item["target_table"], item["target_column"])
                                for item in decisions.get("set_targets", [])]
    try:
        changes = apply_decisions(result, **decisions)
        # Approval must not leave a first-wins ambiguity for ingestion.
        targets = [(item.target_table, item.target_column) for item in result.mappings if item.status == "approved"]
        if len(targets) != len(set(targets)):
            raise ReviewError("Approved mappings contain duplicate targets; reject or correct the conflicting columns.")
    except ReviewError as exc:
        raise ValidationError({"decisions": str(exc)}) from None
    return {**response_state(payload, result), "changes": changes}


def apply(store_id, state, upload):
    payload, result = load_state(state, store_id)
    raw, _, _ = read_csv_upload(upload)
    if hashlib.sha256(raw).hexdigest() != payload["sha256"]:
        raise ValidationError({"file": "Upload the exact original CSV bytes used to propose this mapping."})
    with tempfile.TemporaryDirectory(prefix="customerly-mapping-") as directory:
        path = Path(directory) / "upload.csv"
        path.write_bytes(raw)
        output = Path(directory) / "output"
        report = apply_approved("reviewed-state", path, output, mapping_result=result)
        import csv
        tables = {}
        for filename in sorted(output.glob("*.csv")):
            with filename.open(encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                tables[filename.stem] = {"columns": next(reader), "rows": list(reader)}
        public_report = report.to_dict()
    # Local temporary paths are implementation details, not downloadable URLs.
    public_report.update(input_csv=result.source_file, output_dir=None,
                         mapping_document="reviewed-state", output_files=[])
    return {"tables": tables, "report": public_report, "persisted": False,
            "status": "applied" if tables else "no_applicable_mappings"}
