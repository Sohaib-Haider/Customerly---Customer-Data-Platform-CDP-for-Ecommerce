"""Schema catalog: parsing, provenance gating, drift reporting, validation.

These tests pin the component to the REAL schema sources — if backend/schema.sql
or final-schema.md changes shape, they fail loudly instead of the mapper
silently working off stale assumptions.
"""

from __future__ import annotations

import pytest

from column_mapping.schema_catalog import (
    DOCUMENTED_FK_TARGETS,
    EXPECTED_TABLE_COUNT,
    INGESTIBLE_TABLES,
    parse_provenance_md,
    parse_schema_sql,
)

# Current verified state of the schema (23 tables, 82 upload-mappable fields).
# Deliberately exact: any upstream schema change should break this test so the
# mapping allowlist is re-verified, not silently widened.
# Re-verified against schema.sql @ a78ff7f (Transformation contract v2:
# NUMERIC without source precision, TIMESTAMP wall times, + order_items.source_timestamp).
CURRENT_MAPPABLE_COUNT = 82


def test_parses_all_23_tables_from_real_schema(catalog):
    assert len(catalog.tables) == EXPECTED_TABLE_COUNT == 23
    for table in INGESTIBLE_TABLES:
        assert table in catalog.tables


def test_column_nullability_and_types_come_from_ddl(catalog):
    spec = catalog.tables["order_items"]["sales_value"]
    # contract v2: source precision is not declared in DDL
    assert spec.sql_type == "NUMERIC"
    assert spec.nullable is False
    assert spec.provenance == "[S]"
    assert spec.fk_reference is None

    fk = catalog.tables["order_items"]["product_id"]
    assert fk.fk_reference == "products"


def test_mappable_count_is_verified_against_current_schema(catalog):
    assert catalog.mappable_count == CURRENT_MAPPABLE_COUNT


def test_platform_and_computed_fields_are_not_mappable(catalog):
    # platform tables are structurally excluded (not in INGESTIBLE_TABLES)
    assert catalog.validate("stores", "name") is None
    assert catalog.validate("store_users", "email") is None
    assert catalog.validate("ingestion_uploads", "original_filename") is None
    # every customer_features column is provenance [C]
    assert catalog.validate("customer_features", "recency") is None
    # ML / workflow / segment tables
    assert catalog.validate("ml_models", "feature_importance") is None
    assert catalog.validate("workflows", "name") is None
    assert catalog.validate("customer_segments", "version") is None
    # [P] columns are not source-mappable
    assert catalog.validate("orders", "id") is None
    assert catalog.validate("customers", "created_at") is None
    # 'Decision Required' provenance is excluded (Open Decision #4)
    assert catalog.validate("orders", "region") is None


def test_documented_fields_are_mappable(catalog):
    customer_id = catalog.validate("customers", "source_customer_id")
    assert customer_id is not None and customer_id.kind == "direct"
    sales = catalog.validate("order_items", "sales_value")
    assert sales is not None and sales.requires == ()
    day_no = catalog.validate("orders", "day_no")
    assert day_no is not None


def test_documented_fk_targets_are_relationship_kind(catalog):
    events_customer = catalog.validate("events", "customer_id")
    assert events_customer is not None
    assert events_customer.kind == "relationship"
    assert "relationship_resolution" in events_customer.requires
    assert "user_id" in events_customer.citation
    assert catalog.validate("coupon_redemptions", "campaign_id").kind == "relationship"


def test_validation_rejects_invented_names(catalog):
    assert catalog.validate("customers", "loyalty_points") is None  # not in schema
    assert catalog.validate("nonexistent_table", "id") is None
    assert catalog.validate(None, None) is None
    assert catalog.validate("orders", None) is None


def test_every_fk_exception_is_a_real_fk(catalog):
    for qualified, meta in DOCUMENTED_FK_TARGETS.items():
        table, _, column = qualified.partition(".")
        spec = catalog.tables[table][column]
        assert spec.fk_reference is not None, f"{qualified} is not an FK in the DDL"
        assert table in INGESTIBLE_TABLES
        assert meta.citation, f"{qualified} lacks a citation"


def test_drift_report_finds_the_known_region_open_decision(catalog):
    findings = catalog.verify()
    # The one documented, unresolved SQL-vs-markup provenance gap:
    assert len(findings) == 1, findings
    assert findings[0].startswith("orders.region has unmarked provenance")


def test_alias_dictionary_agrees_with_schema(catalog):
    assert catalog.verify_aliases() == []


def test_parsers_are_independent_functions():
    tables = parse_schema_sql()
    md = parse_provenance_md()
    assert set(md) <= set(tables)  # every markdown section is a real SQL table
    assert md["customers"]["source_customer_id"][0] == "[S]"


def test_prompt_targets_only_mappable_fields(catalog):
    lines = catalog.prompt_targets()
    assert len(lines) == CURRENT_MAPPABLE_COUNT
    assert any(line.startswith("customers.source_customer_id") for line in lines)
    assert not any(line.startswith("customers.created_at") for line in lines)
    assert not any("customer_features." in line for line in lines)
    # relationship targets are labelled so the model knows they need resolution
    assert any("(relationship)" in line for line in lines)
