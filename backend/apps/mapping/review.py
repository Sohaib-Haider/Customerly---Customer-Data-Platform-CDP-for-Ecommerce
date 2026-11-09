"""Manual review: approve / correct / reject mapping proposals.

The ONLY place where a mapping becomes ``approved`` (or ``rejected``). The
LLM's confidence plays no role here — approval is always an explicit human
decision, expressed either interactively or via the non-interactive flags:

    --approve  col1,col2      suggested/needs_review -> approved
    --approve-exact           every method=exact row -> approved
    --approve-suggested       every status=suggested row -> approved
    --set       col=tbl.col   manual target (method becomes `manual`)
    --reject    col           -> rejected (target kept for audit)
    --kind      <kind>        override the detected source-entity kind

Every correction is validated against the canonical schema gate
(`SchemaCatalog.validate`) — a typo'd table or column is a hard error, never
silently accepted. Corrections record ``corrected_from`` so the JSON document
remains an auditable trail, and are stored under ``method: manual`` so an
automated suggestion is never mistaken for a human-confirmed one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .detect import KINDS
from .models import (
    METHOD_MANUAL,
    STATUS_APPROVED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    STATUS_SUGGESTED,
    ColumnMapping,
    MappingResult,
)
from .schema_catalog import SchemaCatalog, get_catalog


class ReviewError(Exception):
    """Invalid review input (bad column name, bad target, unknown status)."""


def load_document(path: str | Path) -> MappingResult:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return MappingResult.from_dict(data)


def save_document(result: MappingResult, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _require_mapping(result: MappingResult, source_column: str) -> ColumnMapping:
    mapping = result.mapping_for(source_column)
    if mapping is None:
        known = ", ".join(m.source_column for m in result.mappings)
        raise ReviewError(f"unknown source column {source_column!r} (known: {known})")
    return mapping


def _approve(result: MappingResult, source_column: str, changes: list[str]) -> None:
    mapping = _require_mapping(result, source_column)
    if mapping.status == STATUS_REJECTED:
        raise ReviewError(
            f"{source_column!r} is rejected — un-reject it first (map again) to approve"
        )
    if not mapping.has_target:
        raise ReviewError(
            f"{source_column!r} has no target — use --set {source_column}=table.column"
        )
    if mapping.status != STATUS_APPROVED:
        mapping.status = STATUS_APPROVED
        changes.append(f"approved {source_column} -> {mapping.target_table}.{mapping.target_column}")


def _reject(result: MappingResult, source_column: str, changes: list[str]) -> None:
    mapping = _require_mapping(result, source_column)
    if mapping.status != STATUS_REJECTED:
        mapping.status = STATUS_REJECTED
        changes.append(
            f"rejected {source_column}"
            + (f" (was {mapping.target_table}.{mapping.target_column})" if mapping.has_target else "")
        )


def _set_target(
    result: MappingResult,
    source_column: str,
    table: str,
    column: str,
    catalog: SchemaCatalog,
    changes: list[str],
) -> None:
    mapping = _require_mapping(result, source_column)
    field_ = catalog.validate(table, column)
    if field_ is None:
        raise ReviewError(
            f"correction {source_column} -> {table}.{column} rejected: not an "
            f"upload-mappable canonical field (use `python -m apps.mapping verify` "
            f"to list eligible targets)"
        )
    before = f"{mapping.target_table}.{mapping.target_column}" if mapping.has_target else None
    mapping.target_table = table
    mapping.target_column = column
    mapping.status = STATUS_APPROVED
    if mapping.method != METHOD_MANUAL:
        mapping.corrected_from = (
            f"method={mapping.method}" + (f", target={before}" if before else ", target=<none>")
        )
        mapping.method = METHOD_MANUAL
    # Human-verified target: confidence reflects certainty of the reviewer,
    # not the model's.
    mapping.confidence = 1.0
    if field_.requires:
        mapping.requires = list(dict.fromkeys([*mapping.requires, *field_.requires]))
    changes.append(
        f"set {source_column} -> {table}.{column} (approved"
        + (f"; was {before}" if before else "")
        + ")"
    )


def _override_kind(result: MappingResult, kind: str, changes: list[str]) -> None:
    if kind not in KINDS:
        raise ReviewError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")
    detection = result.source_profile.setdefault("kind_detection", {})
    previous = detection.get("override_kind") or detection.get("primary_kind")
    detection["override_kind"] = kind
    detection["review_required"] = False
    changes.append(f"override source kind: {previous} -> {kind}")


def apply_decisions(
    result: MappingResult,
    *,
    approve: Iterable[str] = (),
    reject: Iterable[str] = (),
    set_targets: Iterable[tuple[str, str, str]] = (),
    approve_exact: bool = False,
    approve_suggested: bool = False,
    kind: str | None = None,
    catalog: SchemaCatalog | None = None,
) -> list[str]:
    """Apply non-interactive decisions. Returns the list of changes made.

    Raises ReviewError on any invalid input — nothing is written on failure
    because callers save only after this returns.
    """
    catalog = catalog or get_catalog()
    changes: list[str] = []

    if kind is not None:
        _override_kind(result, kind, changes)
    for source_column, table, column in set_targets:
        _set_target(result, source_column, table, column, catalog, changes)
    for source_column in approve:
        _approve(result, source_column, changes)
    if approve_exact:
        for mapping in result.mappings:
            if mapping.method == "exact" and mapping.has_target and mapping.status in (
                STATUS_SUGGESTED,
                STATUS_NEEDS_REVIEW,
            ):
                _approve(result, mapping.source_column, changes)
    if approve_suggested:
        for mapping in result.mappings:
            if mapping.status == STATUS_SUGGESTED and mapping.has_target:
                _approve(result, mapping.source_column, changes)
    for source_column in reject:
        _reject(result, source_column, changes)

    if changes:
        result.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result.source_profile.setdefault("kind_detection", {})
        result.source_profile["review"] = {
            "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "changes": changes,
        }
    return changes


_INTERACTIVE_ACTION_HELP = (
    "[a]pprove / [r]eject / [e]dit target / [n]ext / [q]uit "
    "(blank = next, shows detail first)"
)


def run_interactive(
    result: MappingResult,
    *,
    catalog: SchemaCatalog | None = None,
    input_fn=input,
    print_fn=print,
) -> list[str]:
    """Walk every non-final mapping, one prompt per column. Returns changes."""
    catalog = catalog or get_catalog()
    changes: list[str] = []
    try:
        for mapping in result.mappings:
            if mapping.status in (STATUS_APPROVED, STATUS_REJECTED):
                continue
            target = (
                f"{mapping.target_table}.{mapping.target_column}" if mapping.has_target else "<none>"
            )
            print_fn(
                f"\n{mapping.source_column} -> {target}  "
                f"[{mapping.method}/{mapping.status}"
                + (f" conf={mapping.confidence}" if mapping.confidence is not None else "")
                + "]"
            )
            if mapping.candidates:
                options = ", ".join(
                    f"{c.target_table}.{c.target_column}" for c in mapping.candidates
                )
                print_fn(f"  candidates: {options}")
            if mapping.requires:
                print_fn(f"  requires: {', '.join(mapping.requires)}")
            print_fn(f"  {mapping.explanation}")
            while True:
                choice = input_fn(f"  action ({_INTERACTIVE_ACTION_HELP}): ").strip().lower()
                if choice in ("", "n", "next"):
                    break
                if choice in ("q", "quit"):
                    raise StopIteration
                if choice in ("a", "approve"):
                    _approve(result, mapping.source_column, changes)
                    break
                if choice in ("r", "reject"):
                    _reject(result, mapping.source_column, changes)
                    break
                if choice in ("e", "edit"):
                    raw = input_fn(
                        f"    new target for {mapping.source_column} (table.column): "
                    ).strip()
                    if "." not in raw:
                        print_fn("    expected table.column")
                        continue
                    table, _, column = raw.partition(".")
                    try:
                        _set_target(
                            result, mapping.source_column, table.strip(), column.strip(),
                            catalog, changes,
                        )
                    except ReviewError as exc:
                        print_fn(f"    {exc}")
                        continue
                    break
            print_fn("")  # spacing between rows
    except StopIteration:
        print_fn("\nreview stopped by user")
    if changes:
        result.source_profile["review"] = {
            "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "changes": changes,
        }
    return changes


def review_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    approve: Iterable[str] = (),
    reject: Iterable[str] = (),
    set_targets: Iterable[tuple[str, str, str]] = (),
    approve_exact: bool = False,
    approve_suggested: bool = False,
    kind: str | None = None,
    interactive: bool = False,
    catalog: SchemaCatalog | None = None,
) -> tuple[MappingResult, list[str]]:
    """Load -> decide -> save. Raises ReviewError/ValueError before saving."""
    result = load_document(input_path)
    changes = apply_decisions(
        result,
        approve=approve,
        reject=reject,
        set_targets=set_targets,
        approve_exact=approve_exact,
        approve_suggested=approve_suggested,
        kind=kind,
        catalog=catalog,
    )
    if interactive:
        changes += run_interactive(result, catalog=catalog)
    save_document(result, output_path)
    return result, changes
