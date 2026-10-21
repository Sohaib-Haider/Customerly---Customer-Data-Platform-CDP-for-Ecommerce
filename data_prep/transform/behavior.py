"""eCommerce behavior data from multi category store -> canonical schema.

Member B, part 1 of 2. This dataset feeds Predicted Purchase Intent (Segment 1)
and Cart Abandoners (Segment 9); its only canonical target table is `events`
(segments-schema-mapping.md Segment 1/9, all 9 attributes).

The raw files (2019-Oct.csv.gz / 2019-Nov.csv.gz) are ~115 million rows, so this
transform STREAMS: it reads the gz in chunks, appends mapped events to a CSV as it
goes, and only keeps the small dimension sets (customers, products) in memory.
Nothing is aggregated and no ML feature is computed.

The chunk mapper is deliberately vectorised (pandas string ops, factorize-based id
lookup, a pandas Timedelta shift) rather than per-row Python: a per-row `.map()`
version processed ~26k rows/sec and could not finish the dataset before the
environment restarted, while the vectorised path is an order of magnitude faster.

Column mappings are taken from:
  docs/notes/customer segmentation-notes/segments-schema-mapping.md  (Segments 1/9)
  docs/notes/customer segmentation-notes/segments/Predicted Purchase Intent.md
  docs/notes/customer segmentation-notes/segments/Cart Abandoners.md
  docs/notes/schema-notes/final-schema.md                            (target shape)

Output: `events` plus the `customers` and `products` dimension rows the events
table's NOT NULL / foreign keys require (see BEHAVIOR_AND_RETAIL.md §3 for why
the brief's "maps to the events table" necessarily emits those two as well).

NOT a source for this work: docs/decisions/data-transformation-plan.md
(excluded by its own header).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from common import (
    SHARED_WINDOW_START,
    SOURCE_NAMESPACES,
    source_id,  # kept for single-value callers; the bulk path uses _namespace
    to_float,
    uid,
)

DATASET = "behavior"
PREFIX = SOURCE_NAMESPACES[DATASET] + ":"

# The two monthly files named in the segment documentation. Both .csv and .csv.gz
# are accepted; the gz names are what REES46 actually serves.
RAW_FILES = ["2019-Oct.csv", "2019-Nov.csv"]
RAW_FILES_GZ = ["2019-Oct.csv.gz", "2019-Nov.csv.gz"]

# ---------------------------------------------------------------------------
# Canonical output column order (verbatim from backend/schema.sql)
# ---------------------------------------------------------------------------
COLUMNS = {
    "stores": ["id", "name", "plan", "created_at", "updated_at"],
    "customers": [
        "id", "store_id", "source_customer_id",
        "first_purchase_date", "tenure", "warehouse_to_home",
        "number_of_devices_registered", "satisfaction_score", "complain",
        "days_since_last_order", "cashback_amount",
        "preferred_order_category", "email", "phone_number",
        "churn", "created_at", "updated_at",
    ],
    "products": [
        "id", "store_id", "source_product_id", "name", "brand", "category",
        "department", "commodity_desc", "unit_price", "created_at", "updated_at",
    ],
    "events": [
        "id", "store_id", "customer_id", "session_id", "event_type",
        "event_timestamp", "product_id", "category_id", "category_code",
        "brand", "price",
    ],
}

EVENT_COLUMNS = COLUMNS["events"]
EVENT_REQUIRED = ["customer_id", "session_id", "event_type", "event_timestamp"]


@dataclass
class TransformResult:
    """Emitted frames plus everything the transform refused to do."""

    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    dropped_by_source: dict[str, int] = field(default_factory=dict)
    raw_counts: dict[str, int] = field(default_factory=dict)
    events_written: int = 0
    events_violations: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, count: int, source: str = "events") -> None:
        if count:
            self.dropped[reason] = self.dropped.get(reason, 0) + count
            self.dropped_by_source[source] = self.dropped_by_source.get(source, 0) + count

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _parse_times(series: pd.Series) -> pd.Series:
    """Parse the source's `event_time` (e.g. '2019-10-01 00:00:00 UTC').

    The trailing ' UTC' is stripped because the column is read as text; the
    value is then localised to UTC so the canonical TIMESTAMPTZ is unambiguous.
    Unparseable cells become NaT and the row is dropped (event_timestamp NOT NULL).
    """
    text = series.astype(str).str.strip().str.replace(r"\s+UTC$", "", regex=True)
    return pd.to_datetime(text, utc=True, errors="coerce")


def _namespace(series: pd.Series) -> pd.Series:
    """Vectorised equivalent of common.source_id(raw, DATASET) for a column.

    Same rules as source_id — blank/NaN -> "", a pandas float id "1234.0" ->
    "1234", then the dataset prefix — expressed with pandas string ops so a whole
    chunk is handled in C instead of a Python lambda per row.
    """
    s = series.fillna("").astype(str).str.strip()
    s = s.str.replace(r"^(\d+)\.0$", r"\1", regex=True)
    return (PREFIX + s).where(s != "", "")


def _uid_series(keys: pd.Series, table: str) -> pd.Series:
    """Deterministic UUIDs for a column of namespaced source ids.

    factorize() collapses the column to its distinct values, so uid() (a SHA-1
    uuid5) runs once per distinct key rather than once per row.
    """
    codes, uniques = pd.factorize(keys, use_na_sentinel=False)
    uniq_ids = np.array([uid(table, u) for u in uniques], dtype=object)
    return pd.Series(uniq_ids[codes], index=keys.index, dtype=object)


def _text_or_none(value):
    """Scalar text normalisation: NaN/blank -> None, otherwise stripped text."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def resolve_raw_files(raw_dir) -> list[Path]:
    """Return the two monthly source paths, accepting .csv or .csv.gz."""
    raw_dir = Path(raw_dir)
    for candidate in (RAW_FILES, RAW_FILES_GZ):
        paths = [raw_dir / name for name in candidate]
        if all(p.exists() for p in paths):
            return paths
    found = [p for name in RAW_FILES + RAW_FILES_GZ if (p := raw_dir / name).exists()]
    if not found:
        raise FileNotFoundError(
            f"No behaviour source files in {raw_dir}. Expected "
            f"{' and '.join(RAW_FILES)} (or the .gz names). Download from "
            "https://data.rees46.com/datasets/marketplace/ (2019-Oct.csv.gz, "
            "2019-Nov.csv.gz) or Kaggle "
            "mkechinov/ecommerce-behavior-data-from-multi-category-store."
        )
    return found


