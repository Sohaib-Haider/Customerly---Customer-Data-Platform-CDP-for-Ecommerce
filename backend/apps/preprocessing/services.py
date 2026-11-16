"""Bounded, report-first adapter for HTTP and future ingestion callers."""
import json
import pandas as pd
from rest_framework.exceptions import APIException, ValidationError

from core.api import read_csv_upload, validate_table
from .pipeline import run_pipeline
from .preprocessing import clean_column_names
from .serializers import validate_config


class ProcessingFailed(APIException):
    status_code = 422
    default_detail = "Preprocessing failed; inspect the report. No data was saved."

    def __init__(self, detail):
        # Keep report counts/booleans typed; APIException's usual conversion
        # recursively converts every scalar into an ErrorDetail string.
        self.detail = detail


def run(*, file=None, columns=None, rows=None, config=None):
    config = validate_config(config or {})
    if file is not None:
        _, columns, rows = read_csv_upload(file)
        # Blank CSV cells are missing; literal values such as NA remain strings.
        rows = [[None if value == "" else value for value in row] for row in rows]
    validate_table(columns, rows)
    frame = pd.DataFrame(rows, columns=columns)
    effective_columns = list(clean_column_names(frame).columns) if config["clean_column_names"]["enabled"] else columns
    if any(not name for name in effective_columns) or len(set(effective_columns)) != len(effective_columns):
        raise ValidationError({"columns": "Column cleaning would create blank or duplicate names; rename the input columns."})
    # Reject misspelled configured columns before functions silently skip them.
    available = set(effective_columns)
    for step, options in config.items():
        if not isinstance(options, dict) or not options.get("enabled", True):
            continue
        referenced = options.get("columns") or options.get("subset") or []
        referenced = [*referenced, *options.get("type_map", {})]
        if set(referenced) - available:
            raise ValidationError({"config": f"{step} refers to columns absent after column-name cleaning."})
    cleaned, report = run_pipeline(frame.copy(deep=True), config=config, generate_report=True)
    errors = [step for step, details in report["per_step"].items() if details["status"] == "error"]
    if errors:
        # The legacy Python pipeline continues after errors. The HTTP adapter
        # refuses to mark that partial result ready for ingestion.
        raise ProcessingFailed({"detail": "Preprocessing steps failed; no cleaned data was accepted or saved.", "report": report})
    if not len(cleaned.columns) or not len(cleaned):
        raise ProcessingFailed({"detail": "Cleaning left no rows or columns. Review constant-column and missing-value settings.", "report": report})
    required = config["validate_required_columns"]
    if required["enabled"]:
        present = set(required["required_columns"]).issubset(cleaned.columns)
        report["per_step"]["validate_required_columns"]["all_present"] = present
        if not present:
            raise ProcessingFailed({"detail": "Required columns are absent from the final cleaned output.", "report": report})
    # pandas produces strict JSON nulls for NaN/NaT and ISO timestamps.
    table = json.loads(cleaned.to_json(orient="split", date_format="iso", date_unit="us", double_precision=15))
    return {"status": "completed", "columns": table["columns"], "rows": table["data"],
            "row_count": len(cleaned), "report": report, "persisted": False,
            "remaining_missing_values": int(cleaned.isna().sum().sum())}
