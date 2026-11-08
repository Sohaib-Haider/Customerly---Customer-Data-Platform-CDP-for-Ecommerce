"""Regression tests for issues found running the mapper on the real Kaggle files.

Each test reproduces one real header shape (names only, filler values) and pins
the corrected behaviour. All LLM interaction is mocked.
"""

from __future__ import annotations

from column_mapping.detect import detect_kinds
from column_mapping.llm import PendingColumn, build_prompt
from column_mapping.mapper import map_csv


def _by_col(result):
    return {m.source_column: m for m in result.mappings}


def test_tied_kinds_keep_both_candidates(csv_factory, tmp_path):
    # dunnhumby campaign_desc.csv: products and campaigns tie, so DESCRIPTION
    # must not be forced to products.name by dictionary order.
    path = csv_factory(tmp_path, ["DESCRIPTION", "CAMPAIGN", "START_DAY", "END_DAY"])
    detection = detect_kinds(["DESCRIPTION", "CAMPAIGN", "START_DAY", "END_DAY"])
    assert {"products", "campaigns"} <= detection.tied_kinds
    m = _by_col(map_csv(path, use_llm=False))["DESCRIPTION"]
    assert m.target_table is None
    targets = {(c.target_table, c.target_column) for c in m.candidates}
    assert ("coupon_campaigns", "campaign_type") in targets
    assert ("products", "name") in targets


def test_coupon_campaign_file_is_not_forced_to_marketing(csv_factory, tmp_path):
    # predicting-coupon-redemption campaign_data.csv
    path = csv_factory(tmp_path, ["campaign_id", "campaign_type", "start_date", "end_date"])
    result = _by_col(map_csv(path, use_llm=False))
    for column in ("campaign_id", "campaign_type"):
        targets = {(c.target_table, c.target_column) for c in result[column].candidates}
        assert any(t == "coupon_campaigns" for t, _ in targets), column
        assert result[column].target_table != "marketing_campaigns"
    assert (result["start_date"].target_table, result["start_date"].target_column) == (
        "coupon_campaigns", "start_date")


def test_coupon_campaign_ambiguity_resolved_by_llm(csv_factory, tmp_path, fake_llm_factory):
    path = csv_factory(tmp_path, ["campaign_id", "campaign_type", "start_date", "end_date"])
    llm = fake_llm_factory({
        "campaign_id": ("coupon_campaigns", "source_campaign_id", 0.9, "coupon campaign file"),
        "campaign_type": ("coupon_campaigns", "campaign_type", 0.9, "coupon campaign file"),
    })
    result = _by_col(map_csv(path, llm_client=llm))
    assert result["campaign_id"].target_table == "coupon_campaigns"
    assert result["campaign_type"].target_table == "coupon_campaigns"


def test_specialised_kind_does_not_force_foreign_table(csv_factory, tmp_path):
    # direct-messaging messages-demo.csv: `category` categorises messages.
    cols = ["message_id", "campaign_id", "message_type", "client_id", "channel",
            "category", "sent_at", "is_opened", "is_clicked", "is_purchased"]
    path = csv_factory(tmp_path, cols)
    m = _by_col(map_csv(path, use_llm=False))["category"]
    assert m.target_table is None


def test_retail_kind_still_maps_mixed_entity_columns(csv_factory, tmp_path):
    # A customers-kind file may legitimately carry an order column.
    path = csv_factory(tmp_path, ["quantity", "complain", "TENURE"])
    m = _by_col(map_csv(path, use_llm=False))["quantity"]
    assert (m.target_table, m.target_column) == ("order_items", "quantity")


def test_prompt_carries_whole_file_context(catalog):
    columns = ["id", "message_id", "created_at", "sent_at"]
    detection = detect_kinds(columns)
    detection.resolved_context = {
        "message_id": "message_sends.source_message_id",
        "sent_at": "message_sends.sent_at",
    }
    pending = [PendingColumn(source_column=c, metadata={"name": c}) for c in ("id", "created_at")]
    prompt = build_prompt(pending, catalog, detection)
    assert '"message_id"' in prompt  # sibling header visible
    assert "message_id -> message_sends.source_message_id" in prompt
    assert "audit" in prompt.lower()
