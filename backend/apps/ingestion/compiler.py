"""Compile approved CSV rows into validated, tenant-scoped canonical records.

Identity and Decimal values never go through pandas numeric inference. Only
approved ordinary descriptive columns enter the shared cleaning functions;
protected values remain attached to the original row index.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException, localcontext
import hashlib
import io
import re
import uuid

import pandas as pd
from django.db import connection
from rest_framework.exceptions import ValidationError

from apps.mapping.models import MappingResult
from apps.mapping.schema_catalog import get_catalog, INGESTIBLE_TABLES
from apps.preprocessing.pipeline import run_pipeline
from .storage import storage

NATURAL = {"customers": "source_customer_id", "products": "source_product_id", "orders": "source_order_id",
           "marketing_campaigns": "source_campaign_id", "coupon_campaigns": "source_campaign_id", "message_sends": "source_message_id"}
ORDER = list(INGESTIBLE_TABLES)
ORDER.remove("order_items")
ORDER.insert(ORDER.index("orders") + 1, "order_items")
DEFAULTS = {"retail_disc": Decimal(0), "coupon_disc": Decimal(0), "coupon_match_disc": Decimal(0), "other_discount": Decimal(0)}
FALSE_FIELDS = {"is_opened", "is_clicked", "is_unsubscribed", "is_hard_bounced", "is_soft_bounced", "is_complained", "is_blocked", "is_purchased"}
CHANNELS = {"email": "email", "sms": "sms", "web_push": "push", "mobile_push": "push", "push": "push", "whatsapp": "whatsapp"}


class InvalidBatch(ValidationError):
    pass


def fail(message, *, file=None, row=None, field=None):
    raise InvalidBatch({"detail": message, "file_id": str(file.id) if file else None, "row": row, "field": field})


def identity(store, table, source_key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"customerly:ingestion:{store}:{table}:{source_key}"))


def qualified(source, value):
    return f"{source.namespace}:{value}"


def blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def convert(value, spec):
    if blank(value):
        return None
    kind = spec.sql_type
    if kind.startswith(("VARCHAR", "TEXT")):
        result = str(value).strip()
        match = re.search(r"\((\d+)\)", kind)
        if match and len(result) > int(match[1]):
            raise ValueError("String exceeds the schema's maximum length.")
        return result
    if kind.startswith(("NUMERIC", "INTEGER", "SMALLINT", "BIGINT")):
        number = Decimal(str(value).strip())
        if not number.is_finite():
            raise ValueError("Numeric value must be finite.")
        if number.adjusted() > 131071 or number.as_tuple().exponent < -16383:
            raise ValueError("Numeric exceeds PostgreSQL's supported precision/scale.")
        if kind in {"INTEGER", "SMALLINT", "BIGINT"}:
            bits = {"INTEGER": 32, "SMALLINT": 16, "BIGINT": 64}[kind]
            if number != number.to_integral_value() or not -(2 ** (bits - 1)) <= number < 2 ** (bits - 1):
                raise ValueError("Integer is fractional or outside the schema range.")
            return int(number)
        precision = re.search(r"\((\d+),(\d+)\)", kind)
        if precision:
            total, scale = map(int, precision.groups())
            if abs(number) >= Decimal(10) ** (total - scale) or number != number.quantize(Decimal(1).scaleb(-scale)):
                raise ValueError("Numeric precision/scale exceeds the schema; rounding is not automatic.")
        return number
    if kind == "BOOLEAN":
        normalized = str(value).strip().lower()
        if normalized not in {"true", "false", "1", "0"}:
            raise ValueError("Boolean must be true/false or 1/0.")
        return normalized in {"true", "1"}
    if kind == "DATE":
        return date.fromisoformat(str(value).strip())
    if kind.startswith("TIMESTAMP"):
        timestamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if kind == "TIMESTAMPTZ" and timestamp.tzinfo is None:
            raise ValueError("This timestamp requires an explicit time zone offset.")
        if kind == "TIMESTAMP" and timestamp.tzinfo is not None:
            raise ValueError("This source-local timestamp must not contain a time zone offset.")
        return timestamp
    raise ValueError(f"Unsupported upload type {kind}.")


def read_file(file):
    import csv
    with storage().open(file.storage_name, "rb") as handle:
        raw = handle.read()
    if hashlib.sha256(raw).hexdigest() != file.sha256:
        fail("Stored upload checksum does not match; upload the original again.", file=file)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    return list(reader)


def selection(file, decisions):
    catalog = get_catalog()
    document = MappingResult.from_dict(file.approved_document)
    assignments = {}
    approved_columns = set()
    for item in document.mappings:
        if item.status != "approved":
            continue
        approved_columns.add(item.source_column)
        if item.has_target:
            assignments[(item.target_table, item.target_column)] = {"sources": [item.source_column], "operation": "value"}
            if item.requires and not set(item.requires).issubset({"relationship_resolution"}):
                assignments[(item.target_table, item.target_column)]["operation"] = "unresolved"
    for binding in decisions.get("bindings", []):
        target = (binding["target_table"], binding["target_column"])
        spec = catalog.tables.get(target[0], {}).get(target[1])
        if target[0] not in INGESTIBLE_TABLES or spec is None or not spec.fk_reference or target[1] in {"store_id", "workflow_execution_id"}:
            fail("Bindings must target a supported source relationship, never tenant/platform ownership.", file=file)
        assignments[target] = {"sources": [binding["source_column"]], "operation": "value"}
        approved_columns.add(binding["source_column"])
    for calculation in decisions.get("calculations", []):
        target = (calculation["target_table"], calculation["target_column"])
        operation = calculation["operation"]
        allowed = operation == "multiply" and target == ("order_items", "sales_value")
        allowed |= operation == "date" and target == ("orders", "order_date")
        allowed |= operation == "channel_norm" and target == ("message_sends", "channel_norm")
        if not allowed or len(calculation["source_columns"]) != (2 if operation == "multiply" else 1):
            fail("Unsupported calculation. Use multiplication for line amounts, date extraction, or channel normalization.", file=file)
        assignments[target] = {"sources": calculation["source_columns"], "operation": operation, "format": calculation.get("format")}
        approved_columns.update(calculation["source_columns"])
    for table, columns in decisions.get("identity_columns", {}).items():
        if table not in INGESTIBLE_TABLES or table in NATURAL or table == "holidays":
            fail("Identity columns apply only to occurrence tables without a natural source ID.", file=file)
        approved_columns.update(columns)
    if not assignments:
        fail("No approved mapped fields or relationships in this file.", file=file)
    source_columns = set(document.source_columns)
    if not approved_columns.issubset(source_columns):
        fail("A calculation, identity or relationship references an absent source column.", file=file)
    if any(item["operation"] == "unresolved" for item in assignments.values()):
        fail("An approved field requires a calculation that has not been explicitly specified.", file=file)
    return assignments, approved_columns


def clean_rows(file, decisions, assignments, approved_columns):
    original = read_file(file)
    # Only ordinary descriptions can use inference/filling. Identity, time,
    # numeric, Boolean, and relationship values use strict schema conversion.
    descriptive = set()
    protected = set()
    catalog = get_catalog()
    for (table, column), assignment in assignments.items():
        spec = catalog.tables[table][column]
        is_description = (spec.sql_type.startswith(("VARCHAR", "TEXT")) and not column.endswith("_id")
                          and column not in {"session_id", "coupon_id"} and not spec.fk_reference)
        (descriptive if is_description and assignment["operation"] == "value" else protected).update(assignment["sources"])
    protected.update(column for columns in decisions.get("identity_columns", {}).values() for column in columns)
    descriptive -= protected
    selected = [{column: (None if blank(row[column]) else row[column].strip()) for column in approved_columns} for row in original]
    report = {"rows_before": len(selected), "removed_duplicate_rows": 0, "protected_columns": sorted(protected),
              "policy": "preserve_ids_amounts_dates_booleans_and_occurrences", "cleaning": None}
    if descriptive:
        frame = pd.DataFrame([{column: row[column] for column in descriptive} for row in selected])
        config = {"clean_column_names": {"enabled": False}, "infer_column_types": {"enabled": False},
                  "remove_constant_columns": {"enabled": False}, "remove_duplicates": {"enabled": False},
                  "fix_data_types": {"enabled": False}, "parse_dates": {"enabled": False},
                  "handle_outliers": {"enabled": False}, "handle_missing_values": {"enabled": False}}
        cleaned, cleaning_report = run_pipeline(frame, config=config, generate_report=True)
        if any(value["status"] == "error" for value in cleaning_report["per_step"].values()):
            fail("Preprocessing failed; inspect the file cleaning report.", file=file)
        for index, row in cleaned.iterrows():
            for column in descriptive:
                value = row[column]
                selected[index][column] = None if pd.isna(value) else str(value)
        report["cleaning"] = cleaning_report
    retained = []
    seen = set()
    for index, row in enumerate(selected, start=2):
        key = tuple((column, row[column]) for column in sorted(approved_columns))
        if decisions.get("deduplicate") and key in seen:
            report["removed_duplicate_rows"] += 1
            continue
        seen.add(key)
        retained.append((index, row))
    report["rows_after"] = len(retained)
    return retained, report


def compile_batch(batch, progress=None):
    catalog = get_catalog()
    records = {table: {} for table in INGESTIBLE_TABLES}
    reports = {}
    warnings = []
    review = {str(item["file_id"]): item for item in batch.review["files"]}
    now = datetime.now(timezone.utc)
    for file in batch.files.order_by("id"):
        decisions = review[str(file.id)]
        assignments, columns = selection(file, decisions)
        cleaned, reports[str(file.id)] = clean_rows(file, decisions, assignments, columns)
        if progress is not None:
            progress(file, reports[str(file.id)])
        tables = {table for table, column in assignments}
        for row_number, row in cleaned:
            by_table = defaultdict(dict)
            for (table, column), assignment in assignments.items():
                values = [row[source] for source in assignment["sources"]]
                value = values[0]
                try:
                    if assignment["operation"] == "multiply":
                        if any(blank(item) for item in values):
                            value = None
                        else:
                            left, right = Decimal(values[0]), Decimal(values[1])
                            with localcontext() as context:
                                context.prec = max(28, len(left.as_tuple().digits) + len(right.as_tuple().digits))
                                value = left * right
                    elif assignment["operation"] == "date":
                        value = None if blank(value) else (datetime.strptime(value, assignment["format"]) if assignment.get("format")
                            else datetime.fromisoformat(value.replace("Z", "+00:00"))).date().isoformat()
                    elif assignment["operation"] == "channel_norm":
                        value = CHANNELS.get(str(value).lower()) if not blank(value) else None
                        if value is None:
                            raise ValueError("Unknown channel normalization.")
                    spec = catalog.tables[table][column]
                    # Relationship source keys are strings, not uploaded UUIDs.
                    by_table[table][column] = (None if blank(value) else str(value).strip()) if spec.fk_reference else convert(value, spec)
                except (ValueError, TypeError, DecimalException, OverflowError):
                    fail("Invalid value or unsupported precision for the canonical field.", file=file, row=row_number, field=f"{table}.{column}")
            for table, values in by_table.items():
                if all(value is None for value in values.values()):
                    continue
                if table == "holidays":
                    key = f"{values.get('date')}:{values.get('holiday')}"
                elif table in NATURAL:
                    source_key = values.get(NATURAL[table])
                    if source_key is None:
                        fail("A source identifier is required for this table.", file=file, row=row_number, field=f"{table}.{NATURAL[table]}")
                    values[NATURAL[table]] = qualified(batch.source, source_key)
                    if len(values[NATURAL[table]]) > 255:
                        fail("Namespaced source identifier exceeds 255 characters.", file=file, row=row_number)
                    key = values[NATURAL[table]]
                else:
                    identity_columns = decisions.get("identity_columns", {}).get(table)
                    if identity_columns:
                        identity_values = [row[column] for column in identity_columns]
                        if any(blank(value) for value in identity_values):
                            fail("A selected occurrence identity is missing.", file=file, row=row_number)
                        import json
                        key = qualified(batch.source, json.dumps(identity_values, separators=(",", ":")))
                    else:
                        key = qualified(batch.source, f"file:{file.sha256}:row:{row_number}")
                        warning = f"{file.id}/{table}: file-row identity; reordered or changed files are new occurrences. Supply identity_columns for updates."
                        if warning not in warnings:
                            warnings.append(warning)
                previous = records[table].get(key)
                if previous:
                    for column, value in values.items():
                        if column in previous and previous[column] != value and value is not None and previous[column] is not None:
                            fail("Conflicting records share the same identity across the batch.", file=file, row=row_number, field=f"{table}.{column}")
                        if value is not None:
                            previous[column] = value
                else:
                    records[table][key] = values
    if not any(records.values()):
        fail("The approved fields produced no canonical records; review empty source rows and selected identifiers.")
    # Give natural records stable IDs, reusing existing canonical identities.
    for table, collection in records.items():
        for key, values in collection.items():
            if table == "holidays":
                continue
            existing_id = None
            if table in NATURAL:
                existing = fetch(table, f'"store_id" = %s AND "{NATURAL[table]}" = %s', [str(batch.store_id), key])
                existing_id = existing[0]["id"] if existing else None
            values["id"] = str(existing_id) if existing_id else identity(batch.store_id, table, key)
    for table, collection in records.items():
        for key, values in collection.items():
            specs = catalog.tables[table]
            for column, value in list(values.items()):
                referenced = specs[column].fk_reference if column in specs else None
                if referenced and value is not None:
                    lookup = qualified(batch.source, value)
                    target = records[referenced].get(lookup)
                    if target:
                        values[column] = target["id"]
                    else:
                        natural = NATURAL.get(referenced)
                        existing = fetch(referenced, f'"store_id" = %s AND "{natural}" = %s', [str(batch.store_id), lookup]) if natural else []
                        if not existing:
                            fail("A relationship cannot be resolved in this batch or source/store.", field=f"{table}.{column}")
                        values[column] = str(existing[0]["id"])
            if table == "holidays":
                existing = fetch(table, '"date" = %s AND "holiday" = %s', [values.get("date"), values.get("holiday")])
                if not existing:
                    fail("Holidays are shared reference data; only existing calendar rows may be referenced.")
                values["id"] = str(existing[0]["id"])
                continue
            if "store_id" in specs:
                values["store_id"] = str(batch.store_id)
            if table == "message_sends" and "channel_norm" not in values:
                fail("Approve an explicit channel_norm calculation for message records.")
            existing = fetch(table, '"id" = %s', [values["id"]])
            old = existing[0] if existing else {}
            if "updated_at" in specs:
                values["updated_at"] = now
            for column, spec in specs.items():
                if column in values or column in old:
                    continue
                if column in {"created_at", "updated_at"}:
                    values[column] = now
                elif table == "order_items" and column in DEFAULTS:
                    values[column] = DEFAULTS[column]
                elif table == "message_sends" and column in FALSE_FIELDS:
                    values[column] = False
                elif not spec.nullable:
                    fail("A required canonical field is absent.", field=f"{table}.{column}")
            # Blank uploaded cells do not erase existing non-null values.
            for column in list(values):
                if values[column] is None and old.get(column) is not None:
                    del values[column]
            merged = {**old, **values}
            for column, spec in specs.items():
                if not spec.nullable and merged.get(column) is None:
                    fail("A required canonical field has no value.", field=f"{table}.{column}")
            if table == "order_items":
                order_id = merged["order_id"]
                order = next((item for item in records["orders"].values() if item["id"] == order_id), None)
                order = {**(fetch("orders", '"id" = %s AND "store_id" = %s', [order_id, str(batch.store_id)])[0] if order is None else {}), **(order or {})}
                if merged.get("source_timestamp") and merged["source_timestamp"].date().isoformat() != str(order["order_date"]):
                    fail("Order-line and order-header calendar dates disagree.")
            values["_existing"] = bool(old)
            if old.get("customer_id") is not None:
                values["_previous_customer_id"] = str(old["customer_id"])
            if table == "order_items" and old.get("order_id") is not None:
                values["_previous_order_id"] = str(old["order_id"])
    return records, {"files": reports, "warnings": warnings, "counts": {table: len(items) for table, items in records.items() if items}}


def fetch(table, where, params):
    assert table in INGESTIBLE_TABLES
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT * FROM "{table}" WHERE {where}', params)
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def save_records(records):
    def parameter(value):
        return str(value) if connection.vendor == "sqlite" and isinstance(value, Decimal) else value
    for table in ORDER:
        if table == "holidays":
            continue
        for record in records[table].values():
            values = {key: value for key, value in record.items() if not key.startswith("_")}
            exists = record["_existing"]
            with connection.cursor() as cursor:
                if exists:
                    updates = {key: value for key, value in values.items() if key not in {"id", "store_id", "created_at"}}
                    if updates:
                        cursor.execute(f'UPDATE "{table}" SET ' + ", ".join(f'"{key}" = %s' for key in updates) + ' WHERE "id" = %s', [*[parameter(value) for value in updates.values()], values["id"]])
                else:
                    columns = ", ".join(f'"{key}"' for key in values)
                    cursor.execute(f'INSERT INTO "{table}" ({columns}) VALUES (' + ", ".join(["%s"] * len(values)) + ")", [parameter(value) for value in values.values()])
