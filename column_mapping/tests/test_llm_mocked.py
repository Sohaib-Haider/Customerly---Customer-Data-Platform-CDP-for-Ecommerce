"""Gemini stage, fully mocked: semantics, thresholds, error taxonomy.

FakeLLM (conftest) implements the same `suggest` contract as GeminiColumnClient,
so these tests exercise the real mapper logic around the model — validation,
status thresholds, ambiguity handling and every degradation path — with zero
network and zero credential use.
"""

from __future__ import annotations

import pytest

from column_mapping import config as config_mod
from column_mapping.llm import LLMBadResponse, LLMRateLimit, LLMUnavailable, build_prompt
from column_mapping.mapper import map_csv
from column_mapping.models import (
    METHOD_LLM,
    METHOD_NONE,
    STATUS_NEEDS_REVIEW,
    STATUS_SUGGESTED,
    STATUS_UNMAPPED,
)

# columns with no exact/alias match -> everything goes to the (fake) model
UNKNOWN_COLUMNS = ["hh_id", "ticket_no", "txn_day", "zzz_internal_flag"]


# --------------------------------------------------------------------------
# happy paths
# --------------------------------------------------------------------------
def test_llm_semantic_mapping_high_confidence(csv_factory, tmp_path, fake_llm_factory):
    path = csv_factory(tmp_path, UNKNOWN_COLUMNS)
    fake = fake_llm_factory(
        {
            "hh_id": ("customers", "source_customer_id", 0.93,
                      "household-style key with customer roster semantics"),
            "ticket_no": ("orders", "source_order_id", 0.90,
                          "per-basket ticket number identifies the order"),
            "txn_day": ("orders", "order_date", 0.72,
                        "day column of transaction dates, dtype date"),
        }
    )
    result = map_csv(path, llm_client=fake, kind_override="transactions")

    hh = result.mapping_for("hh_id")
    assert (hh.target_table, hh.target_column) == ("customers", "source_customer_id")
    assert hh.method == METHOD_LLM
    assert hh.status == STATUS_SUGGESTED  # 0.93 >= 0.85
    assert hh.confidence == 0.93

    day = result.mapping_for("txn_day")
    assert day.status == STATUS_NEEDS_REVIEW  # 0.72 < 0.85 -> review
    assert day.confidence == 0.72

    # the untouched column is NOT invented -> unmapped, no target
    flag = result.mapping_for("zzz_internal_flag")
    assert flag.status == STATUS_UNMAPPED
    assert flag.has_target is False

    assert result.llm_info["used"] is True
    assert result.llm_info["model"] == "fake-llm"
    assert result.llm_info["columns_sent"] == 4
    assert result.llm_info["error"] is None


def test_llm_landing_on_alias_target_adopts_documented_context(
    csv_factory, tmp_path, fake_llm_factory
):
    path = csv_factory(tmp_path, ["hh_id", "ticket_no", "store_no", "txn_day"])
    fake = fake_llm_factory(
        {
            "hh_id": ("customers", "source_customer_id", 0.92, "household key"),
            "ticket_no": ("orders", "source_order_id", 0.9, "order ticket"),
            "txn_day": ("orders", "order_date", 0.9, "transaction day"),
            # model lands on an alias target -> decision note #3 is adopted
            "store_no": ("orders", "source_store_id", 0.88, "store number"),
        }
    )
    result = map_csv(path, llm_client=fake, kind_override="transactions")

    store = result.mapping_for("store_no")
    assert store.status == STATUS_NEEDS_REVIEW  # decision note forces review
    assert "Open Decision #3" in store.decision_note
    assert any(
        a.target_table == "causal_data" and a.target_column == "source_store_id"
        for a in store.alternate_targets
    )
    # hh_id also picks up the documented orders.customer_id alternate
    hh = result.mapping_for("hh_id")
    assert any(
        a.target_table == "orders" and a.target_column == "customer_id"
        for a in hh.alternate_targets
    )


def test_llm_resolves_deterministic_ambiguity_but_needs_review(
    csv_factory, tmp_path, fake_llm_factory
):
    # 'price' under an unknown kind -> two documented candidates; the model
    # may pick, but review stays mandatory.
    path = csv_factory(tmp_path, ["price"])
    fake = fake_llm_factory(
        {"price": ("order_items", "unit_price", 0.8, "price of a purchased item")}
    )
    result = map_csv(path, llm_client=fake)
    mapping = result.mapping_for("price")
    assert (mapping.target_table, mapping.target_column) == ("order_items", "unit_price")
    assert mapping.method == METHOD_LLM
    assert mapping.status == STATUS_NEEDS_REVIEW  # resolved ambiguity != approval
    assert len(mapping.candidates) == 2


def test_llm_dataset_kind_opinion_is_recorded_not_obeyed(
    csv_factory, tmp_path, fake_llm_factory
):
    path = csv_factory(tmp_path, UNKNOWN_COLUMNS)
    fake = fake_llm_factory({}, dataset_kind="events")
    result = map_csv(path, llm_client=fake)
    assert result.llm_info["dataset_kind_opinion"] == "events"
    detection = result.source_profile["kind_detection"]
    # detector's own opinion survives; disagreement forces human review
    assert detection["primary_kind"] == "transactions"
    assert detection["review_required"] is True


