"""Data preprocessing functions for user-uploaded CSVs.

All functions operate on pandas DataFrames and return a DataFrame.
They are composable and can be chained in any order via run_pipeline.

Column-to-schema mapping, ID namespacing, and store_id tagging are
handled by the LLM mapping service — NOT by this module.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------

def infer_column_types(df: pd.DataFrame, categorical_threshold: float = 0.5) -> Dict[str, str]:
    """Auto-detect the type of each column in a DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    categorical_threshold : float
        Fraction of unique values below which a string column is
        considered categorical (default 0.5). A string column with
        fewer unique values than this ratio of total rows is typed
        as ``categorical``; otherwise ``text``.

    Returns
    -------
    dict
        Mapping of column name -> type string. Types are one of:
        ``numeric``, ``categorical``, ``datetime``, ``text``.
    """
    types: Dict[str, str] = {}
    n_rows = len(df)

    for col in df.columns:
        series = df[col]

        # Drop nulls for type inference
        non_null = series.dropna()
        if len(non_null) == 0:
            types[col] = "text"
            continue

        # Try numeric
        numeric_series = pd.to_numeric(non_null, errors="coerce")
        numeric_success = numeric_series.notna().sum() / len(non_null)
        if numeric_success >= 0.8:
            types[col] = "numeric"
            continue

        # Try datetime
        try:
            dt_series = pd.to_datetime(non_null, errors="coerce")
            dt_success = dt_series.notna().sum() / len(non_null)
            if dt_success >= 0.8:
                types[col] = "datetime"
                continue
        except (ValueError, TypeError):
            pass

        # String-like column: categorical vs text
        unique_ratio = non_null.nunique() / len(non_null)
        if unique_ratio <= categorical_threshold:
            types[col] = "categorical"
        else:
            types[col] = "text"

    return types


# ---------------------------------------------------------------------------
# Column name cleaning
# ---------------------------------------------------------------------------

def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize DataFrame column names.

    - Strips leading/trailing whitespace
    - Lowercases
    - Replaces spaces and special characters with underscores
    - Collapses multiple underscores into one
    - Strips leading/trailing underscores
    """
    df = df.copy()
    new_names: List[str] = []
    for col in df.columns:
        name = str(col).strip().lower()
        name = "".join(c if c.isalnum() else "_" for c in name)
        name = "_".join(filter(None, name.split("_")))
        new_names.append(name)

    # Handle collisions after cleaning
    seen: Dict[str, int] = {}
    final_names: List[str] = []
    for name in new_names:
        if name in seen:
            seen[name] += 1
            final_names.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            final_names.append(name)

    df.columns = final_names
    return df


# ---------------------------------------------------------------------------
# Whitespace trimming
# ---------------------------------------------------------------------------

def trim_whitespace(df: pd.DataFrame, columns: Optional[List[str]] = None) -> pd.DataFrame:
    """Strip leading/trailing whitespace from string/object columns.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    columns : list of str, optional
        Specific columns to trim. If None, all object/string columns
        are trimmed.
    """
    df = df.copy()
    if columns is None:
        columns = df.select_dtypes(include=["object", "string"]).columns.tolist()

    for col in columns:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            # Replace literal "nan"/"None" strings back to NaN
            df[col] = df[col].replace({"nan": np.nan, "None": np.nan, "": np.nan})

    return df


# ---------------------------------------------------------------------------
# Required column validation
# ---------------------------------------------------------------------------

def validate_required_columns(
    df: pd.DataFrame, required_columns: List[str], raise_on_missing: bool = True
) -> bool:
    """Check that all required columns exist in the DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    required_columns : list of str
        Column names that must be present.
    raise_on_missing : bool
        If True, raise ValueError when a column is missing. If False,
        return False instead.

    Returns
    -------
    bool
        True if all required columns are present.

    Raises
    ------
    ValueError
        If raise_on_missing is True and a required column is absent.
    """
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        msg = f"Missing required columns: {missing}"
        if raise_on_missing:
            raise ValueError(msg)
        logger.warning(msg)
        return False
    return True


# ---------------------------------------------------------------------------
# Constant column removal
# ---------------------------------------------------------------------------

