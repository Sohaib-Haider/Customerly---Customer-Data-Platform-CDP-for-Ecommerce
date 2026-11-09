"""End-to-end CLI runs: verify / map --no-llm / review / apply (offline)."""

from __future__ import annotations

import json
from pathlib import Path

from apps.mapping.cli import main

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "store_upload_sample.csv"


def test_verify_reports_schema_tables(capsys):
    code = main(["verify"])
    out = capsys.readouterr().out
    assert "23" in out  # table count verified against the DDL
    assert "orders.region" in out  # the one documented open-provenance gap
    assert code == 1  # drift exists -> gated, not silently clean


def test_verify_show_targets(capsys):
    code = main(["verify", "--show-targets"])
    out = capsys.readouterr().out
    assert "customers.source_customer_id" in out
    assert code == 1


def test_map_no_llm_writes_document(tmp_path, capsys):
    out_doc = tmp_path / "sample.mapping.json"
    code = main(["map", str(SAMPLE), "-o", str(out_doc), "--no-llm"])
    assert code == 0
    assert out_doc.is_file()

    doc = json.loads(out_doc.read_text(encoding="utf-8"))
    assert doc["schema"] == "column_mapping/v1"
    assert doc["source"]["columns"][0] == "hh_id"
    assert doc["source_profile"]["kind_detection"]["effective_kind"] == "transactions"
    by_status = doc["summary"]["by_status"]
    assert by_status.get("suggested", 0) + by_status.get("needs_review", 0) >= 4
    # exact match on the canonical name is present without any LLM
    week = next(m for m in doc["mappings"] if m["source_column"] == "week_no")
    assert (week["target_table"], week["target_column"]) == ("orders", "week_no")
    assert week["method"] == "exact"
    # undocumented/unmappable columns stay unmapped — nothing invented
    flag = next(m for m in doc["mappings"] if m["source_column"] == "zzz_internal_flag")
    assert flag["status"] in ("needs_review", "unmapped")
    assert flag["target_table"] is None
    out = capsys.readouterr().out
    assert "llm: skipped" in out
    assert str(out_doc) in out


def test_map_to_stdout_has_no_secrets(tmp_path, capsys, monkeypatch):
    # even with a fake key in the environment, it must not reach stdout
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-assertion-not-a-real-key")
    code = main(["map", str(SAMPLE), "-o", "-", "--no-llm"])
    captured = capsys.readouterr()
    assert code == 0
    doc = json.loads(captured.out)
    assert doc["schema"] == "column_mapping/v1"
    assert "fake-key-for-assertion" not in captured.out
    assert "fake-key-for-assertion" not in captured.err


def test_full_review_apply_cycle(tmp_path, capsys):
    out_doc = tmp_path / "sample.mapping.json"
    assert main(["map", str(SAMPLE), "-o", str(out_doc), "--no-llm"]) == 0

    # approve the confident ones + a manual correction for an unknown one
    code = main([
        "review", str(out_doc),
        "--approve", "week_no,price",
        "--set", "hh_id=customers.source_customer_id",
        "--kind", "transactions",
    ])
    assert code == 0
    doc = json.loads(out_doc.read_text(encoding="utf-8"))
    approved = {
        m["source_column"]
        for m in doc["mappings"]
        if m["status"] == "approved"
    }
    assert {"week_no", "price", "hh_id"} <= approved
    hh = next(m for m in doc["mappings"] if m["source_column"] == "hh_id")
    assert hh["method"] == "manual"
    assert doc["source_profile"]["kind_detection"]["override_kind"] == "transactions"

    # apply approved-only
    out_dir = tmp_path / "out"
    code = main(["apply", str(out_doc), str(SAMPLE), "-o", str(out_dir)])
    assert code == 0
    orders = (out_dir / "orders.csv").read_text(encoding="utf-8").splitlines()
    assert orders[0] == "week_no"  # only the direct approved rename landed
    customers = (out_dir / "customers.csv").read_text(encoding="utf-8").splitlines()
    assert customers[0] == "source_customer_id"
    assert len(customers) == SAMPLE.read_text(encoding="utf-8").count("\n")
    report = json.loads((out_dir / "apply_report.json").read_text(encoding="utf-8"))
    assert len(report["applied"]) == 3
    assert {s["source_column"] for s in report["skipped"]} >= {"zzz_internal_flag", "loyalty_pts"}


def test_review_rejects_invented_target(tmp_path, capsys):
    out_doc = tmp_path / "d.json"
    assert main(["map", str(SAMPLE), "-o", str(out_doc), "--no-llm"]) == 0
    code = main(["review", str(out_doc), "--set", "hh_id=customers.not_a_column"])
    assert code == 2
    assert "not an upload-mappable canonical field" in capsys.readouterr().err


def test_map_missing_file(tmp_path, capsys):
    code = main(["map", str(tmp_path / "nope.csv"), "--no-llm"])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_apply_with_nothing_approved_still_succeeds(tmp_path, capsys):
    out_doc = tmp_path / "d.json"
    assert main(["map", str(SAMPLE), "-o", str(out_doc), "--no-llm"]) == 0
    out_dir = tmp_path / "out"
    code = main(["apply", str(out_doc), str(SAMPLE), "-o", str(out_dir)])
    assert code == 0
    assert "nothing applied" in capsys.readouterr().out
