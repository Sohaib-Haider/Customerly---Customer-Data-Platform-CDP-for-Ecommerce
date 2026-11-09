"""The mapping orchestrator: profile -> detect kind -> exact/alias -> LLM.

Pipeline for one CSV (headers + safe local metadata only ever leave here):

1. ``profile_csv``     — local column metadata (dtype/null/distinct/shapes).
2. ``detect_kinds``    — reviewable source-entity kind (multi-entity aware).
3. exact/alias match   — deterministic resolution seeded from the documented
                         mappings in :mod:`apps.mapping.aliases` and the
                         canonical column names of the parsed schema.
4. Gemini              — ONLY the unresolved/ambiguous remainder goes to the
                         model, validated against the same mappable gate.
5. status assignment   — method (exact|alias|llm|manual|none) and status
                         (suggested|needs_review|...|unmapped) kept strictly
                         separate; confidence never implies approval.
6. collision detection — two source columns proposing one target: exact/alias
                         then highest confidence wins; the loser is flagged.

Failure behaviour: missing key, SDK absence, API errors, rate limits and
invalid model JSON never raise out of :func:`map_csv` — the LLM stage degrades
to ``needs_review`` rows carrying the (scrubbed) reason, while exact/alias
results are preserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import aliases as alias_mod
from .config import (
    LLM_CONFIDENCE_HIGH,
    LLM_CONFIDENCE_MIN,
    MAX_COLUMNS_PER_PROMPT,
    scrub,
)
from .detect import KINDS, KindDetection, detect_kinds
from .llm import LLMClient, LLMError, LLMSuggestion, PendingColumn
from .models import (
    METHOD_ALIAS,
    METHOD_EXACT,
    METHOD_LLM,
    STATUS_NEEDS_REVIEW,
    STATUS_SUGGESTED,
    STATUS_UNMAPPED,
    AlternateTarget,
    Candidate,
    ColumnMapping,
    MappingResult,
)
from .profile import profile_csv
from .schema_catalog import MappableField, SchemaCatalog, get_catalog

_METHOD_RANK = {METHOD_EXACT: 0, METHOD_ALIAS: 1, METHOD_LLM: 3}


class _Cand:
    """Internal competing candidate before it becomes a public Candidate."""

    __slots__ = ("table", "column", "method", "requires", "decision_note", "entry", "field", "citation")

    def __init__(
        self,
        table: str,
        column: str,
        method: str,
        requires: tuple[str, ...] = (),
        decision_note: str | None = None,
        entry: alias_mod.AliasTarget | None = None,
        field: MappableField | None = None,
        citation: str = "",
    ) -> None:
        self.table = table
        self.column = column
        self.method = method
        self.requires = requires
        self.decision_note = decision_note
        self.entry = entry
        self.field = field
        self.citation = citation

    @property
    def qualified(self) -> tuple[str, str]:
        return (self.table, self.column)

    def to_candidate(self) -> Candidate:
        return Candidate(
            target_table=self.table,
            target_column=self.column,
            method=self.method,
            citation=self.citation,
        )


def map_csv(
    csv_path: str | Path,
    *,
    catalog: SchemaCatalog | None = None,
    use_llm: bool = True,
    llm_client: LLMClient | None = None,
    model: str | None = None,
    kind_override: str | None = None,
) -> MappingResult:
    """Map one CSV's columns onto the canonical schema.

    Never raises for LLM problems — inspect ``result.llm_info["error"]`` and
    per-row ``review_note`` values instead.
    """
    catalog = catalog or get_catalog()
    profile = profile_csv(csv_path)
    detection = detect_kinds(profile.columns)

    if kind_override is not None:
        if kind_override not in KINDS:
            raise ValueError(f"unknown kind {kind_override!r}; expected one of {', '.join(KINDS)}")
        detection.override = kind_override
    kind = detection.effective_kind

    prompt_metadata = {
        item["name"]: item for item in profile.to_prompt_metadata()
    }

    mappings: list[ColumnMapping] = []
    pending: list[PendingColumn] = []

    for column in profile.columns:
        resolved, pending_payload = _resolve_deterministic(
            column, kind, catalog, accepted_kinds=detection.tied_kinds
        )
        if resolved is not None:
            mappings.append(resolved)
        else:
            pending_payload.metadata = prompt_metadata.get(
                column, {"name": column, "inferred_dtype": "unknown"}
            )
            pending.append(pending_payload)

    llm_info: dict[str, Any] = {
        "model": None,
        "used": False,
        "columns_sent": 0,
        "invalid_discarded": 0,
        "dataset_kind_opinion": None,
        "error": None,
    }

    detection.resolved_context = {
        m.source_column: f"{m.target_table}.{m.target_column}"
        for m in mappings
        if m.target_table and m.target_column
    }

    if pending:
        _run_llm_stage(
            pending,
            mappings=mappings,
            catalog=catalog,
            detection=detection,
            use_llm=use_llm,
            llm_client=llm_client,
            model=model,
            llm_info=llm_info,
        )

    _flag_collisions(mappings)

    source_profile = {
        "kind_detection": detection.to_dict(),
        "column_profiles": [p.to_dict() for p in profile.column_profiles.values()],
    }
    return MappingResult(
        source_file=Path(csv_path).name,
        row_count=profile.row_count,
        source_columns=list(profile.columns),
        source_profile=source_profile,
        llm_info=llm_info,
        mappings=mappings,
    )


# ---------------------------------------------------------------------------
# Stage 3: deterministic resolution (exact name match + documented aliases)
# ---------------------------------------------------------------------------
# File kinds that own dedicated tables. In these files a column whose only
# candidates belong to other tables is sent to the LLM rather than forced.
_SPECIALISED_KINDS = frozenset({"messages", "events", "campaigns", "coupons", "holidays"})


def _resolve_deterministic(
    source_column: str,
    kind: str,
    catalog: SchemaCatalog,
    accepted_kinds: frozenset[str] | None = None,
) -> tuple[ColumnMapping | None, PendingColumn]:
    """Return (resolved mapping | None, pending payload).

    When the mapping is None the column must go to the LLM stage; the payload
    then carries any pre-validated candidates (deterministic ambiguity).

    ``accepted_kinds`` holds every kind tied for the top detection score. A
    candidate survives when it fits ANY of them, so a tied (mixed) file never
    has its kind decided by dictionary order; competing survivors go to the
    LLM as validated candidates instead.
    """
    accepted_kinds = accepted_kinds or frozenset({kind})
    norm = alias_mod.normalize(source_column)
    cands: dict[tuple[str, str], _Cand] = {}

    for field_ in catalog.exact_matches(norm):
        cands[(field_.table, field_.column)] = _Cand(
            table=field_.table,
            column=field_.column,
            method=METHOD_EXACT,
            requires=field_.requires,
            field=field_,
            citation=f"canonical column name equals source ({field_.provenance})",
        )

    for entry in alias_mod.lookup(source_column):
        key = (entry.table, entry.column)
        existing = cands.get(key)
        if existing is not None:
            # Same target found by BOTH rules: keep the `exact` label but adopt
            # the documented alias metadata, so a decision note / calculation
            # requirement is never lost to a normalized-name collision
            # (e.g. a source column literally named 'coupon_discount').
            if existing.method == METHOD_EXACT and existing.entry is None:
                existing.entry = entry
                existing.requires = tuple(dict.fromkeys([*existing.requires, *entry.requires]))
                existing.decision_note = existing.decision_note or entry.decision_note
            continue
        cands[key] = _Cand(
            table=entry.table,
            column=entry.column,
            method=METHOD_ALIAS,
            requires=entry.requires,
            decision_note=entry.decision_note,
            entry=entry,
            citation=entry.citation,
        )

    if not cands:
        return None, PendingColumn(source_column=source_column, metadata={})

    # Fold: a candidate another candidate's alias entry declares as an
    # ALTERNATE is not a competitor — it is the documented case of one source
    # column feeding a second table (e.g. customer_id -> customers roster AND
    # coupon_redemptions.customer_id), persisted via the primary + alternates.
    original = dict(cands)
    owned_alternates: set[tuple[str, str]] = set()
    for cand in cands.values():
        if cand.entry is not None:
            owned_alternates.update((a.table, a.column) for a in cand.entry.alternates)
    folded = {
        key: cand
        for key, cand in cands.items()
        if key not in owned_alternates or cand.entry is not None
    }
    if folded:
        cands = folded

    def kind_ok(cand: _Cand) -> bool:
        entry_kinds = cand.entry.kinds if cand.entry is not None else None
        return any(alias_mod.kind_matches(k, cand.table, entry_kinds) for k in accepted_kinds)

    surviving = [cand for cand in cands.values() if kind_ok(cand)]

    if len(surviving) == 1:
        return _build_resolved(source_column, surviving[0], kind), PendingColumn(source_column, {})
    if len(surviving) == 0:
        surviving = [cand for cand in original.values() if kind_ok(cand)]
    if len(surviving) == 1:
        return _build_resolved(source_column, surviving[0], kind), PendingColumn(source_column, {})
    if len(surviving) > 1:
        # Bare name-equality can collide across tables (e.g. week_no exists in
        # both orders and causal_data). When exactly ONE survivor is backed by
        # a documented alias entry, that documented evidence wins.
        documented = [cand for cand in surviving if cand.entry is not None]
        if len(documented) == 1:
            surviving = documented
    if len(surviving) == 1:
        return _build_resolved(source_column, surviving[0], kind), PendingColumn(source_column, {})
    if not surviving and accepted_kinds & _SPECIALISED_KINDS:
        # Every candidate belongs to a table that does not fit this file's
        # specialised kind (e.g. `category` in a messages file only names
        # products.category). Do not force it: hand the rejected options to
        # the LLM, which sees the whole file and may also answer "no target".
        # Retail kinds (customers/orders/products/transactions) routinely mix
        # entities in one file, so they keep the honest-ambiguity fallback.
        return None, PendingColumn(
            source_column=source_column,
            metadata={},
            candidates=[c.to_candidate().to_dict() for c in original.values()],
        )
    if not surviving:
        surviving = list(original.values())  # filter too strict -> honest ambiguity
    if len(surviving) > 1:
        return None, PendingColumn(
            source_column=source_column,
            metadata={},
            candidates=[c.to_candidate().to_dict() for c in surviving],
        )
    return _build_resolved(source_column, surviving[0], kind), PendingColumn(source_column, {})


def _build_resolved(
    source_column: str, cand: _Cand, kind: str
) -> ColumnMapping:
    entry = cand.entry
    requires = list(cand.requires)
    alternates: list[AlternateTarget] = []
    if entry is not None:
        for alt in entry.alternates:
            if kind_applicable(kind, alt.kinds):
                alternates.append(
                    AlternateTarget(
                        target_table=alt.table,
                        target_column=alt.column,
                        requires=list(alt.requires),
                        citation=alt.citation,
                    )
                )

    if cand.method == METHOD_EXACT:
        field_ = cand.field
        assert field_ is not None
        explanation = (
            f"Exact match: source name equals canonical column "
            f"{field_.qualified} ({field_.provenance}, {field_.sql_type})."
        )
        if field_.kind == "relationship":
            explanation += f" {field_.citation}."
    else:
        explanation = f"Documented alias: {cand.citation}."

    # Status rules (method/status strictly separate from approval):
    #   decision entanglement or calculation params -> always needs_review
    #   plain exact/alias (incl. relationship resolution) -> suggested
    status = STATUS_SUGGESTED
    if cand.decision_note:
        status = STATUS_NEEDS_REVIEW
        explanation += " See decision_note."
    elif "calculation" in requires:
        status = STATUS_NEEDS_REVIEW
        explanation += " Requires calculation — needs review."

    return ColumnMapping(
        source_column=source_column,
        target_table=cand.table,
        target_column=cand.column,
        method=cand.method,
        status=status,
        confidence=None,  # confidence is an LLM concept only
        explanation=explanation,
        requires=requires,
        decision_note=cand.decision_note,
        alternate_targets=alternates,
        candidates=[],
    )


def kind_applicable(kind: str, entry_kinds: frozenset[str] | None) -> bool:
    """Alternate-target scoping: unknown kind keeps everything (informational)."""
    if entry_kinds is None or kind == "unknown":
        return True
    return kind in entry_kinds


# ---------------------------------------------------------------------------
# Stage 4: LLM for the remainder
# ---------------------------------------------------------------------------
def _run_llm_stage(
    pending: list[PendingColumn],
    *,
    mappings: list[ColumnMapping],
    catalog: SchemaCatalog,
    detection: KindDetection,
    use_llm: bool,
    llm_client: LLMClient | None,
    model: str | None,
    llm_info: dict[str, Any],
) -> None:
    client: LLMClient | None = llm_client
    stage_error: str | None = None

    if client is None and use_llm:
        try:
            from .llm import make_client

            client = make_client(model=model)
        except LLMError as exc:
            stage_error = f"llm_unavailable: {scrub(str(exc))}"

    if client is None and stage_error is None:
        stage_error = "llm_skipped: LLM stage disabled (--no-llm)"

    suggestions: dict[str, LLMSuggestion] = {}

    if stage_error is None:
        assert client is not None
        llm_info["model"] = getattr(client, "model", model)
        llm_info["used"] = True
        for start in range(0, len(pending), MAX_COLUMNS_PER_PROMPT):
            chunk = pending[start : start + MAX_COLUMNS_PER_PROMPT]
            try:
                outcome = client.suggest(chunk, catalog, detection)
            except LLMError as exc:
                stage_error = f"llm_error: {scrub(str(exc))}"
                break
            for suggestion in outcome.suggestions:
                suggestions[suggestion.source_column] = suggestion
            if outcome.dataset_kind:
                llm_info["dataset_kind_opinion"] = outcome.dataset_kind
                if detection.override is None and outcome.dataset_kind != detection.primary:
                    detection.review_required = True
            llm_info["columns_sent"] += len(chunk)

    if stage_error is not None:
        llm_info["error"] = stage_error

    for pending_column in pending:
        if pending_column.source_column in suggestions:
            mappings.append(
                _mapping_from_suggestion(
                    pending_column,
                    suggestions[pending_column.source_column],
                    catalog=catalog,
                    detection=detection,
                    llm_info=llm_info,
                )
            )
        elif stage_error is not None:
            # LLM could not answer — human decision required, never invented.
            mappings.append(
                ColumnMapping(
                    source_column=pending_column.source_column,
                    method="none",
                    status=STATUS_NEEDS_REVIEW,
                    confidence=None,
                    explanation=(
                        "Not resolved automatically (LLM stage unavailable). "
                        "Set a target manually in review or re-run with the API key."
                    ),
                    candidates=[Candidate.from_dict(c) for c in pending_column.candidates],
                    review_note=stage_error,
                )
            )
        else:
            # LLM ran but did not answer for this column.
            mappings.append(
                _mapping_from_suggestion(
                    pending_column, None, catalog=catalog, detection=detection, llm_info=llm_info
                )
            )


def _mapping_from_suggestion(
    pending_column: PendingColumn,
    suggestion: LLMSuggestion | None,
    *,
    catalog: SchemaCatalog,
    detection: KindDetection,
    llm_info: dict[str, Any],
) -> ColumnMapping:
    candidates = [Candidate.from_dict(c) for c in pending_column.candidates]

    # --- no / null answer -------------------------------------------------
    if suggestion is None or (not suggestion.target_table and not suggestion.target_column):
        explanation = (
            suggestion.explanation
            if suggestion is not None and suggestion.explanation
            else "No canonical target found for this column."
        )
        if candidates:
            return ColumnMapping(
                source_column=pending_column.source_column,
                method="none",
                status=STATUS_NEEDS_REVIEW,
                confidence=None,
                explanation=explanation,
                candidates=candidates,
                review_note="ambiguous: multiple documented targets — pick one in review",
            )
        return ColumnMapping(
            source_column=pending_column.source_column,
            method="none",
            status=STATUS_UNMAPPED,
            confidence=None,
            explanation=explanation + " Left unmapped — no target invented.",
        )

    # --- answer that must pass the schema gate ---------------------------
    field_ = catalog.validate(suggestion.target_table, suggestion.target_column)
    if field_ is None:
        llm_info["invalid_discarded"] = llm_info.get("invalid_discarded", 0) + 1
        return ColumnMapping(
            source_column=pending_column.source_column,
            method="none",
            status=STATUS_UNMAPPED,
            confidence=None,
            explanation=suggestion.explanation or "Discarded non-canonical suggestion.",
            review_note=(
                "invalid_target_discarded: Gemini proposed "
                f"{suggestion.target_table}.{suggestion.target_column}, which is not an "
                "upload-mappable canonical field"
            ),
        )

    confidence = max(0.0, min(1.0, float(suggestion.confidence)))
    if confidence < LLM_CONFIDENCE_MIN:
        return ColumnMapping(
            source_column=pending_column.source_column,
            target_table=field_.table,
            target_column=field_.column,
            method=METHOD_LLM,
            status=STATUS_UNMAPPED,
            confidence=round(confidence, 2),
            explanation=suggestion.explanation
            or "Confidence below the accept threshold — not proposed.",
            review_note=f"low_confidence: {confidence:.2f} < {LLM_CONFIDENCE_MIN:.2f}",
        )

    # Adopt documented context when the model lands on an alias target.
    requires = list(field_.requires)
    decision_note: str | None = None
    alternates: list[AlternateTarget] = []
    entry = alias_mod.alias_index_by_target().get((field_.table, field_.column))
    if entry is not None:
        requires = list(dict.fromkeys([*requires, *entry.requires]))
        decision_note = entry.decision_note
        for alt in entry.alternates:
            if kind_applicable(detection.effective_kind, alt.kinds):
                alternates.append(
                    AlternateTarget(
                        target_table=alt.table,
                        target_column=alt.column,
                        requires=list(alt.requires),
                        citation=alt.citation,
                    )
                )

    explanation = (
        suggestion.explanation
        or f"Gemini mapped {pending_column.source_column} to {field_.qualified}."
    )
    if entry is not None:
        explanation += f" Documented: {entry.citation}."

    was_ambiguous = bool(candidates)
    if decision_note or "calculation" in requires or was_ambiguous:
        status = STATUS_NEEDS_REVIEW
    else:
        status = STATUS_SUGGESTED if confidence >= LLM_CONFIDENCE_HIGH else STATUS_NEEDS_REVIEW

    return ColumnMapping(
        source_column=pending_column.source_column,
        target_table=field_.table,
        target_column=field_.column,
        method=METHOD_LLM,
        status=status,
        confidence=round(confidence, 2),
        explanation=explanation,
        requires=requires,
        decision_note=decision_note,
        alternate_targets=alternates,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Stage 6: two source columns wanting one target
# ---------------------------------------------------------------------------
def _flag_collisions(mappings: list[ColumnMapping]) -> None:
    by_target: dict[tuple[str, str], list[ColumnMapping]] = {}
    for mapping in mappings:
        if mapping.has_target and mapping.status != STATUS_UNMAPPED:
            by_target.setdefault((mapping.target_table, mapping.target_column), []).append(mapping)

    for group in by_target.values():
        if len(group) < 2:
            continue
        winner = max(
            group,
            key=lambda m: (_METHOD_RANK.get(m.method, 9), -(m.confidence or 0.0)),
        )
        for mapping in group:
            if mapping is winner:
                continue
            mapping.status = STATUS_NEEDS_REVIEW
            mapping.review_note = (
                f"target_collision: {winner.target_table}.{winner.target_column} is also "
                f"proposed for '{winner.source_column}' — keep one in review"
            )