def compute_offset(raw_paths: list[Path], chunk_size: int) -> int:
    """Days to add so this dataset's earliest event lands on SHARED_WINDOW_START.

    One streaming pass over `event_time` only. The offset is computed once from
    the dataset's own minimum and applied uniformly to every row — never inferred
    per record (2.7-constraints.md).
    """
    min_ts = None
    for path in raw_paths:
        for chunk in pd.read_csv(
            path, usecols=["event_time"], dtype=str, chunksize=chunk_size,
            low_memory=False,
        ):
            parsed = _parse_times(chunk["event_time"]).min()
            if pd.notna(parsed) and (min_ts is None or parsed < min_ts):
                min_ts = parsed
    if min_ts is None or pd.isna(min_ts):
        raise ValueError("no parseable event_time found in the behaviour sources")
    return (SHARED_WINDOW_START - min_ts.date()).days


# ---------------------------------------------------------------------------
# chunk mapper
# ---------------------------------------------------------------------------
def _map_chunk(
    chunk: pd.DataFrame,
    file_tag: str,
    base_index: int,
    store_id: str,
    offset: int,
    acc: dict,
    result: TransformResult,
) -> pd.DataFrame:
    """Map one chunk of raw events into canonical `events` rows + audit.

    Returns a DataFrame in EVENT_COLUMNS order (store_id included). `acc` holds
    the running customer set and product catalog; `result` records every drop.
    """
    n = len(chunk)
    result.raw_counts[file_tag] = result.raw_counts.get(file_tag, 0) + n

    user = _namespace(chunk["user_id"])
    prod = _namespace(chunk["product_id"])
    sess = _namespace(chunk["user_session"])
    # dtype=str leaves missing cells as NaN; fillna("") keeps them blank rather
    # than turning them into the literal text "nan" before the empty check.
    etype = chunk["event_type"].fillna("").astype(str).str.strip()
    ts = _parse_times(chunk["event_time"])
    price = pd.to_numeric(chunk["price"], errors="coerce")
    cat_id = chunk.get("category_id")
    cat_code = chunk.get("category_code")
    brand = chunk.get("brand")

    # ---- drops: only rows that could not be loaded or would break a NOT NULL
    result.drop("events with empty user_id", int((user == "").sum()), source=file_tag)
    result.drop("events with empty user_session", int((sess == "").sum()), source=file_tag)
    result.drop("events with empty event_type", int((etype == "").sum()), source=file_tag)
    result.drop("events with unparseable event_time", int(ts.isna().sum()), source=file_tag)
    keep = (user != "") & (sess != "") & (etype != "") & ts.notna()

    # ---- dimension accumulators, on the rows that survive -----------------
    ku = user[keep]
    acc["customers"].update(ku.unique())
    c_series = cat_code[keep] if cat_code is not None else None
    b_series = brand[keep] if brand is not None else None
    sub = pd.DataFrame({"p": prod[keep], "c": c_series, "b": b_series})
    sub = sub[sub["p"] != ""]
    if len(sub):
        sub = sub.drop_duplicates(subset=["p"])
        for p, c, b in sub.itertuples(index=False):
            c = _text_or_none(c)
            b = _text_or_none(b)
            entry = acc["products"].get(p)
            if entry is None:
                acc["products"][p] = {
                    "category_code": c,
                    "brand": b,
                    "name": c if c else p,
                }
            else:
                if entry["category_code"] is None and c:
                    entry["category_code"] = c
                if entry["brand"] is None and b:
                    entry["brand"] = b
                if entry["name"] == p and c:
                    entry["name"] = c

    if not keep.all():
        user, prod, sess, etype, ts, price = (
            user[keep], prod[keep], sess[keep], etype[keep], ts[keep], price[keep]
        )
        if cat_id is not None:
            cat_id = cat_id[keep]
        if cat_code is not None:
            cat_code = cat_code[keep]
        if brand is not None:
            brand = brand[keep]

    # ---- deterministic ids -------------------------------------------------
    out = pd.DataFrame(index=chunk.index[keep])
    idx = np.arange(base_index, base_index + len(out)).astype(str)
    out["id"] = [uid("events", k) for k in np.char.add(f"{file_tag}|", idx)]
    out["store_id"] = store_id
    out["customer_id"] = _uid_series(user, "customers")
    out["session_id"] = sess
    out["event_type"] = etype
    out["event_timestamp"] = ts + pd.Timedelta(days=offset)
    out["product_id"] = _uid_series(prod, "products").where(prod != "", None)
    out["category_id"] = cat_id if cat_id is not None else None
    out["category_code"] = cat_code if cat_code is not None else None
    out["brand"] = brand if brand is not None else None
    out["price"] = price

    # ---- NOT NULL audit on the emitted rows --------------------------------
    for col in EVENT_REQUIRED:
        bad = int(out[col].isna().sum())
        if bad:
            result.events_violations[col] = result.events_violations.get(col, 0) + bad

    return out[EVENT_COLUMNS]


