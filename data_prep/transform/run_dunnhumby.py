"""CLI: transform Dunnhumby raw CSVs into canonical-schema CSVs, ready to load.

Usage:
    python data_prep/transform/run_dunnhumby.py \
        --raw data_prep/raw/dunnhumby \
        --out data_prep/processed/dunnhumby

Reads the 8 source files, transforms them into the 8 canonical tables this
dataset can serve, validates against the constraints backend/schema.sql
declares, then writes one CSV per table plus an audit report.

Load order (respecting FK dependencies) is printed at the end.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import uid  # noqa: E402
from dunnhumby import ANCHOR, COLUMNS, RAW_FILES, load_raw, transform  # noqa: E402
from validate import validate  # noqa: E402

# Insert order: a table can only appear after every table it references.
LOAD_ORDER = [
    "stores",
    "customers",
    "products",
    "orders",
    "order_items",
    "coupon_campaigns",
    "coupon_redemptions",
    "causal_data",
]


def default_store_id() -> str:
    """Deterministic tenant UUID so re-runs produce identical output.

    Not the store's real id — that is assigned by the platform at signup. This
    is a stable placeholder so the transformed files load reproducibly for
    end-to-end platform testing.
    """
    return uid("stores", "dunnhumby-demo")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default="data_prep/raw/dunnhumby",
                        help="directory holding the 8 Dunnhumby source CSVs")
    parser.add_argument("--out", default="data_prep/processed/dunnhumby",
                        help="directory to write canonical CSVs into")
    parser.add_argument("--store-id", default=None,
                        help="tenant store UUID to stamp on every row (default: deterministic)")
    parser.add_argument("--skip-validate", action="store_true",
                        help="write output even if validation fails")
    args = parser.parse_args()

    store_id = args.store_id or default_store_id()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading source files from {args.raw}")
    print(f"  expected: {', '.join(RAW_FILES)}")
    try:
        raw = load_raw(args.raw)
    except FileNotFoundError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    started = time.time()
    result = transform(raw, store_id)
    elapsed = time.time() - started

    print(f"\nTransformed in {elapsed:.1f}s. Row counts:")
    for table in LOAD_ORDER:
        frame = result.frames[table]
        print(f"  {table:<20} {len(frame):>10,} rows")

    # ---- validation -----------------------------------------------------
    report = validate(result.frames, result.raw_counts, result.dropped,
                      result.dropped_by_source, anchor=ANCHOR)
    print()
    print(report.render())

    if result.dropped:
        print("\nDROPPED ROWS (source rows that could not be placed):")
        for reason, count in sorted(result.dropped.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>10,}  {reason}")

    if result.notes:
        print("\nNOTES / UNMAPPED:")
        for note in result.notes:
            print(f"  - {note}")

    if not report.ok and not args.skip_validate:
        print("\nValidation failed — nothing written. Fix the transform or pass "
              "--skip-validate to force output.", file=sys.stderr)
        return 1

    # ---- write CSVs (nullable columns -> empty string, matching SQL NULL) --
    for table in LOAD_ORDER:
        frame = result.frames[table][COLUMNS[table]]
        frame.to_csv(out_dir / f"{table}.csv", index=False, na_rep="")

    audit = {
        "source_dataset": "frtgnn/dunnhumby-the-complete-journey",
        "store_id_placeholder": store_id,
        "raw_counts": result.raw_counts,
        "emitted_counts": {t: int(len(result.frames[t])) for t in LOAD_ORDER},
        "dropped": result.dropped,
        "dropped_by_source": result.dropped_by_source,
        "notes": result.notes,
        "validation": {"ok": report.ok,
                       "failures": report.failures,
                       "warnings": report.warnings},
        "load_order": LOAD_ORDER,
    }
    (out_dir / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")

    print(f"\nWrote {len(LOAD_ORDER)} canonical CSVs + audit.json to {out_dir}")
    print("\nLoad order (FK-safe): " + " -> ".join(LOAD_ORDER))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
