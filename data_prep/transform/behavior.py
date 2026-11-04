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
    DEMO_STORE_NAME, set_tenant,
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
    sampled_counts: dict[str, int] = field(default_factory=dict)
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
    s = s.mask(s.str.lower().isin(["nan", "none", "null", "<na>"]), "")
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
    raw_dir = Path(raw_dir)
    paths = []
    for alternatives in zip(RAW_FILES, RAW_FILES_GZ):
        found = next((raw_dir / n for n in alternatives if (raw_dir / n).is_file()), None)
        if found is None:
            raise FileNotFoundError(f"Missing configured month: {alternatives} in {raw_dir}")
        paths.append(found)
    return paths


def compute_offset(raw_paths: list[Path], chunk_size: int) -> int:
    """Real source timestamps are retained; no alignment scan is needed."""
    return 0


def session_sample_mask(chunk: pd.DataFrame, fraction: float, seed: int) -> pd.Series:
    """Select whole customer sessions consistently across chunks and months.

    Each session has the same inclusion probability, so the retained row share
    is approximately fraction, not exactly fraction. No source rows are changed.
    """
    if not 0 < fraction <= 1:
        raise ValueError("sample_fraction must be greater than 0 and at most 1")
    if not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ValueError("sample_seed must be an integer in [0, 2**64)")
    if fraction == 1:
        return pd.Series(True, index=chunk.index)
    keys = chunk[["user_id", "user_session"]].fillna("").astype(str)
    keys = keys.apply(lambda column: column.str.strip())
    hashes = pd.util.hash_pandas_object(
        keys, index=False, hash_key=f"{seed:016x}", categorize=False,
    )
    return hashes < int(fraction * 2**64)


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
    source_positions=None,
) -> pd.DataFrame:
    """Map one chunk of raw events into canonical `events` rows + audit.

    Returns a DataFrame in EVENT_COLUMNS order (store_id included). `acc` holds
    the running customer set and product catalog; `result` records every drop.
    """
    n = len(chunk)
    positions = np.arange(n) if source_positions is None else np.asarray(source_positions)
    result.raw_counts[file_tag] = result.raw_counts.get(file_tag, 0) + n

    user = _namespace(chunk["user_id"])
    prod = _namespace(chunk["product_id"])
    sess = _namespace(chunk["user_session"])
    # dtype=str leaves missing cells as NaN; fillna("") keeps them blank rather
    # than turning them into the literal text "nan" before the empty check.
    etype = chunk["event_type"].fillna("").astype(str).str.strip()
    ts = _parse_times(chunk["event_time"])
    price = chunk["price"]  # Preserve exact source decimals; validated before publication.
    cat_id = chunk.get("category_id")
    cat_code = chunk.get("category_code")
    brand = chunk.get("brand")

    # ---- drops: only rows that could not be loaded or would break a NOT NULL
    result.drop("events with empty user_id", int((user == "").sum()), source=file_tag)
    result.drop("events with empty user_session", int((sess == "").sum()), source=file_tag)
    result.drop("events with empty event_type", int((etype == "").sum()), source=file_tag)
    result.drop("events with unparseable event_time", int(ts.isna().sum()), source=file_tag)
    keep = (user != "") & (sess != "") & (etype != "") & ts.notna()
    result.dropped_by_source[file_tag] = result.dropped_by_source.get(file_tag, 0) - (
        int((user == "").sum()) + int((sess == "").sum()) + int((etype == "").sum()) + int(ts.isna().sum())
    ) + int((~keep).sum())
    if not keep.all() and acc.get("exceptions_dir") is not None:
        rejected = chunk.loc[~keep].copy()
        rejected["source_row"] = base_index + positions[(~keep).to_numpy()]
        rejected["exception_reason"] = ["; ".join(reason for bad, reason in (
            (user.loc[i] == "", "missing user_id"), (sess.loc[i] == "", "missing session"),
            (etype.loc[i] == "", "missing event_type"), (pd.isna(ts.loc[i]), "invalid timestamp")) if bad)
            for i in rejected.index]
        destination = acc["exceptions_dir"] / file_tag
        destination.parent.mkdir(parents=True, exist_ok=True)
        rejected.to_csv(destination, mode="a", header=not destination.exists(), index=False)

    # ---- dimension accumulators, on the rows that survive -----------------
    ku = user[keep]
    acc["customers"].update(ku.unique())
    c_series = cat_code[keep] if cat_code is not None else None
    b_series = brand[keep] if brand is not None else None
    sub = pd.DataFrame({"p": prod[keep], "c": c_series, "b": b_series})
    sub = sub[sub["p"] != ""]
    if len(sub):
        sub = sub.replace("", None).groupby("p", sort=False, as_index=False).first()
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
    idx = (base_index + positions[keep.to_numpy()]).astype(str)
    out["id"] = [uid("events", k) for k in np.char.add(f"evb:{file_tag}|", idx)]
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
    *,
    sample_fraction: float = 1.0,
    sample_seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """Stream the source files into `events` on disk and build the dimensions.

    Optional sampling keeps complete customer sessions before preprocessing,
    without creating an intermediate copy. raw_counts counts all scanned rows;
    sampled_counts counts selected input rows (before cleaning). Intentionally
    unselected rows are not validation failures. Event IDs retain source offsets.

    Returns the in-memory frames for `stores`, `customers` and `products`. The
    `events` frame is not held — it is written incrementally to `events_path`.
    """
    import time

    # Validate configuration even when a source file has no data rows.
    session_sample_mask(pd.DataFrame(columns=["user_id", "user_session"]),
                        sample_fraction, sample_seed)

    set_tenant(store_id)
    acc = {"customers": set(), "products": {}}
    events_path = Path(events_path)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    acc["exceptions_dir"] = events_path.parent.parent / "exceptions"

    first_chunk = True
    base_index = 0
    written = 0
    started = time.time()
    for path in raw_paths:
        base_index = 0
        file_tag = Path(path).name.removesuffix(".gz")
        result.raw_counts.setdefault(file_tag, 0)
        result.sampled_counts.setdefault(file_tag, 0)
        print(f"    streaming {file_tag} ...", flush=True)
        for chunk in pd.read_csv(
            path, dtype=str, chunksize=chunk_size, low_memory=False, keep_default_na=False, na_values=[""],
        ):
            source_count = len(chunk)
            selected = session_sample_mask(chunk, sample_fraction, sample_seed)
            positions = np.flatnonzero(selected.to_numpy())
            result.sampled_counts[file_tag] += len(positions)
            # _map_chunk records selected rows; account for the rest here so
            # raw_counts still represents the complete, unchanged source file.
            result.raw_counts[file_tag] += source_count - len(positions)
            if not len(positions):
                base_index += source_count
                continue
            mapped = _map_chunk(chunk.loc[selected], file_tag, base_index,
                                store_id, offset, acc, result,
                                source_positions=positions)
            mapped.to_csv(
                events_path, mode="w" if first_chunk else "a", header=first_chunk, index=False, na_rep=""
            )
            first_chunk = False
            written += len(mapped)
            base_index += source_count
            if base_index // chunk_size % 25 == 0:
                print(f"      {base_index:,} rows read, {written:,} events, "
                      f"{time.time() - started:.0f}s", flush=True)

        total = result.raw_counts[file_tag]
        retained = result.sampled_counts[file_tag]
        share = retained / total if total else 0
        print(f"    {file_tag}: selected {retained:,}/{total:,} rows ({share:.2%})", flush=True)
        result.note(f"{file_tag}: session sampling fraction={sample_fraction}, "
                    f"seed={sample_seed}; scanned={total}, selected={retained}, "
                    f"intentionally excluded={total - retained}. "
                    "Selection precedes cleaning; UUIDs use original source row offsets.")

    if first_chunk:
        pd.DataFrame(columns=EVENT_COLUMNS).to_csv(events_path, index=False)

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
    from contract import canonical
    result.frames = {t: canonical(t, f, store_id) for t, f in result.frames.items()}
    return result.frames


def _stores(store_id: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "id": store_id,
        "name": DEMO_STORE_NAME,
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