def remove_constant_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop columns where all non-null values are identical."""
    df = df.copy()
    constant_cols: List[str] = []
    for col in df.columns:
        non_null = df[col].dropna()
        if len(non_null) == 0 or non_null.nunique() <= 1:
            constant_cols.append(col)

    if constant_cols:
        df = df.drop(columns=constant_cols)
        logger.info("Removed constant columns: %s", constant_cols)

    return df


# ---------------------------------------------------------------------------
# Duplicate removal
# ---------------------------------------------------------------------------

def remove_duplicates(
    df: pd.DataFrame, subset: Optional[List[str]] = None, keep: str = "first"
) -> pd.DataFrame:
    """Remove duplicate rows.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    subset : list of str, optional
        Columns to consider for duplication. If None, all columns
        are used.
    keep : str
        Which duplicates to keep: ``'first'``, ``'last'``, or
        ``False`` (drop all duplicates).
    """
    df = df.copy()
    before = len(df)
    df = df.drop_duplicates(subset=subset, keep=keep)
    after = len(df)
    removed = before - after
    if removed:
        logger.info("Removed %d duplicate rows", removed)
    return df


# ---------------------------------------------------------------------------
# Missing value handling
# ---------------------------------------------------------------------------

def handle_missing_values(
    df: pd.DataFrame,
    strategy: str = "drop",
    columns: Optional[List[str]] = None,
    fill_value: Any = None,
    type_aware: bool = True,
    inferred_types: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Handle missing values in a DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    strategy : str
        ``'drop'``, ``'mean'``, ``'median'``, ``'mode'``, or
        ``'constant'``.
    columns : list of str, optional
        Columns to apply the strategy to. If None, all columns.
    fill_value : any, optional
        Value to use when strategy is ``'constant'``.
    type_aware : bool
        If True, automatically choose an appropriate strategy per
        column type when strategy is not explicitly column-specific.
    inferred_types : dict, optional
        Output of infer_column_types(). Used for type-aware defaults.

    Returns
    -------
    pd.DataFrame
        DataFrame with missing values handled.
    """
    df = df.copy()
    if columns is None:
        columns = df.columns.tolist()

    if inferred_types is None:
        inferred_types = infer_column_types(df)

    for col in columns:
        if col not in df.columns:
            continue

        null_count = df[col].isna().sum()
        if null_count == 0:
            continue

        col_type = inferred_types.get(col, "text")

        if strategy == "drop":
            df = df.dropna(subset=[col])
        elif strategy == "constant":
            df[col] = df[col].fillna(fill_value)
        elif strategy == "mean" and col_type == "numeric":
            df[col] = df[col].fillna(df[col].mean())
        elif strategy == "median" and col_type == "numeric":
            df[col] = df[col].fillna(df[col].median())
        elif strategy == "mode":
            mode_val = df[col].mode()
            if not mode_val.empty:
                df[col] = df[col].fillna(mode_val.iloc[0])
        elif type_aware:
            # Auto-select strategy based on column type
            if col_type == "numeric":
                df[col] = df[col].fillna(df[col].median())
            elif col_type in ("categorical", "text", "datetime"):
                mode_val = df[col].mode()
                if not mode_val.empty:
                    df[col] = df[col].fillna(mode_val.iloc[0])
            else:
                df[col] = df[col].fillna(fill_value)
        else:
            df[col] = df[col].fillna(fill_value)

    return df


# ---------------------------------------------------------------------------
# Data type fixing
# ---------------------------------------------------------------------------

