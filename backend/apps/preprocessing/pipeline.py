"""Pipeline orchestrator for the preprocessing service.

Chains individual preprocessing functions in a logical order and
optionally generates a JSON summary report of what was done.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .preprocessing import (
    clean_column_names,
    fix_data_types,
    handle_missing_values,
    handle_outliers,
    infer_column_types,
    parse_dates,
    remove_constant_columns,
    remove_duplicates,
    trim_whitespace,
    validate_required_columns,
)

# Default execution order: structural -> typing -> cleaning -> transformation
DEFAULT_STEPS: List[str] = [
    "clean_column_names",
    "trim_whitespace",
    "validate_required_columns",
    "infer_column_types",
    "remove_constant_columns",
    "remove_duplicates",
    "fix_data_types",
    "parse_dates",
    "handle_missing_values",
    "handle_outliers",
]


def default_config() -> Dict[str, Any]:
    """Return a sensible default preprocessing configuration.

    All steps are enabled with conservative defaults:
    - Missing values: type-aware (median for numeric, mode for categorical)
    - Duplicates: removed (keep first)
    - Outliers: IQR method, cap action
    Model-specific encoding and scaling belong to the segments app.
    """
    return {
        "clean_column_names": {"enabled": True},
        "trim_whitespace": {"enabled": True, "columns": None},
        "validate_required_columns": {
            "enabled": True,
            "required_columns": [],
            "raise_on_missing": True,
        },
        "infer_column_types": {"enabled": True, "categorical_threshold": 0.5},
        "remove_constant_columns": {"enabled": True},
        "remove_duplicates": {"enabled": True, "subset": None, "keep": "first"},
        "fix_data_types": {"enabled": True, "type_map": {}},
        "parse_dates": {"enabled": True, "columns": None, "formats": None},
        "handle_missing_values": {
            "enabled": True,
            "strategy": "type_aware",
            "columns": None,
            "fill_value": None,
        },
        "handle_outliers": {
            "enabled": True,
            "columns": None,
            "method": "iqr",
            "action": "cap",
            "z_threshold": 3.0,
        },
        "generate_report": False,
    }


def run_pipeline(
    df: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    generate_report: bool = False,
) -> Tuple[pd.DataFrame, Optional[Dict[str, Any]]]:
    """Run the full preprocessing pipeline on a DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    config : dict, optional
        Per-step configuration. Missing steps fall back to
        ``default_config()``. Set ``generate_report`` at top level
        or inside config.
    generate_report : bool
        If True, return a JSON-serializable report dict alongside
        the cleaned DataFrame.

    Returns
    -------
    tuple
        (cleaned_df, report_dict_or_none)
    """
    cfg = default_config()
    if config:
        cfg.update(config)

    do_report = generate_report or cfg.get("generate_report", False)

    report: Dict[str, Any] = {
        "steps_executed": [],
        "rows_before": len(df),
        "columns_before": df.shape[1],
        "per_step": {},
    }

    inferred_types: Optional[Dict[str, str]] = None
    original_columns = list(df.columns)

    for step in DEFAULT_STEPS:
        step_cfg = cfg.get(step, {})
        if not step_cfg.get("enabled", True):
            continue

        step_report: Dict[str, Any] = {}
        df_before = df.copy()

        try:
            if step == "clean_column_names":
                df = clean_column_names(df)
                step_report["renamed_columns"] = {
                    orig: new for orig, new in zip(original_columns, df.columns) if orig != new
                }

            elif step == "trim_whitespace":
                df = trim_whitespace(df, columns=step_cfg.get("columns"))

            elif step == "validate_required_columns":
                required = step_cfg.get("required_columns", [])
                raise_on_missing = step_cfg.get("raise_on_missing", True)
                validate_required_columns(df, required, raise_on_missing=raise_on_missing)
                step_report["required_columns"] = required
                step_report["all_present"] = True

            elif step == "infer_column_types":
                threshold = step_cfg.get("categorical_threshold", 0.5)
                inferred_types = infer_column_types(df, categorical_threshold=threshold)
                step_report["column_types"] = inferred_types

            elif step == "remove_constant_columns":
                before_cols = df.shape[1]
                df = remove_constant_columns(df)
                after_cols = df.shape[1]
                step_report["removed_columns"] = before_cols - after_cols

            elif step == "remove_duplicates":
                before_rows = len(df)
                df = remove_duplicates(
                    df,
                    subset=step_cfg.get("subset"),
                    keep=step_cfg.get("keep", "first"),
                )
                after_rows = len(df)
                step_report["removed_rows"] = before_rows - after_rows

            elif step == "fix_data_types":
                type_map = step_cfg.get("type_map", {})
                df = fix_data_types(df, type_map=type_map, inferred_types=inferred_types)

            elif step == "parse_dates":
                df = parse_dates(
                    df,
                    columns=step_cfg.get("columns"),
                    formats=step_cfg.get("formats"),
                    inferred_types=inferred_types,
                )

            elif step == "handle_missing_values":
                before_nulls = int(df.isna().sum().sum())
                strategy = step_cfg.get("strategy", "type_aware")
                # Map the string "type_aware" to None so preprocessing.py
                # applies its own type-aware logic
                strat = None if strategy == "type_aware" else strategy
                df = handle_missing_values(
                    df,
                    strategy=strat,
                    columns=step_cfg.get("columns"),
                    fill_value=step_cfg.get("fill_value"),
                    type_aware=(strategy == "type_aware"),
                    inferred_types=inferred_types,
                )
                after_nulls = int(df.isna().sum().sum())
                step_report["nulls_before"] = before_nulls
                step_report["nulls_after"] = after_nulls
                step_report["nulls_handled"] = before_nulls - after_nulls

            elif step == "handle_outliers":
                df = handle_outliers(
                    df,
                    columns=step_cfg.get("columns"),
                    method=step_cfg.get("method", "iqr"),
                    action=step_cfg.get("action", "cap"),
                    z_threshold=step_cfg.get("z_threshold", 3.0),
                    inferred_types=inferred_types,
                )

            step_report["status"] = "success"
            step_report["rows_after"] = len(df)
            step_report["columns_after"] = df.shape[1]

        except Exception as exc:  # noqa: BLE001
            step_report["status"] = "error"
            step_report["error"] = str(exc)
            # Continue to next step rather than aborting the whole pipeline
            # so partial results are still returned.

        report["steps_executed"].append(step)
        report["per_step"][step] = step_report

    report["rows_after"] = len(df)
    report["columns_after"] = df.shape[1]
    report["column_types"] = inferred_types or {}

    if do_report:
        return df, report
    return df, None


def preprocess_csv(
    input_path: str,
    output_path: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    chunksize: Optional[int] = None,
    backup: bool = False,
    generate_report: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Read a CSV, preprocess it, and write the result back.

    Parameters
    ----------
    input_path : str
        Path to the input CSV.
    output_path : str, optional
        Path to write the cleaned CSV. If None, overwrites input_path.
    config : dict, optional
        Pipeline configuration (see default_config()).
    chunksize : int, optional
        If set, read the CSV in chunks of this size. Each chunk is
        preprocessed independently and written to the output. Note:
        type inference and outlier detection are computed on the
        first chunk only and applied to subsequent chunks.
    backup : bool
        If True, copy input_path to input_path + ".bak" before
        overwriting. Only relevant when output_path is None.
    generate_report : bool
        If True, write a JSON report alongside the output CSV
        (output_path + ".report.json" or input_path + ".report.json").

    Returns
    -------
    tuple
        (output_path, report_dict_or_none)
    """
    import shutil
    import tempfile

    out_path = output_path or input_path

    # Optional backup before overwriting
    if backup and out_path == input_path:
        bak_path = input_path + ".bak"
        shutil.copy2(input_path, bak_path)

    # When overwriting in-place with chunks, write to a temp file first
    # to avoid truncating the input while still reading from it.
    inplace = out_path == input_path
    actual_out = out_path
    if inplace and chunksize is not None:
        actual_out = out_path + ".tmp"

    if chunksize is None:
        # Single-pass: read whole file
        df = pd.read_csv(input_path)
        df, report = run_pipeline(df, config=config, generate_report=generate_report)
        df.to_csv(actual_out, index=False)
    else:
        # Chunked processing for large files
        first_chunk = True
        inferred_types: Optional[Dict[str, Any]] = None
        report = None

        reader = pd.read_csv(input_path, chunksize=chunksize)
        writer_initialized = False

        for chunk in reader:
            # Build config that propagates inferred_types from first chunk
            chunk_cfg = dict(config or default_config())
            if inferred_types is not None:
                # Propagate inferred types to subsequent chunks
                for step in ("fix_data_types", "parse_dates",
                             "handle_missing_values", "handle_outliers"):
                    sc = chunk_cfg.get(step, {})
                    sc["inferred_types"] = inferred_types

            chunk, chunk_report = run_pipeline(
                chunk, config=chunk_cfg, generate_report=True
            )

            # Capture inferred types from first chunk for subsequent ones
            if first_chunk:
                inferred_types = chunk_report.get("column_types")
                report = chunk_report
                first_chunk = False

            # Write chunk
            mode = "w" if not writer_initialized else "a"
            header = not writer_initialized
            chunk.to_csv(actual_out, mode=mode, header=header, index=False)
            writer_initialized = True

    # If we wrote to a temp file, replace the original
    if inplace and chunksize is not None:
        shutil.move(actual_out, out_path)

    if generate_report and report is not None:
        report_path = out_path + ".report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

    return out_path, report
