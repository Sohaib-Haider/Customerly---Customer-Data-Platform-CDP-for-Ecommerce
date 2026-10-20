"""Shared id-namespace and calendar-offset helpers for the offline transform stage.

This module is the single source of truth for the two things every transform
script must agree on:

1.  Id namespacing — every source id is prefixed with the dataset's namespace so
    ids from two different Kaggle datasets can never collide inside the same
    canonical store (schema-blueprint.md principle 4, traceability back to source).
2.  Date strategy — each dataset declares its own. Day-number sources align onto
    their own documented anchor; real-date sources are shifted by a single auditable
    window offset. See DATASET_ANCHORS below — deliberately not a global constant.

Nothing else lives here. No column mappings, no taxonomy, no feature logic —
those belong to the individual transform scripts.

Reference: docs/notes/schema-notes/schema-blueprint.md (principles 3 and 4),
docs/notes/customer segmentation-notes/segments-schema-mapping.md (per-segment
anchor_date + (day_no - 1) offset), docs/submission/2.7-constraints.md (per-record
offsets must be documented, not inferred).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone

# Fixed namespace for every UUID this stage emits. Derived from a constant
# string so re-running a transform produces byte-identical UUIDs, which makes
# reloading the output idempotent instead of duplicating rows.
UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://customerly.local/data_prep/transform"
)

# Per-dataset id prefix. "dnb" = Dunnhumby: The Complete Journey.
# Add one namespace constant per source dataset as other members land theirs.
SOURCE_NAMESPACES = {"dunnhumby": "dnb"}

# ---------------------------------------------------------------------------
# Date strategy — PER DATASET, never global.
# ---------------------------------------------------------------------------
# A source with native day numbers (Dunnhumby's DAY 1-711) has no real dates, so
# it *invents* them from an anchor. A source with real timestamps (Online Retail
# II's InvoiceDate) must NOT be shifted by an anchor — doing so destroys the
# only real timestamps it has. So the anchor is declared per dataset and passed
# explicitly: a dataset with real dates has no entry here and is told to use
# shift_date()/window_offset() instead.
#
# Rationale: while this transform only serves Dunnhumby, the anchor used to be a
# single module-level ANCHOR_DATE constant. A later member reading common.py would
# import it "to put dates on the shared timeline" and silently move real timestamps
# by ~1460 days (2010 -> 2016), or off the calendar entirely. Removing the global
# makes that impossible to write by accident.
#
# Dunnhumby anchor: segments-schema-mapping.md Segment 6 documents
#   order_date = anchor_date + (day_no - 1)   (anchor configurable, default 2016-01-01)
DATASET_ANCHORS = {
    "dunnhumby": date(2016, 1, 1),
    # "online_retail_ii": <not applicable — real InvoiceDate, use window_offset>
}


def anchor_for(dataset: str) -> date:
    """Return the declared anchor for a day-number source.

    Raises rather than defaulting: an unregistered dataset has no documented
    anchor, and guessing one would invent dates for data that already has them.
    """
    try:
        return DATASET_ANCHORS[dataset]
    except KeyError:
        raise KeyError(
            f"No anchor declared for dataset {dataset!r}. If it has real dates, "
            f"shift with window_offset() instead — do NOT route them through "
            f"align_day(). Add an entry to DATASET_ANCHORS only for a source with "
            f"native day numbers."
        ) from None


def source_id(raw: object, dataset: str) -> str:
    """Normalise a raw source id and prefix it with the dataset namespace.

    Handles the two things raw Kaggle CSVs actually do to identifiers: they arrive
    as floats from pandas (1234.0) and as strings with stray whitespace.
    Returns an empty string for unusable values so callers can drop the row.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""
    # pandas upcasts integer-looking id columns to float when any value is null
    if text.endswith(".0"):
        stem = text[:-2]
        if stem.lstrip("-").isdigit():
            text = stem
    ns = SOURCE_NAMESPACES.get(dataset)
    return f"{ns}:{text}" if ns else text


def uid(table: str, key: str) -> str:
    """Deterministic primary/foreign key for one canonical row.

    Derived from (table, source key) only — deliberately NOT from store_id — so
    the output keeps loading correctly even if the demo tenant's UUID is
    regenerated at load time. Postgres UUID columns accept this string form.
    """
    return str(uuid.uuid5(UUID_NAMESPACE, f"{table}|{key}"))


