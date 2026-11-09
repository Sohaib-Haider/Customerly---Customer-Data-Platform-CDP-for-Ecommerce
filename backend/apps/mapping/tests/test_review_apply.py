"""Review flow (approve/correct/reject) and apply gate (approved-only)."""

from __future__ import annotations

import json

import pytest

from apps.mapping.apply import apply_approved
from apps.mapping.mapper import map_csv
from apps.mapping.models import (
    METHOD_MANUAL,
    REQUIRES_RELATIONSHIP,
    STATUS_APPROVED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    STATUS_SUGGESTED,
)
from apps.mapping.review import (
    ReviewError,
    apply_decisions,
    load_document,
    review_file,
    run_interactive,
    save_document,
)


def mapped(csv_path, answers=None, kind=None):
    """Map with a FakeLLM when answers are given, else without the LLM."""
    if answers is None:
        return map_csv(csv_path, use_llm=False)
    from conftest import FakeLLM

    return map_csv(csv_path, llm_client=FakeLLM(answers), kind_override=kind)


# --------------------------------------------------------------------------
# approve
# --------------------------------------------------------------------------
def test_approve_flow_and_apply_only_approved(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["quantity", "complain", "zzz_internal_flag"])
    result = map_csv(path, use_llm=False)
    save_document(result, tmp_path / "doc.json")

    reviewed, changes = review_file(
        tmp_path / "doc.json",
        tmp_path / "doc.json",
        approve=["quantity"],
    )
    assert reviewed.mapping_for("quantity").status == STATUS_APPROVED
    assert reviewed.mapping_for("complain").status == STATUS_SUGGESTED  # untouched
    assert any("approved quantity" in c for c in changes)

    report = apply_approved(tmp_path / "doc.json", path, tmp_path / "out")
    assert [a.source_column for a in report.applied] == ["quantity"]
    skipped = {s.source_column: s.reason for s in report.skipped}
    assert skipped["complain"] == "not_approved (status=suggested)"
    assert skipped["zzz_internal_flag"].startswith("no_target")
    # output contains only the approved rename
    out_csv = (tmp_path / "out" / "order_items.csv").read_text(encoding="utf-8")
    assert out_csv.splitlines()[0] == "quantity"
    assert len(out_csv.splitlines()) == 4  # header + 3 rows
    report_json = json.loads((tmp_path / "out" / "apply_report.json").read_text("utf-8"))
    assert report_json["schema"] == "column_mapping_apply/v1"


def test_approve_exact_and_suggested_bulk(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["quantity", "complain", "TENURE"])
    result = map_csv(path, use_llm=False)
    apply_decisions(result, approve_exact=True)
    assert all(m.status == STATUS_APPROVED for m in result.mappings)

    result2 = map_csv(path, use_llm=False)
    apply_decisions(result2, approve_suggested=True)
    assert all(m.status == STATUS_APPROVED for m in result2.mappings)


def test_approve_without_target_is_rejected(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["zzz_internal_flag"])
    result = map_csv(path, use_llm=False)
    with pytest.raises(ReviewError, match="no target"):
        apply_decisions(result, approve=["zzz_internal_flag"])


def test_unknown_column_in_review_is_rejected(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["quantity"])
    result = map_csv(path, use_llm=False)
    with pytest.raises(ReviewError, match="unknown source column"):
        apply_decisions(result, approve=["not_a_column"])


# --------------------------------------------------------------------------
# correct
# --------------------------------------------------------------------------
def test_manual_correction_sets_method_and_validates(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["hh_id"])
    result = map_csv(path, use_llm=False)  # unresolved -> needs_review
    apply_decisions(result, set_targets=[("hh_id", "customers", "source_customer_id")])
    mapping = result.mapping_for("hh_id")
    assert mapping.status == STATUS_APPROVED
    assert mapping.method == METHOD_MANUAL
    assert mapping.confidence == 1.0  # human-verified, not model confidence
    assert "method=none" in mapping.corrected_from


def test_manual_correction_rejects_invented_target(catalog, csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["hh_id"])
    result = map_csv(path, use_llm=False, catalog=catalog)
    with pytest.raises(ReviewError, match="not an upload-mappable canonical field"):
        apply_decisions(result, set_targets=[("hh_id", "customers", "made_up_col")])
    with pytest.raises(ReviewError, match="not an upload-mappable canonical field"):
        apply_decisions(result, set_targets=[("hh_id", "made_up_table", "id")])
    # nothing changed
    assert result.mapping_for("hh_id").has_target is False


