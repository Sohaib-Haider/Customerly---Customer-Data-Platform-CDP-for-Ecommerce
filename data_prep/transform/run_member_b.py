"""CLI: transform Member B's two datasets into canonical-schema CSVs, ready to load.

Usage:
    python data_prep/transform/run_member_b.py \
        --behavior-raw data_prep/raw/behavior \
        --retail-raw  data_prep/raw/retail \
        --out         data_prep/processed/member_b

Reads the behaviour event stream (2019-Oct/Nov) and Online Retail II, maps both
onto the canonical schema, merges the shared `customers`/`products`/`stores`
dimensions under one tenant, validates against backend/schema.sql, then writes
one CSV per table plus an audit report.

Load order (FK-safe) is printed at the end.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

import behavior  # noqa: E402
import retail  # noqa: E402
from common import SHARED_WINDOW_START, uid  # noqa: E402
from validate import validate  # noqa: E402

TABLES = ["stores", "customers", "products", "orders", "order_items", "events"]
LOAD_ORDER = ["stores", "customers", "products", "orders", "order_items", "events"]

# One canonical column order per emitted table (verbatim from backend/schema.sql).
COLUMNS = {
    "stores": behavior.COLUMNS["stores"],
    "customers": behavior.COLUMNS["customers"],
    "products": behavior.COLUMNS["products"],
    "orders": retail.COLUMNS["orders"],
    "order_items": retail.COLUMNS["order_items"],
    "events": behavior.COLUMNS["events"],
}
COLUMNS_NO_EVENTS = {t: c for t, c in COLUMNS.items() if t != "events"}


def default_store_id() -> str:
    """Deterministic tenant UUID so re-runs produce identical output.

    Pass the same --store-id to every member's transform if all datasets are to
    share one demo store (see README.md convention 1).
    """
    return uid("stores", "member-b-demo")


def _merge_counts(*results) -> tuple[dict, dict, dict]:
    raw_counts: dict[str, int] = {}
    dropped: dict[str, int] = {}
    dropped_by_source: dict[str, int] = {}
    for r in results:
        for k, v in r.raw_counts.items():
            raw_counts[k] = raw_counts.get(k, 0) + v
        for k, v in r.dropped.items():
            dropped[k] = dropped.get(k, 0) + v
        for k, v in r.dropped_by_source.items():
            dropped_by_source[k] = dropped_by_source.get(k, 0) + v
    return raw_counts, dropped, dropped_by_source


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--behavior-raw", default="data_prep/raw/behavior",
                        help="directory holding 2019-Oct/Nov.csv[.gz]")
    parser.add_argument("--retail-raw", default="data_prep/raw/retail",
                        help="directory holding online_retail_II.xlsx or .csv")
    parser.add_argument("--out", default="data_prep/processed/member_b",
                        help="directory to write canonical CSVs into")
    parser.add_argument("--store-id", default=None,
                        help="tenant store UUID to stamp on every row (default: deterministic)")
    parser.add_argument("--chunk-size", type=int, default=1_000_000,
                        help="behaviour rows per streaming chunk")
    parser.add_argument("--events-readback-max-bytes", type=int, default=200_000_000,
                        help="read events.csv back for validation only when at most this size")
    parser.add_argument("--skip-validate", action="store_true",
                        help="write output even if validation fails")
    args = parser.parse_args()

    store_id = args.store_id or default_store_id()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- behaviour (streamed events) ---------------------------------------
    print("Reading behaviour sources ...")
    behavior_paths = behavior.resolve_raw_files(args.behavior_raw)
    for p in behavior_paths:
        print(f"  {p}")
    started = time.time()
    offset = behavior.compute_offset(behavior_paths, args.chunk_size)
    print(f"  shared timeline offset: {offset} days "
          f"(earliest event -> {SHARED_WINDOW_START})")

    b_result = behavior.TransformResult()
    events_path = out_dir / "events.csv"
    if events_path.exists():
        events_path.unlink()
    b_frames = behavior.transform_stream(
        behavior_paths, store_id, b_result, events_path, offset, args.chunk_size
    )
    print(f"  behaviour: {b_result.events_written:,} events written to {events_path.name} "
          f"in {time.time() - started:.1f}s")

    # ---- Online Retail II --------------------------------------------------
    print("Reading Online Retail II ...")
    r_raw = retail.load_raw(args.retail_raw)
    r_result = retail.transform(r_raw, store_id)
    r_frames = r_result.frames

    # ---- merge shared dimensions under one tenant --------------------------
    frames: dict[str, pd.DataFrame] = {}
    frames["stores"] = (
        pd.concat([b_frames["stores"], r_frames["stores"]], ignore_index=True)
        .drop_duplicates(subset="id")
    )
    frames["customers"] = (
        pd.concat([b_frames["customers"], r_frames["customers"]], ignore_index=True)
        .drop_duplicates(subset=["store_id", "source_customer_id"])
    )
    frames["products"] = (
        pd.concat([b_frames["products"], r_frames["products"]], ignore_index=True)
        .drop_duplicates(subset=["store_id", "source_product_id"])
    )
    frames["orders"] = r_frames["orders"]
    frames["order_items"] = r_frames["order_items"]

    # events is too large to hold for the real dataset; read it back for
    # validation only when it is small (fixtures / smoke runs).
    events_size = events_path.stat().st_size if events_path.exists() else 0
    readback = events_size <= args.events_readback_max_bytes
    if readback:
        # dtype=str keeps source ids and category_id as text; reading with
        # inferred types would turn '11' into 11.0 and rewrite the file.
        frames["events"] = pd.read_csv(events_path, dtype=str, low_memory=False)
    else:
        print(f"  events.csv is {events_size / 1e9:.1f} GB — skipping in-memory readback; "
              "events NOT NULL/FK integrity is guaranteed by construction "
              "(customers/products are built from the same rows)")

    # ---- validation --------------------------------------------------------
    raw_counts, dropped, dropped_by_source = _merge_counts(b_result, r_result)
    columns = COLUMNS if readback else COLUMNS_NO_EVENTS
    report = validate(
        {t: f for t, f in frames.items() if t in columns},
        raw_counts, dropped, dropped_by_source, anchor=None, columns=columns,
    )
    print()
    print(report.render())

    if b_result.dropped or r_result.dropped:
        print("\nDROPPED ROWS (source rows that could not be placed):")
        for reason, count in sorted(dropped.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>10,}  {reason}")

    notes = list(dict.fromkeys(b_result.notes + r_result.notes))
    if notes:
        print("\nNOTES / UNMAPPED:")
        for note in notes:
            print(f"  - {note}")

    if not report.ok and not args.skip_validate:
        print("\nValidation failed — nothing written. Fix the transform or pass "
              "--skip-validate to force output.", file=sys.stderr)
        return 1

    # ---- write CSVs ---------------------------------------------------------
    for table in LOAD_ORDER:
        if table in frames:
            frames[table].to_csv(out_dir / f"{table}.csv", index=False, na_rep="")

    audit = {
        "member": "B",
        "store_id_placeholder": store_id,
        "behavior": {
            "sources": [str(p) for p in behavior_paths],
            "shared_timeline_offset_days": offset,
            "events_written": b_result.events_written,
            "events_violations": b_result.events_violations,
            "raw_counts": b_result.raw_counts,
            "dropped": b_result.dropped,
            "dropped_by_source": b_result.dropped_by_source,
            "notes": b_result.notes,
        },
        "retail": {
            "raw_counts": r_result.raw_counts,
            "dropped": r_result.dropped,
            "dropped_by_source": r_result.dropped_by_source,
            "notes": r_result.notes,
        },
        "emitted_counts": {t: int(len(frames[t])) for t in frames},
        "events_readback_validated": readback,
        "validation": {"ok": report.ok,
                       "failures": report.failures,
                       "warnings": report.warnings},
        "load_order": LOAD_ORDER,
    }
    (out_dir / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")

    print(f"\nWrote {len(LOAD_ORDER)} canonical CSVs + audit.json to {out_dir}")
    print("Load order (FK-safe): " + " -> ".join(LOAD_ORDER))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
