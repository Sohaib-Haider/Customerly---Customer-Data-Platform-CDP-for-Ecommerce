"""Command-line interface: ``python -m apps.mapping <command>``.

Commands
--------
verify   Parse backend/schema.sql + final-schema.md, cross-check them and the
         curated layers (aliases, FK targets), print findings. Exit code 1 on
         drift so CI/review can gate on it.
map      Map a CSV's columns to the canonical schema -> <out>.mapping.json.
review   Approve / correct / reject proposals -> revised JSON document.
apply    Write per-table CSVs for APPROVED direct mappings + apply_report.json.

Global honesty rules baked into every command:
* the API key is never printed (see apps.mapping.config.scrub);
* `map` degrades instead of crashing when Gemini is unavailable;
* `review` validates corrections against the real schema;
* `apply` refuses anything that is not an approved direct mapping.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_MODEL, load_env, scrub
from .detect import KINDS
from .models import (
    ALL_STATUSES,
    STATUS_APPROVED,
    STATUS_NEEDS_REVIEW,
    STATUS_SUGGESTED,
)
from .schema_catalog import get_catalog


def _parse_set_targets(values: Sequence[str]) -> list[tuple[str, str, str]]:
    """'col=table.column' -> (col, table, column)."""
    parsed: list[tuple[str, str, str]] = []
    for raw in values:
        if "=" not in raw or "." not in raw.split("=", 1)[1]:
            raise SystemExit(f"--set expects col=table.column, got {raw!r}")
        column, _, target = raw.partition("=")
        table, _, field_name = target.partition(".")
        parsed.append((column.strip(), table.strip(), field_name.strip()))
    return parsed


def _split_csv(values: Sequence[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        items.extend(part.strip() for part in value.split(",") if part.strip())
    return items


# --------------------------------------------------------------------------- verify
def cmd_verify(args: argparse.Namespace) -> int:
    catalog = get_catalog()
    findings = catalog.verify() + catalog.verify_aliases()
    print(f"schema.sql tables parsed:       {len(catalog.tables)} (expected 23)")
    print(f"ingestible tables:              {len(catalog.mappable)}/{11} have mappable fields")
    print(f"mappable canonical fields:      {catalog.mappable_count}")
    if args.show_targets:
        print("\nUpload-mappable targets:")
        for line in catalog.prompt_targets():
            print(f"  {line}")
    if findings:
        print(f"\nDRIFT FINDINGS ({len(findings)}) — reported, not resolved:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print("\nOK: schema.sql, final-schema.md and curated layers agree.")
    return 0


# --------------------------------------------------------------------------- map
def cmd_map(args: argparse.Namespace) -> int:
    from .mapper import map_csv
    from .review import save_document

    load_env()
    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"error: {csv_path} not found", file=sys.stderr)
        return 2

    try:
        result = map_csv(
            csv_path,
            use_llm=not args.no_llm,
            model=args.model,
            kind_override=args.kind,
        )
    except ValueError as exc:
        print(f"error: {scrub(str(exc))}", file=sys.stderr)
        return 2

    out_path = (
        Path(args.output)
        if args.output and args.output != "-"
        else csv_path.with_suffix(".mapping.json")
    )
    if args.output == "-":
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        save_document(result, out_path)

    # human summary (never includes the API key). With `-o -` the JSON owns
    # stdout, so the summary goes to stderr and stdout stays parseable.
    summary_stream = sys.stderr if args.output == "-" else sys.stdout
    summary = result.summary()
    detection = result.source_profile.get("kind_detection", {})
    print(f"\nmapped {csv_path.name} ({summary['columns']} columns, "
          f"kind={detection.get('effective_kind')}, "
          f"review_required={detection.get('review_required')})",
          file=summary_stream)
    for status in ALL_STATUSES:
        count = summary["by_status"].get(status, 0)
        if count:
            print(f"  {status:14} {count}", file=summary_stream)
    if result.llm_info.get("used"):
        print(f"  llm: model={result.llm_info['model']} "
              f"columns_sent={result.llm_info['columns_sent']}", file=summary_stream)
    elif (result.llm_info.get("error") or "").startswith("llm_skipped"):
        print("  llm: skipped (--no-llm)", file=summary_stream)
    else:
        print(f"  llm: {scrub(result.llm_info.get('error') or 'unavailable')}",
              file=summary_stream)
    if args.output != "-":
        print(f"  -> {out_path}", file=summary_stream)
        print("  next: python -m apps.mapping review "
              f"{out_path} -o <approved.json>", file=summary_stream)
    return 0


# --------------------------------------------------------------------------- review
def cmd_review(args: argparse.Namespace) -> int:
    from .review import ReviewError, review_file

    load_env()
    input_path = Path(args.document)
    if not input_path.is_file():
        print(f"error: {input_path} not found", file=sys.stderr)
        return 2
    output_path = Path(args.output) if args.output else input_path

    try:
        result, changes = review_file(
            input_path,
            output_path,
            approve=_split_csv(args.approve or ()),
            reject=_split_csv(args.reject or ()),
            set_targets=_parse_set_targets(args.set or []),
            approve_exact=args.approve_exact,
            approve_suggested=args.approve_suggested,
            kind=args.kind,
            interactive=args.interactive,
        )
    except (ReviewError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {scrub(str(exc))}", file=sys.stderr)
        return 2

    approved = sum(1 for m in result.mappings if m.status == STATUS_APPROVED)
    if changes:
        for change in changes:
            print(f"  * {change}")
    else:
        print("  (no decisions applied)")
    print(f"\n{approved} approved / {len(result.mappings)} total -> {output_path}")
    pending = [m for m in result.mappings if m.status in (STATUS_SUGGESTED, STATUS_NEEDS_REVIEW)]
    if pending and not args.interactive:
        print(f"  {len(pending)} still awaiting review "
              "(rerun with --interactive, or --approve .../--set .../--reject ...)")
    return 0


# --------------------------------------------------------------------------- apply
def cmd_apply(args: argparse.Namespace) -> int:
    from .apply import apply_approved
    from .review import load_document

    document_path = Path(args.document)
    csv_path = Path(args.csv)
    for path in (document_path, csv_path):
        if not path.is_file():
            print(f"error: {path} not found", file=sys.stderr)
            return 2
    try:
        result = load_document(document_path)
        report = apply_approved(document_path, csv_path, args.output_dir, mapping_result=result)
    except ValueError as exc:
        print(f"error: {scrub(str(exc))}", file=sys.stderr)
        return 2

    if not report.applied:
        print("nothing applied — no mappings are `approved` yet.")
        print("approve first: python -m apps.mapping review <doc.json> --approve col1,col2")
    for applied in report.applied:
        print(f"  applied {applied.source_column} -> "
              f"{applied.target_table}.{applied.target_column}")
    for conflict in report.conflicts:
        print(f"  conflict: {conflict}")
    print(f"\nrows: {report.row_count} | applied: {len(report.applied)} | "
          f"skipped: {len(report.skipped)} | files in {args.output_dir}/")
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.mapping",
        description="Gemini-assisted CSV column mapping onto the Customerly canonical schema.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify", help="cross-check schema.sql / final-schema.md / curated layers")
    p_verify.add_argument("--show-targets", action="store_true", help="list every eligible target")
    p_verify.set_defaults(func=cmd_verify)

    p_map = sub.add_parser("map", help="map a CSV's columns -> *.mapping.json")
    p_map.add_argument("csv", help="source CSV file (header is what matters)")
    p_map.add_argument("-o", "--output", help="output JSON path, or '-' for stdout")
    p_map.add_argument("--no-llm", action="store_true", help="skip Gemini (exact/alias only)")
    p_map.add_argument("--model", help=f"Gemini model override (default: $GEMINI_MODEL or {DEFAULT_MODEL})")
    p_map.add_argument("--kind", choices=list(KINDS),
                       help="override detected source-entity kind (reviewable)")
    p_map.set_defaults(func=cmd_map)

    p_review = sub.add_parser("review", help="approve/correct/reject proposals")
    p_review.add_argument("document", help="mapping JSON from `map`")
    p_review.add_argument("-o", "--output", help="write result here (default: in place)")
    p_review.add_argument("--approve", action="append", metavar="COLS",
                          help="comma-separated source columns to approve")
    p_review.add_argument("--reject", action="append", metavar="COLS",
                          help="comma-separated source columns to reject")
    p_review.add_argument("--set", action="append", metavar="COL=TABLE.COLUMN",
                          help="manually set (and approve) a target")
    p_review.add_argument("--approve-exact", action="store_true",
                          help="approve every method=exact row")
    p_review.add_argument("--approve-suggested", action="store_true",
                          help="approve every status=suggested row")
    p_review.add_argument("--kind", choices=list(KINDS),
                          help="override the detected source-entity kind")
    p_review.add_argument("--interactive", action="store_true",
                          help="prompt for every non-final mapping")
    p_review.set_defaults(func=cmd_review)

    p_apply = sub.add_parser("apply", help="apply APPROVED direct mappings to a CSV")
    p_apply.add_argument("document", help="reviewed mapping JSON")
    p_apply.add_argument("csv", help="the same source CSV that produced the document")
    p_apply.add_argument("-o", "--output-dir", default="mapping_output",
                         help="directory for per-table CSVs (default: mapping_output/)")
    p_apply.set_defaults(func=cmd_apply)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