def fix_data_types(
    df: pd.DataFrame,
    type_map: Optional[Dict[str, str]] = None,
    inferred_types: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Cast columns to specified or inferred types.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    type_map : dict, optional
        Explicit mapping of column name -> target type
        (``'int'``, ``'float'``, ``'str'``, ``'datetime'``,
        ``'category'``, ``'bool'``).
    inferred_types : dict, optional
        Output of infer_column_types(). Used when type_map is not
        provided for a column.
    """
    df = df.copy()
    if inferred_types is None:
        inferred_types = infer_column_types(df)

    for col in df.columns:
        target_type = None
        if type_map and col in type_map:
            target_type = type_map[col]
        else:
            inferred = inferred_types.get(col, "text")
            if inferred == "numeric":
                target_type = "float"
            elif inferred == "datetime":
                target_type = "datetime"
            elif inferred == "categorical":
                target_type = "category"
            else:
                target_type = "str"

        try:
            if target_type == "int":
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            elif target_type == "float":
                df[col] = pd.to_numeric(df[col], errors="coerce")
            elif target_type == "str":
                df[col] = df[col].astype(str)
            elif target_type == "datetime":
                df[col] = pd.to_datetime(df[col], errors="coerce")
            elif target_type == "category":
                df[col] = df[col].astype("category")
            elif target_type == "bool":
                df[col] = df[col].map(
                    {"true": True, "false": False, "1": True, "0": False,
                     True: True, False: False, 1: True, 0: False}
                )
        except (ValueError, TypeError) as exc:
            logger.warning("Could not convert column %s to %s: %s", col, target_type, exc)

    return df


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def parse_dates(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
    formats: Optional[List[str]] = None,
    inferred_types: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Convert string columns to datetime.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    columns : list of str, optional
        Specific columns to parse. If None, all columns inferred as
        ``datetime`` are parsed.
    formats : list of str, optional
        Date format strings to try (passed to pandas to_datetime).
    inferred_types : dict, optional
        Output of infer_column_types().
    """
    df = df.copy()
    if inferred_types is None:
        inferred_types = infer_column_types(df)

    if columns is None:
        columns = [c for c, t in inferred_types.items() if t == "datetime"]

    for col in columns:
        if col not in df.columns:
            continue
        try:
            df[col] = pd.to_datetime(df[col], format=formats[0] if formats else None, errors="coerce")
        except (ValueError, TypeError) as exc:
            logger.warning("Could not parse dates in column %s: %s", col, exc)

    return df


# ---------------------------------------------------------------------------
# Outlier handling
# ---------------------------------------------------------------------------

def handle_outliers(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
    method: str = "iqr",
    action: str = "cap",
    z_threshold: float = 3.0,
    inferred_types: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Detect and handle outliers in numeric columns.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    columns : list of str, optional
        Numeric columns to check. If None, all numeric-inferred
        columns are checked.
    method : str
        ``'iqr'`` (interquartile range) or ``'zscore'``.
    action : str
        ``'cap'`` (clip to bounds), ``'remove'`` (drop rows), or
        ``'flag'`` (add boolean outlier column).
    z_threshold : float
        Z-score threshold when method is ``'zscore'``.
    inferred_types : dict, optional
        Output of infer_column_types().
    """
    df = df.copy()
    if inferred_types is None:
        inferred_types = infer_column_types(df)

    if columns is None:
        columns = [c for c, t in inferred_types.items() if t == "numeric"]

    outlier_flags = pd.Series(False, index=df.index)

    for col in columns:
        if col not in df.columns:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue

        series = df[col].dropna()
        if len(series) == 0:
            continue

        if method == "iqr":
            q1 = series.quantile(0.25)
            q3 = series.quantile(0.75)
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
        elif method == "zscore":
            mean = series.mean()
            std = series.std()
            if std == 0 or np.isnan(std):
                continue
            lower = mean - z_threshold * std
            upper = mean + z_threshold * std
        else:
            logger.warning("Unknown outlier method %s, skipping column %s", method, col)
            continue

        is_outlier = (df[col] < lower) | (df[col] > upper)
        outlier_flags = outlier_flags | is_outlier

        if action == "cap":
            df[col] = df[col].clip(lower=lower, upper=upper)
        elif action == "flag":
            df[f"{col}_outlier"] = is_outlier.astype(int)
        # 'remove' is handled after the loop

    if action == "remove":
        df = df.loc[~outlier_flags].copy()
        logger.info("Removed %d outlier rows", int(outlier_flags.sum()))

    return df


# ---------------------------------------------------------------------------
# Categorical encoding
# ---------------------------------------------------------------------------

def encode_categorical(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
    method: str = "label",
    inferred_types: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Encode categorical columns.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    columns : list of str, optional
        Categorical columns to encode. If None, all
        categorical-inferred columns are encoded.
    method : str
        ``'label'`` (integer codes), ``'onehot'`` (new binary columns),
        or ``'ordinal'`` (alphabetical integer codes).
    inferred_types : dict, optional
        Output of infer_column_types().
    """
    df = df.copy()
    if inferred_types is None:
        inferred_types = infer_column_types(df)

    if columns is None:
        columns = [c for c, t in inferred_types.items() if t == "categorical"]

    for col in columns:
        if col not in df.columns:
            continue

        if method == "label":
            uniques = df[col].dropna().unique()
            mapping = {v: i for i, v in enumerate(uniques)}
            df[col] = df[col].map(mapping)
        elif method == "ordinal":
            cats = sorted(df[col].dropna().unique())
            mapping = {cat: i for i, cat in enumerate(cats)}
            df[col] = df[col].map(mapping)
        elif method == "onehot":
            dummies = pd.get_dummies(df[col], prefix=col, dummy_na=True)
            df = df.drop(columns=[col])
            df = pd.concat([df, dummies], axis=1)
        else:
            logger.warning("Unknown encoding method %s, skipping column %s", method, col)

    return df


# ---------------------------------------------------------------------------
# Numeric normalization
# ---------------------------------------------------------------------------

def normalize_numeric(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
    method: str = "zscore",
    inferred_types: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Normalize/scale numeric columns.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    columns : list of str, optional
        Numeric columns to normalize. If None, all numeric-inferred
        columns are normalized.
    method : str
        ``'zscore'``, ``'minmax'``, or ``'robust'``.
    inferred_types : dict, optional
        Output of infer_column_types().
    """
    df = df.copy()
    if inferred_types is None:
        inferred_types = infer_column_types(df)

    if columns is None:
        columns = [c for c, t in inferred_types.items() if t == "numeric"]

    for col in columns:
        if col not in df.columns:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue

        series = df[col].dropna()
        if len(series) == 0:
            continue

        if method == "zscore":
            mean = series.mean()
            std = series.std()
            if std == 0 or np.isnan(std):
                df[col] = 0.0
            else:
                df[col] = (df[col] - mean) / std
        elif method == "minmax":
            min_val = series.min()
            max_val = series.max()
            if max_val == min_val:
                df[col] = 0.0
            else:
                df[col] = (df[col] - min_val) / (max_val - min_val)
        elif method == "robust":
            median = series.median()
            q1 = series.quantile(0.25)
            q3 = series.quantile(0.75)
            iqr = q3 - q1
            if iqr == 0 or np.isnan(iqr):
                df[col] = 0.0
            else:
                df[col] = (df[col] - median) / iqr
        else:
            logger.warning("Unknown normalization method %s, skipping column %s", method, col)

    return df
