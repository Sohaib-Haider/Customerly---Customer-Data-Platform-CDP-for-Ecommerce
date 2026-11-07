"""Apply ONLY explicitly approved direct mappings to a CSV.

Behaviour (deliberately conservative):

* A mapping is applied iff status == ``approved`` AND ``requires`` is empty
  (relationship-resolution keys and calculated fields are kept separate and
  skipped with a reason — they belong to the offline transform/load stage).
* Output is one CSV per canonical target table, containing exactly the
  approved renamed columns and the original row values (string-for-string —
  no type casting, no id generation, no FK resolution: that is the transform
  stage's job, see data_prep/transform/).
* Unapproved rows are never applied, even at 0.99 confidence — approval is
  the only gate.
* Two approved source columns targeting the same canonical column: the first
  approved wins, the later one is reported as a conflict (never merged).
* An ``apply_report.json`` records every applied and skipped mapping with its
  reason, so the output is auditable.

No database writes happen anywhere in this component.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import STATUS_APPROVED, MappingResult
from .review import load_document


@dataclass
class AppliedRow:
    source_column: str
    target_table: str
    target_column: str
    method: str
    confidence: float | None


@dataclass
class SkippedRow:
    source_column: str
    status: str
    target_table: str | None
    target_column: str | None
    reason: str


@dataclass
class ApplyReport:
    input_csv: str
    output_dir: str
    mapping_document: str
    row_count: int
    applied: list[AppliedRow] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    applied_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema"] = "column_mapping_apply/v1"
        return data


def _skip_reason(mapping: Any) -> str:
    if not mapping.has_target:
        return "no_target (left unmapped)"
    if mapping.status == "rejected":
        return "rejected_by_reviewer"
    if mapping.status != STATUS_APPROVED:
        return f"not_approved (status={mapping.status})"
    if "relationship_resolution" in mapping.requires:
        return "requires relationship_resolution (handled by the load/transform stage)"
    if "calculation" in mapping.requires:
        return "requires calculation (handled by the transform stage)"
    if mapping.requires:
        return f"requires {', '.join(mapping.requires)}"
    return "not_approved"


def apply_approved(
    mapping_document: str | Path,
    input_csv: str | Path,
    output_dir: str | Path,
    *,
    mapping_result: MappingResult | None = None,
) -> ApplyReport:
    """Write per-table CSVs for approved direct mappings + a report.

    Raises ValueError when the input CSV's columns do not cover the mapping
    document's source columns (wrong pairing), so mismatched files fail loudly
    instead of producing partial silent output.
    """
    result = mapping_result or load_document(mapping_document)
    input_csv = Path(input_csv)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        source_columns = list(reader.fieldnames or [])
        rows = list(reader)

    missing = [c for c in result.source_columns if c not in source_columns]
    if missing:
        raise ValueError(
            f"{input_csv.name} does not contain the mapped columns: {', '.join(missing)} "
            f"— pass the same file that produced the mapping document"
        )

    report = ApplyReport(
        input_csv=str(input_csv),
        output_dir=str(output_dir),
        mapping_document=str(mapping_document),
        row_count=len(rows),
    )

    # group approved direct mappings by target table, first-wins on conflicts
    per_table: dict[str, list[Any]] = {}
    seen_targets: dict[tuple[str, str], str] = {}
    for mapping in result.mappings:
        # The single gate: explicitly approved AND nothing but a rename.
        if mapping.status != STATUS_APPROVED or not mapping.is_direct:
            report.skipped.append(
                SkippedRow(
                    source_column=mapping.source_column,
                    status=mapping.status,
                    target_table=mapping.target_table,
                    target_column=mapping.target_column,
                    reason=_skip_reason(mapping),
                )
            )
            continue
        key = (mapping.target_table, mapping.target_column)
        if key in seen_targets:
            report.conflicts.append(
                f"{mapping.source_column} -> {mapping.target_table}.{mapping.target_column} "
                f"skipped: canonical column already filled by '{seen_targets[key]}'"
            )
            report.skipped.append(
                SkippedRow(
                    source_column=mapping.source_column,
                    status=mapping.status,
                    target_table=mapping.target_table,
                    target_column=mapping.target_column,
                    reason="target_conflict",
                )
            )
            continue
        seen_targets[key] = mapping.source_column
        per_table.setdefault(mapping.target_table, []).append(mapping)
        report.applied.append(
            AppliedRow(
                source_column=mapping.source_column,
                target_table=mapping.target_table,
                target_column=mapping.target_column,
                method=mapping.method,
                confidence=mapping.confidence,
            )
        )

    for table, mappings in sorted(per_table.items()):
        out_path = output_dir / f"{table}.csv"
        mappings_sorted = sorted(mappings, key=lambda m: source_columns.index(m.source_column))
        fieldnames = [m.target_column for m in mappings_sorted]
        with out_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        m.target_column: (row.get(m.source_column) or "")
                        for m in mappings_sorted
                    }
                )
        report.output_files.append(str(out_path))

    report_path = output_dir / "apply_report.json"
    report_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report.output_files.append(str(report_path))
    return report