def align_day(day_no: object, anchor: date) -> date | None:
    """Map a source-native day number onto that dataset's store timeline.

    day_no itself is preserved verbatim in orders.day_no because interval maths
    (replenishment gaps) must stay on the native calendar; only the human-readable
    order_date is offset. Returns None when the day number is missing/unusable,
    leaving order_date NULL as final-schema.md requires for undetermined sources.

    `anchor` is required and comes from anchor_for(dataset) — a day-number source
    must not borrow another dataset's anchor.
    """
    value = to_int(day_no)
    if value is None:
        return None
    return anchor + timedelta(days=value - 1)


def window_offset(real_dates: Iterable[date], window_start: date) -> int:
    """Days to add to a source's real dates so its earliest date hits window_start.

    For datasets that ship real timestamps (e.g. Online Retail II's InvoiceDate),
    per data-transformation-plan.md "InvoiceDate -> shift to store window". The
    offset is computed once from the source's own minimum and applied uniformly —
    never inferred per row, which is what makes it auditable
    (2.7-constraints.md: "apply documented, auditable conversion factors and date
    offsets rather than inferring them per record").
    """
    return (window_start - min(real_dates)).days


def shift_date(value: date | datetime, offset_days: int) -> date:
    """Apply a window offset to a single real date."""
    if isinstance(value, datetime):
        value = value.date()
    return value + timedelta(days=offset_days)


def combine_timestamp(day: date | None, trans_time: object) -> datetime | None:
    """Combine an aligned date with a source time-of-day into one UTC timestamp.

    Dunnhumby ships a separate TRANS_TIME column (zero-padded HHMM, e.g. 1631 ==
    16:31), so order_timestamp is genuinely present for this source rather than
    synthesised — see the "Order Peak Hour" card and final-schema.md Open Decision #5.
    """
    tod = parse_trans_time(trans_time)
    if day is None or tod is None:
        return None
    return datetime.combine(day, tod, tzinfo=timezone.utc)


def parse_trans_time(raw: object) -> time | None:
    """Parse TRANS_TIME into a time of day.

    The real dataset ships it as a *4-digit HHMM* integer (1631 == 16:31), which
    the source docs describe only as "time of day". Tolerate the other shapes too:
    HMMSS (5 digits) and HHMMSS (6 digits), which is what an ingestion rewrite or a
    different source system would produce. A 4-digit value is HHMM, never HHMMSS —
    treating it as the latter silently shifts every order's timestamp by hours.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    text = text.replace(":", "").replace(".", "")
    if not text.isdigit():
        return None
    # width decides the layout. 3 digits means the zero pad was lost upstream
    # ("0955" read as an int becomes 955) -- HMM parses to the same wall clock
    # as the HHMM it was born from, so accepting it recovers the value instead
    # of dropping the order's timestamp entirely.
    if len(text) == 3:
        hour, minute, second = int(text[0:1]), int(text[1:3]), 0
    elif len(text) == 4:
        hour, minute, second = int(text[0:2]), int(text[2:4]), 0
    elif len(text) == 5:
        hour, minute, second = int(text[0:1]), int(text[1:3]), int(text[3:5])
    elif len(text) == 6:
        hour, minute, second = int(text[0:2]), int(text[2:4]), int(text[4:6])
    else:
        return None
    if hour > 23 or minute > 59 or second > 59:
        return None
    return time(hour, minute, second)


def to_int(raw: object) -> int | None:
    """Lenient int coercion for CSV columns pandas may have upcast to float."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int,)):
        return int(raw)
    if isinstance(raw, float):
        if raw != raw:  # NaN
            return None
        return int(raw)
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def to_float(raw: object) -> float | None:
    """Lenient float coercion; returns None for blanks and non-numeric text so
    bad cells become NULL instead of poisoning an entire money column."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return None if value != value else value  # NaN -> None
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    text = text.replace(",", "").replace("$", "").replace("£", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return None if value != value else value


def to_bool(raw: object, default: bool | None = None) -> bool | None:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return default


def empty_string(raw: object) -> bool:
    """True when a raw cell carries no usable value (used for key validation)."""
    return source_id(raw, "") == ""
