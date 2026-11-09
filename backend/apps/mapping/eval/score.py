"""Score column_mapping outputs against the real-dataset answer key.

Usage:
    python -m apps.mapping.eval.score MAPPING_DIR [--truth PATH] [--json OUT]

MAPPING_DIR holds one ``<dataset>__<file-stem>.mapping.json`` per source file,
as produced by ``python -m apps.mapping map``. A column is correct when its
proposed target (or "NONE" when no target was proposed) is in the answer key's
acceptable set. Columns whose answer is NONE measure false positives; the rest
measure recall.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_TRUTH = Path(__file__).with_name("ground_truth.json")


def _predicted(mapping: dict) -> str:
    if mapping.get("target_table") and mapping.get("target_column"):
        return f"{mapping['target_table']}.{mapping['target_column']}"
    return "NONE"


def _truth_key(mapping_file: Path) -> str:
    stem = mapping_file.name.removesuffix(".mapping.json")
    dataset, _, file_stem = stem.partition("__")
    return f"{dataset}/{file_stem}.csv"


def score(mapping_dir: Path, truth_path: Path = DEFAULT_TRUTH) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))["files"]
    rows, missing_files = [], []
    seen = set()
    for path in sorted(mapping_dir.glob("*.mapping.json")):
        key = _truth_key(path)
        if key not in truth:
            continue
        seen.add(key)
        doc = json.loads(path.read_text(encoding="utf-8"))
        by_col = {m["source_column"]: m for m in doc["mappings"]}
        for column, accepted in truth[key].items():
            m = by_col.get(column, {})
            pred = _predicted(m) if m else "NONE"
            rows.append({
                "file": key, "column": column, "predicted": pred,
                "accepted": accepted, "correct": pred in accepted,
                "method": m.get("method", "missing"), "status": m.get("status"),
                "confidence": m.get("confidence"),
                "expects_target": accepted != ["NONE"] and "NONE" not in accepted,
                "expects_none": accepted == ["NONE"],
            })
    missing_files = sorted(set(truth) - seen)

    def pct(n, d):
        return round(100 * n / d, 1) if d else None

    by_method = defaultdict(Counter)
    for r in rows:
        by_method[r["method"]]["total"] += 1
        by_method[r["method"]]["correct"] += r["correct"]
    target_rows = [r for r in rows if r["expects_target"]]
    none_rows = [r for r in rows if r["expects_none"]]
    summary = {
        "columns": len(rows),
        "accuracy": pct(sum(r["correct"] for r in rows), len(rows)),
        "recall_on_mappable": pct(sum(r["correct"] for r in target_rows), len(target_rows)),
        "wrong_target": sum(1 for r in target_rows if not r["correct"] and r["predicted"] != "NONE"),
        "missed": sum(1 for r in target_rows if r["predicted"] == "NONE"),
        "false_positive_on_unmappable": sum(1 for r in none_rows if not r["correct"]),
        "unmappable_columns": len(none_rows),
        "by_method": {k: {"total": v["total"], "correct": v["correct"]} for k, v in sorted(by_method.items())},
        "missing_files": missing_files,
    }
    return {"summary": summary, "errors": [r for r in rows if not r["correct"]], "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mapping_dir", type=Path)
    ap.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args(argv)
    report = score(args.mapping_dir, args.truth)
    s = report["summary"]
    print(f"columns scored:            {s['columns']}")
    print(f"overall accuracy:          {s['accuracy']}%")
    print(f"recall on mappable cols:   {s['recall_on_mappable']}%")
    print(f"wrong target:              {s['wrong_target']}")
    print(f"missed (left unmapped):    {s['missed']}")
    print(f"false positives:           {s['false_positive_on_unmappable']} of {s['unmappable_columns']} unmappable columns")
    print("by method:                 " + ", ".join(f"{k} {v['correct']}/{v['total']}" for k, v in s["by_method"].items()))
    if s["missing_files"]:
        print(f"files with no mapping output: {len(s['missing_files'])}")
    print("\nerrors:")
    for r in report["errors"]:
        print(f"  {r['file']:<62} {r['column']:<28} got {r['predicted']:<40} want {' | '.join(r['accepted'])}  [{r['method']}]")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
