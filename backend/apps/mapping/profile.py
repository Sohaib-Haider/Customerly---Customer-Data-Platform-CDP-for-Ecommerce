"""Local-only CSV profiling: safe metadata, never raw values.

Fixes the plan's header-only contradiction: the mapper DOES profile data, but
entirely on this machine. What leaves this module for the Gemini prompt is
per-column *metadata* — inferred dtype, null share, distinct count, value
-shape tags, a likely-PII flag — never cell values, never customer records.
Raw values never leave this module at all.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PROFILE_SAMPLE_ROWS, ROW_SCAN_CAP

# Value-shape tags derived locally by regex; only the TAGS are shared.
_RE_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_INT = re.compile(r"^-?\d+$")
_RE_DECIMAL = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?\d+\.\d+$")
_RE_CURRENCY = re.compile(r"^[$£€]\s?-?\d|^-?\d+(\.\d+)?\s?[£€]$")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RE_PHONE = re.compile(r"^\+?[\d\s().-]{7,15}$")
_RE_URL = re.compile(r"^https?://", re.I)
_RE_BOOL = re.compile(r"^(true|false)$", re.I)
_RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Column names that mark PII even when value-shape detection misses.
_PII_NAME_HINTS = frozenset(
    normalize_hint
    for normalize_hint in (
        "email", "mail", "phone", "mobile", "address", "firstname", "lastname",
        "fullname", "name", "dob", "birth", "ssn", "nationalid",
    )
)

_DTYPE_PRIORITY = ("datetime", "date", "bool", "decimal", "integer", "string")


@dataclass
class ColumnProfile:
    name: str
    dtype: str = "string"  # datetime | date | bool | integer | decimal | string
    null_count: int = 0
    distinct_count: int = 0
    formats: list[str] = field(default_factory=list)
    likely_pii: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "null_count": self.null_count,
            "distinct_count": self.distinct_count,
            "formats": list(self.formats),
            "likely_pii": self.likely_pii,
        }


@dataclass
class CSVProfile:
    file_name: str
    columns: list[str]
    row_count: int | None
    row_count_capped: bool
    sampled_rows: int
    column_profiles: dict[str, ColumnProfile]
    duplicate_columns: list[str] = field(default_factory=list)

    def to_prompt_metadata(self) -> list[dict[str, Any]]:
        """What the LLM is allowed to see: names + metadata, no values."""
        return [
            {
                "name": p.name,
                "inferred_dtype": p.dtype,
                "null_share": round(
                    p.null_count / self.sampled_rows, 3
                ) if self.sampled_rows else 0.0,
                "approx_distinct_values": p.distinct_count,
                "value_shapes": p.formats,
                "likely_pii": p.likely_pii,
            }
            for p in self.column_profiles.values()
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "row_count": self.row_count,
            "row_count_capped": self.row_count_capped,
            "sampled_rows": self.sampled_rows,
            "duplicate_columns": self.duplicate_columns,
            "columns": [p.to_dict() for p in self.column_profiles.values()],
        }


def _classify_value(value: str) -> tuple[str, list[str]]:
    """Return (dtype vote, shape tags) for one cell. Pure local function."""
    v = value.strip()
    tags: list[str] = []
    dtype = "string"
    if _RE_BOOL.match(v):
        dtype, tags = "bool", ["true_false"]
    elif _RE_ISO_DATETIME.match(v):
        dtype, tags = "datetime", ["iso_datetime"]
    elif _RE_DATE.match(v):
        dtype, tags = "date", ["date_yyyy_mm_dd"]
    elif _RE_INT.match(v):
        dtype, tags = "integer", ["integer"]
    elif _RE_DECIMAL.match(v):
        dtype, tags = "decimal", ["decimal"]
    if _RE_CURRENCY.match(v):
        tags.append("currency_symbol")
    if _RE_EMAIL.match(v):
        tags.append("email_like")
    # Phone-like only when separators/+ prefix make it phone-ish: bare digit
    # runs stay plain integers so id columns are not mistaken for PII.
    if not tags and _RE_PHONE.match(v) and not v.isdigit():
        tags.append("phone_like")
    if _RE_URL.match(v):
        tags.append("url_like")
    if _RE_UUID.match(v):
        tags.append("uuid")
    return dtype, tags


def profile_csv(path: str | Path, sample_rows: int = PROFILE_SAMPLE_ROWS) -> CSVProfile:
    """Profile a CSV header + a bounded sample of its rows.

    Counts every row up to ROW_SCAN_CAP (row counts feed ingestion_uploads
    row_count later), but value profiling uses at most ``sample_rows`` rows.
    """
    path = Path(path)
    duplicate_columns: list[str] = []
    column_profiles: dict[str, ColumnProfile] = {}
    dtype_votes: dict[str, Counter] = {}
    tag_votes: dict[str, Counter] = {}
    sampled = 0
    total_rows = 0
    capped = False

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"{path} is empty — no header row") from None
        header = [h.strip() for h in header]
        seen: set[str] = set()
        for name in header:
            key = name or f"_unnamed_{len(seen)}"
            if key in seen:
                duplicate_columns.append(key)
            seen.add(key)
            column_profiles.setdefault(key, ColumnProfile(name=key))
            dtype_votes.setdefault(key, Counter())
            tag_votes.setdefault(key, Counter())

        for row in reader:
            total_rows += 1
            if total_rows > ROW_SCAN_CAP:
                capped = True
                break
            if sampled < sample_rows:
                sampled += 1
                for index, name in enumerate(header):
                    key = name or f"_unnamed_{index}"
                    if index >= len(row):
                        continue
                    value = row[index]
                    profile = column_profiles.get(key)
                    if profile is None:
                        continue
                    if value is None or not value.strip():
                        profile.null_count += 1
                        continue
                    dtype, tags = _classify_value(value)
                    dtype_votes[key][dtype] += 1
                    for tag in tags:
                        tag_votes[key][tag] += 1

    # finalize: distinct counts from a second bounded pass per column
    distinct = _distinct_counts(path, header, sample_rows)

    for key, profile in column_profiles.items():
        votes = dtype_votes.get(key, Counter())
        if votes:
            profile.dtype = _winner_dtype(votes)
        formats = [tag for tag, count in tag_votes.get(key, Counter()).items() if count > 0]
        profile.formats = sorted(formats)
        profile.distinct_count = distinct.get(key, 0)
        name_hint = re.sub(r"[^a-z0-9]", "", key.lower())
        profile.likely_pii = (
            "email_like" in profile.formats
            or "phone_like" in profile.formats
            or any(hint in name_hint for hint in _PII_NAME_HINTS)
        )

    row_count = None if capped else total_rows
    return CSVProfile(
        file_name=path.name,
        columns=header,
        row_count=row_count,
        row_count_capped=capped,
        sampled_rows=sampled,
        column_profiles=column_profiles,
        duplicate_columns=duplicate_columns,
    )


def _distinct_counts(path: Path, header: list[str], sample_rows: int) -> dict[str, int]:
    """Exact distinct counts over the bounded sample (local only)."""
    distinct: dict[str, set] = {name or f"_unnamed_{i}": set() for i, name in enumerate(header)}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        sampled = 0
        for row in reader:
            if sampled >= sample_rows:
                break
            sampled += 1
            for index, name in enumerate(header):
                if index >= len(row):
                    continue
                key = name or f"_unnamed_{index}"
                buckets = distinct.get(key)
                if buckets is None:
                    continue
                value = row[index].strip() if isinstance(row[index], str) else row[index]
                if value:
                    buckets.add(value)
    return {key: len(values) for key, values in distinct.items()}


def _winner_dtype(votes: Counter) -> str:
    """Pick the most-voted dtype, biased towards richer temporal types."""
    best = votes.most_common(1)[0][0]
    # A single datetime/date value beats thousands of strings? No — majority
    # wins first; only ties are broken by type richness.
    top = [name for name, count in votes.items() if count == votes[best]]
    for candidate in _DTYPE_PRIORITY:
        if candidate in top:
            return candidate
    return best