# ---------------------------------------------------------------------------
# streaming transform
# ---------------------------------------------------------------------------
def transform_stream(
    raw_paths: list[Path],
    store_id: str,
    result: TransformResult,
    events_path: Path,
    offset: int,
    chunk_size: int = 1_000_000,
) -> dict[str, pd.DataFrame]:
    """Stream the source files into `events` on disk and build the dimensions.

    Returns the in-memory frames for `stores`, `customers` and `products`. The
    `events` frame is not held — it is written incrementally to `events_path`.
    """
    import time

    acc = {"customers": set(), "products": {}}
    events_path = Path(events_path)
    events_path.parent.mkdir(parents=True, exist_ok=True)

    first_chunk = True
    base_index = 0
    written = 0
    started = time.time()
    for path in raw_paths:
        file_tag = Path(path).name
        print(f"    streaming {file_tag} ...", flush=True)
        for chunk in pd.read_csv(
            path, dtype=str, chunksize=chunk_size, low_memory=False,
        ):
            mapped = _map_chunk(chunk, file_tag, base_index, store_id, offset, acc, result)
            mapped.to_csv(
                events_path, mode="a", header=first_chunk, index=False, na_rep=""
            )
            first_chunk = False
            written += len(mapped)
            base_index += len(chunk)
            if base_index // chunk_size % 25 == 0:
                print(f"      {base_index:,} rows read, {written:,} events, "
                      f"{time.time() - started:.0f}s", flush=True)

    result.events_written = written
    if not written:
        result.note("0 events emitted — check the source files are not empty")

    result.frames = {
        "stores": _stores(store_id),
        "customers": _attach_store(_customers(acc["customers"], result), store_id),
        "products": _attach_store(_products(acc["products"], result), store_id),
    }
    result.note(
        "`events` emitted as a streamed CSV (too large to hold in memory); "
        "customers/products dimensions were derived from the event rows so the "
        "events FKs resolve — see BEHAVIOR_AND_RETAIL.md §3"
    )
    return result.frames


