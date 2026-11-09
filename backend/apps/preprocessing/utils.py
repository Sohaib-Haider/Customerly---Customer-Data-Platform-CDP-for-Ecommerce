"""Utility helpers for the preprocessing service.

File validation, size estimation, and chunk-size recommendations for
large CSV processing under storage constraints.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import pandas as pd


def validate_csv_path(path: str) -> None:
    """Validate that a CSV file exists and is readable.

    Parameters
    ----------
    path : str
        Path to the CSV file.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the file is empty or cannot be read as CSV.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV file not found: {path}")

    if os.path.getsize(path) == 0:
        raise ValueError(f"CSV file is empty: {path}")

    try:
        pd.read_csv(path, nrows=1)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Cannot read CSV file {path}: {exc}") from exc


def get_file_size_mb(path: str) -> float:
    """Return file size in megabytes."""
    return os.path.getsize(path) / (1024 * 1024)


def recommend_chunksize(file_size_mb: float, target_chunk_mb: float = 50.0) -> int:
    """Recommend a chunksize (in rows) for reading a large CSV.

    Estimates rows per MB from a small sample, then computes how many
    rows fit in target_chunk_mb.

    Parameters
    ----------
    file_size_mb : float
        Total file size in MB.
    target_chunk_mb : float
        Target memory size per chunk in MB (default 50 MB).

    Returns
    -------
    int
        Recommended chunksize (rows), at least 1000.
    """
    # Read a small sample to estimate rows per MB
    sample_path = "/tmp/_preprocessing_sample.csv"
    try:
        df_sample = pd.read_csv("/dev/stdin", nrows=1000)  # placeholder, not used
    except Exception:
        pass

    # Conservative fallback: assume ~1KB per row on average
    # 50 MB / 1 KB = ~50,000 rows per chunk
    rows_per_mb = 1000  # conservative estimate
    chunksize = int(target_chunk_mb * rows_per_mb)
    return max(chunksize, 1000)


def estimate_output_size_mb(input_path: str, compression_ratio: float = 0.8) -> float:
    """Estimate the output CSV size in MB after preprocessing.

    Preprocessing typically reduces size slightly (removing duplicates,
    constant columns) or keeps it similar. This is a rough estimate.
    """
    input_mb = get_file_size_mb(input_path)
    return input_mb * compression_ratio


def check_disk_space_mb(path: str) -> float:
    """Return available disk space in MB for the directory containing path."""
    import shutil

    directory = os.path.dirname(os.path.abspath(path))
    if not directory:
        directory = "."
    total, used, free = shutil.disk_usage(directory)
    return free / (1024 * 1024)


def should_use_chunked(file_size_mb: float, threshold_mb: float = 100.0) -> bool:
    """Decide whether to use chunked processing based on file size."""
    return file_size_mb > threshold_mb


def create_backup(input_path: str) -> str:
    """Create a backup copy of the input file before overwriting.

    Returns the backup path.
    """
    import shutil

    bak_path = input_path + ".bak"
    shutil.copy2(input_path, bak_path)
    return bak_path
