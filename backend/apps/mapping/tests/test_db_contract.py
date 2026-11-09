"""The future persistence contract: rows shaped exactly like
``ingestion_column_mappings`` (backend/schema.sql §4) — emitted, never written.
"""

from __future__ import annotations

from apps.mapping.mapper import map_csv
from apps.mapping.models import (
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_UNMAPPED,
    MappingResult,
)

EXPECTED_FIELDS = {"upload_id", "source_column", "target_table", "target_column", "confidence"}


def test_row_shape_matches_ingestion_column_mappings(catalog, csv_factory, tmp_path):
    upload_id = "3f0c4a6e-1b2d-4e8f-9a7c-5d6e7f8a9b0c"
    path = csv_factory(tmp_path, ["quantity", "zzz_internal_flag"])
    result = map_csv(path, use_llm=False, catalog=catalog)
    rows = result.to_ingestion_column_mappings_rows(upload_id)

    assert len(rows) == 1  # unmapped column emits nothing
    row = rows[0]
    assert set(row) == EXPECTED_FIELDS
    assert row["upload_id"] == upload_id
    assert row["source_column"] == "quantity"
    assert row["target_table"] == "order_items"
    assert row["target_column"] == "quantity"
    assert row["confidence"] is None  # non-LLM mapping: no fake confidence

    # every value type matches the DDL columns
    assert isinstance(row["upload_id"], str)
    assert isinstance(row["source_column"], str)
    assert row["confidence"] is None or 0 <= row["confidence"] <= 1


def test_unmapped_and_rejected_rows_are_excluded(catalog, csv_factory, tmp_path):
    upload_id = "00000000-0000-4000-8000-000000000001"
    from conftest import FakeLLM

    path = csv_factory(tmp_path, ["quantity", "hh_id"])
    fake = FakeLLM({"hh_id": ("customers", "source_customer_id", 0.91, "key")})
    result = map_csv(path, llm_client=fake, catalog=catalog)
    result.mapping_for("quantity").status = STATUS_REJECTED

    rows = result.to_ingestion_column_mappings_rows(upload_id)
    sources = {r["source_column"] for r in rows}
    assert sources == {"hh_id"}  # rejected + unmapped excluded


def test_confidence_is_rounded_to_two_decimals(catalog, csv_factory, tmp_path):
    from conftest import FakeLLM

    path = csv_factory(tmp_path, ["hh_id"])
    fake = FakeLLM({"hh_id": ("customers", "source_customer_id", 0.87654, "key")})
    result = map_csv(path, llm_client=fake, catalog=catalog)
    rows = result.to_ingestion_column_mappings_rows("x")
    assert rows[0]["confidence"] == 0.88


def test_document_roundtrip_preserves_everything(catalog, csv_factory, tmp_path):
    from conftest import FakeLLM

    path = csv_factory(
        tmp_path, ["household_key", "quantity", "zzz_internal_flag", "price"]
    )
    fake = FakeLLM(
        {"zzz_internal_flag": ("stores", "name", 0.9, "invented table target")}
    )
    result = map_csv(path, llm_client=fake, catalog=catalog)
    restored = MappingResult.from_dict(result.to_dict())
    assert restored.to_dict() == result.to_dict()
    # the schema version gate rejects foreign documents
    import json

    bad = result.to_dict()
    bad["schema"] = "something/else"
    try:
        MappingResult.from_dict(json.loads(json.dumps(bad)))
    except ValueError as exc:
        assert "unsupported mapping document version" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_status_and_method_are_separate_axes(catalog, csv_factory, tmp_path):
    """High confidence never implies approval; approval never changes method."""
    from conftest import FakeLLM

    path = csv_factory(tmp_path, ["hh_id"])
    fake = FakeLLM({"hh_id": ("customers", "source_customer_id", 0.99, "key")})
    result = map_csv(path, llm_client=fake, catalog=catalog)
    mapping = result.mapping_for("hh_id")
    assert mapping.status != STATUS_APPROVED  # no human decision has happened
    assert mapping.confidence == 0.99  # ...yet confidence is high

    mapping.status = STATUS_APPROVED
    assert mapping.method == "llm"  # approval does not rewrite how it was found