def _stores(store_id: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "id": store_id,
        "name": "Member B Demo Store",
        "plan": "pro",
        "created_at": None,
        "updated_at": None,
    }])[COLUMNS["stores"]]


def _attach_store(frame: pd.DataFrame, store_id: str) -> pd.DataFrame:
    if "store_id" in frame.columns:
        return frame
    out = frame.copy()
    out.insert(1 if "id" in out.columns else 0, "store_id", store_id)
    return out


def _customers(keys: set[str], result: TransformResult) -> pd.DataFrame:
    """user_id roster -> customers. Every other column is NULL by design.

    first_purchase_date is deliberately left NULL: deriving it would be
    aggregation (MIN(order_date)), which the brief rules out. Consistent with
    DUNNHUMBY.md §7.1(d).
    """
    keys = sorted(keys)
    out = pd.DataFrame({"source_customer_id": keys})
    out["id"] = out["source_customer_id"].map(lambda k: uid("customers", k))
    for col in COLUMNS["customers"]:
        if col not in out.columns and col != "store_id":
            out[col] = None
    result.note(
        "customers.first_purchase_date left NULL — derivation would be aggregation; "
        "the platform computes it (DUNNHUMBY.md §7.1(d))"
    )
    return out[[c for c in COLUMNS["customers"] if c != "store_id"]]


def _products(catalog: dict[str, dict], result: TransformResult) -> pd.DataFrame:
    """Product catalog built from the event stream -> products.

    products.name is NOT NULL but this source has no product-name column;
    category_code is the closest descriptive attribute and is used as the name
    (falling back to the source id). FLAGGED as derived, not sourced. brand and
    category come straight from the event rows. unit_price is left NULL
    (Open Decision #11 — price here is event-level, not a catalog price).
    """
    keys = sorted(catalog)
    out = pd.DataFrame({"source_product_id": keys})
    out["id"] = out["source_product_id"].map(lambda k: uid("products", k))
    out["name"] = out["source_product_id"].map(lambda k: catalog[k]["name"])
    out["brand"] = out["source_product_id"].map(lambda k: catalog[k]["brand"])
    out["category"] = out["source_product_id"].map(lambda k: catalog[k]["category_code"])
    out["department"] = None
    out["commodity_desc"] = None
    out["unit_price"] = None
    out["created_at"] = None
    out["updated_at"] = None
    if (out["name"].isna()).any():
        result.note("products.name derived from category_code (no name column in this source)")
    result.note("products.unit_price left NULL — Open Decision #11 (price is event-level here)")
    return out[[c for c in COLUMNS["products"] if c != "store_id"]]