# --------------------------------------------------------------------------
# rejections / degradation — none of these may raise out of map_csv
# --------------------------------------------------------------------------
def test_invalid_target_is_discarded(csv_factory, tmp_path, fake_llm_factory):
    path = csv_factory(tmp_path, ["hh_id"])
    fake = fake_llm_factory(
        {"hh_id": ("customers", "loyalty_points", 0.99, "invented column!")}
    )
    result = map_csv(path, llm_client=fake)
    mapping = result.mapping_for("hh_id")
    assert mapping.status == STATUS_UNMAPPED
    assert mapping.has_target is False
    assert "invalid_target_discarded" in mapping.review_note
    assert result.llm_info["invalid_discarded"] == 1


def test_low_confidence_below_floor_is_not_proposed(csv_factory, tmp_path, fake_llm_factory):
    path = csv_factory(tmp_path, ["mystery_col"])
    fake = fake_llm_factory(
        {"mystery_col": ("customers", "source_customer_id", 0.3, "pure guess")}
    )
    result = map_csv(path, llm_client=fake)
    mapping = result.mapping_for("mystery_col")
    assert mapping.status == STATUS_UNMAPPED
    assert "low_confidence" in mapping.review_note
    assert mapping.confidence == 0.3  # kept for the reviewer's context


def test_explicit_null_answer_stays_unmapped(csv_factory, tmp_path, fake_llm_factory):
    path = csv_factory(tmp_path, ["zzz_internal_flag"])
    fake = fake_llm_factory(
        {"zzz_internal_flag": (None, None, 0.1, "internal bookkeeping, not customer data")}
    )
    result = map_csv(path, llm_client=fake)
    mapping = result.mapping_for("zzz_internal_flag")
    assert mapping.status == STATUS_UNMAPPED
    assert mapping.has_target is False
    assert "not customer data" in mapping.explanation


def test_rate_limit_degrades_and_preserves_exact_results(
    csv_factory, tmp_path, fake_llm_factory
):
    path = csv_factory(tmp_path, ["quantity", "hh_id"])
    fake = fake_llm_factory({}, error=LLMRateLimit("Gemini rate limit (HTTP 429)"))
    result = map_csv(path, llm_client=fake)
    # deterministic result survives
    quantity = result.mapping_for("quantity")
    assert quantity.status == STATUS_SUGGESTED
    assert quantity.method == "exact"
    # the LLM column degrades to human review, never unmapped-guessed
    hh = result.mapping_for("hh_id")
    assert hh.status == STATUS_NEEDS_REVIEW
    assert "llm_error" in hh.review_note
    assert result.llm_info["error"] is not None
    assert result.llm_info["error"].startswith("llm_error")


def test_llm_unavailable_without_api_key(csv_factory, tmp_path, monkeypatch):
    # simulate "no key configured" by pointing .env discovery nowhere
    monkeypatch.setattr(config_mod, "ENV_FILE", tmp_path / "no-such.env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    path = csv_factory(tmp_path, ["quantity", "hh_id"])
    result = map_csv(path, use_llm=True)  # will try to build a real client
    quantity = result.mapping_for("quantity")
    assert quantity.status == STATUS_SUGGESTED
    hh = result.mapping_for("hh_id")
    assert hh.status == STATUS_NEEDS_REVIEW
    assert "llm_unavailable" in hh.review_note
    assert result.llm_info["used"] is False
    # the API key must never appear anywhere in the document
    assert "GEMINI_API_KEY is not set" in result.llm_info["error"]


def test_no_llm_flag_skips_stage(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["hh_id", "quantity"])
    result = map_csv(path, use_llm=False)
    hh = result.mapping_for("hh_id")
    assert hh.status == STATUS_NEEDS_REVIEW
    assert "llm_skipped" in hh.review_note


def test_bad_response_error_type_is_distinct():
    assert issubclass(LLMBadResponse, Exception)
    assert issubclass(LLMRateLimit, Exception)
    assert issubclass(LLMUnavailable, Exception)


# --------------------------------------------------------------------------
# prompt safety
# --------------------------------------------------------------------------
def test_prompt_contains_metadata_but_never_raw_values(catalog):
    from column_mapping.detect import detect_kinds
    from column_mapping.llm import PendingColumn

    pending = [
        PendingColumn(
            source_column="cust_mail",
            metadata={
                "name": "cust_mail",
                "inferred_dtype": "string",
                "null_share": 0.0,
                "approx_distinct_values": 4,
                "value_shapes": ["email_like"],
                "likely_pii": True,
            },
        )
    ]
    prompt = build_prompt(pending, catalog, detect_kinds(["cust_mail"]))
    assert "cust_mail" in prompt
    assert "email_like" in prompt
    assert "customers.source_customer_id" in prompt  # target list embedded
    assert "fake.customer1@example.com" not in prompt  # raw values never sent
    assert "@example.com" not in prompt