def test_corrections_and_approvals_survive_roundtrip(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["hh_id", "quantity"])
    result = map_csv(path, use_llm=False)
    apply_decisions(
        result,
        set_targets=[("hh_id", "customers", "source_customer_id")],
        approve=["quantity"],
    )
    doc = tmp_path / "doc.json"
    save_document(result, doc)
    restored = load_document(doc)
    assert restored.mapping_for("hh_id").status == STATUS_APPROVED
    assert restored.mapping_for("quantity").status == STATUS_APPROVED
    assert restored.mapping_for("hh_id").method == METHOD_MANUAL


# --------------------------------------------------------------------------
# reject
# --------------------------------------------------------------------------
def test_reject_flow(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["quantity"])
    result = map_csv(path, use_llm=False)
    apply_decisions(result, reject=["quantity"])
    assert result.mapping_for("quantity").status == STATUS_REJECTED

    doc = tmp_path / "doc.json"
    save_document(result, doc)
    report = apply_approved(doc, path, tmp_path / "out")
    assert report.applied == []
    assert report.skipped[0].reason == "rejected_by_reviewer"


# --------------------------------------------------------------------------
# relationship / calculation separation at apply time
# --------------------------------------------------------------------------
def test_relationship_mapping_never_applied(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["user_id", "user_session", "event_time"])
    result = map_csv(path, use_llm=False)
    apply_decisions(result, approve=["user_id"])  # documented FK target
    doc = tmp_path / "doc.json"
    save_document(result, doc)

    report = apply_approved(doc, path, tmp_path / "out")
    assert report.applied == []
    skipped = {s.source_column: s.reason for s in report.skipped}
    assert "relationship_resolution" in skipped["user_id"]
    assert not (tmp_path / "out" / "events.csv").exists()


def test_calculation_alternate_is_never_applied(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["DAY"])
    result = map_csv(path, use_llm=False)
    day = result.mapping_for("DAY")
    assert day.alternate_targets  # includes calculated orders.order_date
    apply_decisions(result, approve=["DAY"])
    doc = tmp_path / "doc.json"
    save_document(result, doc)

    report = apply_approved(doc, path, tmp_path / "out")
    # only the direct primary lands; the calculated alternate stays in JSON
    assert [(a.target_table, a.target_column) for a in report.applied] == [
        ("orders", "day_no")
    ]
    out_csv = (tmp_path / "out" / "orders.csv").read_text(encoding="utf-8")
    assert out_csv.splitlines()[0] == "day_no"


def test_apply_flags_conflicting_targets(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["quantity", "units_sold"])
    from conftest import FakeLLM

    fake = FakeLLM(
        {"units_sold": ("order_items", "quantity", 0.85, "quantity sold")}
    )
    result = map_csv(path, llm_client=fake)
    apply_decisions(result, approve=["quantity", "units_sold"])
    doc = tmp_path / "doc.json"
    save_document(result, doc)

    report = apply_approved(doc, path, tmp_path / "out")
    assert [a.source_column for a in report.applied] == ["quantity"]  # first/stronger wins
    assert any("units_sold" in c for c in report.conflicts)


def test_apply_refuses_mismatched_csv(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["quantity"])
    other = csv_factory(tmp_path, ["something_else"], prefix="other")
    result = map_csv(path, use_llm=False)
    apply_decisions(result, approve=["quantity"])
    doc = tmp_path / "doc.json"
    save_document(result, doc)
    with pytest.raises(ValueError, match="does not contain the mapped columns"):
        apply_approved(doc, other, tmp_path / "out")


# --------------------------------------------------------------------------
# interactive review (scripted stdin)
# --------------------------------------------------------------------------
def test_interactive_review_scripted(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["quantity", "complain"])
    result = map_csv(path, use_llm=False)
    answers = iter(["a", "r"])  # approve quantity, reject complain
    printed: list[str] = []
    changes = run_interactive(
        result, input_fn=lambda _prompt: next(answers), print_fn=printed.append
    )
    assert result.mapping_for("quantity").status == STATUS_APPROVED
    assert result.mapping_for("complain").status == STATUS_REJECTED
    assert len(changes) == 2


def test_review_file_records_changes_in_document(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["date", "holiday"])
    result = map_csv(path, use_llm=False)  # kind=holidays -> both resolvable
    doc = tmp_path / "doc.json"
    save_document(result, doc)
    reviewed, _ = review_file(doc, doc, approve_exact=True, kind="holidays")
    doc_data = json.loads(doc.read_text("utf-8"))
    assert doc_data["source_profile"]["review"]["changes"]
    assert doc_data["source_profile"]["kind_detection"]["override_kind"] == "holidays"
    assert reviewed.mapping_for("date").status == STATUS_APPROVED
