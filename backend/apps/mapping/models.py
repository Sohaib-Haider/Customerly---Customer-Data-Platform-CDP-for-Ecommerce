"""Data model for mapping proposals: method vs. status vs. approval.

Two orthogonal concepts, deliberately kept apart (refinement of the plan):

``method``  — HOW a target was proposed:  exact | alias | llm | manual | none
``status``  — WHERE review stands:        suggested | needs_review |
                                          approved | rejected | unmapped

Confidence is Gemini's self-reported score for ``method=llm`` rows only and
NEVER implies approval: only an explicit human decision (this package's
``review`` command, a future API endpoint, ...) can move a mapping into
``approved``, and only ``approved`` rows are applied / persisted.

The JSON document shape is versioned via ``schema`` so a future ingestion
service can consume it. :meth:`MappingResult.to_ingestion_column_mappings_rows`
emits rows shaped exactly like the ``ingestion_column_mappings`` contract
(upload_id, source_column, target_table, target_column, confidence) — emitting
rows is not persisting them; no database connection exists in this component.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_DOC_VERSION = "column_mapping/v1"

# method
METHOD_EXACT = "exact"
METHOD_ALIAS = "alias"
METHOD_LLM = "llm"
METHOD_MANUAL = "manual"
METHOD_NONE = "none"

# status
STATUS_SUGGESTED = "suggested"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_UNMAPPED = "unmapped"

ALL_METHODS = (METHOD_EXACT, METHOD_ALIAS, METHOD_LLM, METHOD_MANUAL, METHOD_NONE)
ALL_STATUSES = (
    STATUS_SUGGESTED,
    STATUS_NEEDS_REVIEW,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_UNMAPPED,
)

# `requires` vocabulary
REQUIRES_RELATIONSHIP = "relationship_resolution"
REQUIRES_CALCULATION = "calculation"


@dataclass
class AlternateTarget:
    """A second canonical field the same source column feeds.

    Documented reality: ``DAY`` -> orders.day_no AND orders.order_date,
    ``PRODUCT_ID`` -> products.source_product_id AND order_items.product_id ...
    The DB contract is one row per (upload_id, source_column), so only the
    primary target can ever be persisted; alternates live in this JSON for
    the future ingestion service and for reviewers.
    """

    target_table: str
    target_column: str
    requires: list[str] = field(default_factory=list)
    citation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlternateTarget":
        return cls(
            target_table=data["target_table"],
            target_column=data["target_column"],
            requires=list(data.get("requires", [])),
            citation=data.get("citation", ""),
        )


@dataclass
class Candidate:
    """A possible target shown when a source column is ambiguous."""

    target_table: str
    target_column: str
    method: str  # how THIS candidate would be reached (exact/alias)
    citation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        return cls(
            target_table=data["target_table"],
            target_column=data["target_column"],
            method=data.get("method", METHOD_NONE),
            citation=data.get("citation", ""),
        )


@dataclass
class ColumnMapping:
    source_column: str
    target_table: str | None = None
    target_column: str | None = None
    method: str = METHOD_NONE
    status: str = STATUS_NEEDS_REVIEW
    confidence: float | None = None  # llm only; None for exact/alias/manual
    explanation: str = ""
    requires: list[str] = field(default_factory=list)
    decision_note: str | None = None  # documented open-decision entanglement
    alternate_targets: list[AlternateTarget] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    review_note: str | None = None  # e.g. collision, llm_unavailable
    corrected_from: str | None = None  # set by review on manual correction

    @property
    def has_target(self) -> bool:
        return bool(self.target_table and self.target_column)

    @property
    def is_direct(self) -> bool:
        """True when nothing but renaming is needed to apply this mapping."""
        return self.has_target and not self.requires

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_column": self.source_column,
            "target_table": self.target_table,
            "target_column": self.target_column,
            "method": self.method,
            "status": self.status,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "requires": list(self.requires),
            "decision_note": self.decision_note,
            "alternate_targets": [a.to_dict() for a in self.alternate_targets],
            "candidates": [c.to_dict() for c in self.candidates],
            "review_note": self.review_note,
            "corrected_from": self.corrected_from,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ColumnMapping":
        return cls(
            source_column=data["source_column"],
            target_table=data.get("target_table"),
            target_column=data.get("target_column"),
            method=data.get("method", METHOD_NONE),
            status=data.get("status", STATUS_NEEDS_REVIEW),
            confidence=data.get("confidence"),
            explanation=data.get("explanation", ""),
            requires=list(data.get("requires", [])),
            decision_note=data.get("decision_note"),
            alternate_targets=[
                AlternateTarget.from_dict(a) for a in data.get("alternate_targets", [])
            ],
            candidates=[Candidate.from_dict(c) for c in data.get("candidates", [])],
            review_note=data.get("review_note"),
            corrected_from=data.get("corrected_from"),
        )


@dataclass
class MappingResult:
    source_file: str
    row_count: int | None
    source_columns: list[str]
    source_profile: dict[str, Any]  # kind detection, reviewable/overridable
    llm_info: dict[str, Any]  # model, used, error (scrubbed), discarded count
    mappings: list[ColumnMapping]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def mapping_for(self, source_column: str) -> ColumnMapping | None:
        for mapping in self.mappings:
            if mapping.source_column == source_column:
                return mapping
        return None

    def summary(self) -> dict[str, Any]:
        by_status = {status: 0 for status in ALL_STATUSES}
        by_method = {method: 0 for method in ALL_METHODS}
        for mapping in self.mappings:
            by_status[mapping.status] = by_status.get(mapping.status, 0) + 1
            by_method[mapping.method] = by_method.get(mapping.method, 0) + 1
        return {
            "columns": len(self.mappings),
            "by_status": by_status,
            "by_method": by_method,
            "direct_applicable": sum(
                1 for m in self.mappings if m.status == STATUS_APPROVED and m.is_direct
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_DOC_VERSION,
            "generated_at": self.generated_at,
            "source": {
                "file_name": self.source_file,
                "row_count": self.row_count,
                "columns": list(self.source_columns),
            },
            "source_profile": self.source_profile,
            "llm": self.llm_info,
            "mappings": [m.to_dict() for m in self.mappings],
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MappingResult":
        if data.get("schema") != SCHEMA_DOC_VERSION:
            raise ValueError(
                f"unsupported mapping document version {data.get('schema')!r} "
                f"(expected {SCHEMA_DOC_VERSION!r})"
            )
        source = data.get("source", {})
        return cls(
            source_file=source.get("file_name", ""),
            row_count=source.get("row_count"),
            source_columns=list(source.get("columns", [])),
            source_profile=data.get("source_profile", {}),
            llm_info=data.get("llm", {}),
            mappings=[ColumnMapping.from_dict(m) for m in data.get("mappings", [])],
            generated_at=data.get("generated_at", ""),
        )

    # ------------------------------------------------------------------
    # Database contract emitter (design-only; nothing is persisted here).
    # ------------------------------------------------------------------
    def to_ingestion_column_mappings_rows(
        self, upload_id: str
    ) -> list[dict[str, Any]]:
        """Rows shaped exactly like ``ingestion_column_mappings``.

        Contract (backend/schema.sql §4): upload_id UUID, source_column
        VARCHAR(255) NOT NULL, target_table VARCHAR(100) NOT NULL,
        target_column VARCHAR(100) NOT NULL, confidence NUMERIC(3,2).

        Only rows that HAVE a persistable target are emitted — an unmapped
        source column has no canonical column to record (and
        ``(upload_id, source_column)`` is UNIQUE, so a row could not be
        corrected later without an UPDATE). ``rejected`` rows are excluded
        too: a human explicitly vetoed that mapping.

        No persistence happens in this component: there is no store_id /
        upload lifecycle yet. A future ingestion service should persist the
        rows of *approved* mappings once ``ingestion_uploads`` rows exist;
        ``status``/``method``/``explanation`` are intentionally NOT part of
        this shape because the canonical table has no columns for them —
        they remain in the JSON document as the review audit trail.
        """
        rows: list[dict[str, Any]] = []
        for mapping in self.mappings:
            if not mapping.has_target:
                continue
            if mapping.status in (STATUS_UNMAPPED, STATUS_REJECTED):
                continue
            confidence = mapping.confidence
            rows.append(
                {
                    "upload_id": upload_id,
                    "source_column": mapping.source_column,
                    "target_table": mapping.target_table,
                    "target_column": mapping.target_column,
                    "confidence": None if confidence is None else round(float(confidence), 2),
                }
            )
        return rows
