"""Deterministic stage: exact canonical-name matches and documented aliases.

Every case here runs with the LLM disabled (`use_llm=False`) so the
exact/alias logic is tested in isolation from any model behaviour.
"""

from __future__ import annotations

from apps.mapping.mapper import map_csv
from apps.mapping.models import (
    METHOD_ALIAS,
    METHOD_EXACT,
    METHOD_NONE,
    REQUIRES_CALCULATION,
    REQUIRES_RELATIONSHIP,
    STATUS_NEEDS_REVIEW,
    STATUS_SUGGESTED,
)


def test_exact_match_on_canonical_name(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["quantity", "complain"])
    result = map_csv(path, use_llm=False)
    quantity = result.mapping_for("quantity")
    assert (quantity.target_table, quantity.target_column) == ("order_items", "quantity")
    assert quantity.method == METHOD_EXACT
    assert quantity.status == STATUS_SUGGESTED
    assert quantity.confidence is None  # confidence is an LLM concept only
    assert quantity.requires == []

    complain = result.mapping_for("complain")
    assert (complain.target_table, complain.target_column) == ("customers", "complain")
    assert complain.method == METHOD_EXACT


def test_exact_match_normalizes_spaces_and_case(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["Satisfaction Score", "TENURE"])
    result = map_csv(path, use_llm=False)
    satisfaction = result.mapping_for("Satisfaction Score")
    assert (satisfaction.target_table, satisfaction.target_column) == (
        "customers",
        "satisfaction_score",
    )
    assert satisfaction.method == METHOD_EXACT
    tenure = result.mapping_for("TENURE")
    assert (tenure.target_table, tenure.target_column) == ("customers", "tenure")


def test_alias_household_key_with_documented_alternate(csv_factory, tmp_path):
    # a roster-like file (customers kind) — primary only, no order alternate
    path = csv_factory(tmp_path, ["household_key", "AGE_DESC"])
    result = map_csv(path, use_llm=False)
    mapping = result.mapping_for("household_key")
    assert (mapping.target_table, mapping.target_column) == ("customers", "source_customer_id")
    assert mapping.method == METHOD_ALIAS
    assert mapping.status == STATUS_SUGGESTED
    assert mapping.alternate_targets == []  # AGE_DESC file has no orders

    # a transactions file — the documented alternate feeds orders.customer_id
    tx = csv_factory(tmp_path, ["household_key", "BASKET_ID", "SALES_VALUE"], prefix="tx")
    tx_result = map_csv(tx, use_llm=False)
    hh = tx_result.mapping_for("household_key")
    assert (hh.target_table, hh.target_column) == ("customers", "source_customer_id")
    assert any(
        a.target_table == "orders" and a.target_column == "customer_id"
        for a in hh.alternate_targets
    )
    assert REQUIRES_RELATIONSHIP in hh.alternate_targets[0].requires


def test_alias_basket_id_multi_target(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["BASKET_ID", "SALES_VALUE", "DAY"], prefix="tx")
    result = map_csv(path, use_llm=False)
    basket = result.mapping_for("BASKET_ID")
    assert (basket.target_table, basket.target_column) == ("orders", "source_order_id")
    assert basket.method == METHOD_ALIAS
    # order_items.order_id is the documented second target (DUNNHUMBY.md L212)
    assert any(
        a.target_table == "order_items" and a.target_column == "order_id"
        and REQUIRES_RELATIONSHIP in a.requires
        for a in basket.alternate_targets
    )


def test_day_primary_plus_calculation_alternate(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["DAY"], prefix="tx")
    result = map_csv(path, use_llm=False)
    day = result.mapping_for("DAY")
    assert (day.target_table, day.target_column) == ("orders", "day_no")
    # the calculated date target is kept separately and forces review
    calc = [a for a in day.alternate_targets if a.target_column == "order_date"]
    assert calc and REQUIRES_CALCULATION in calc[0].requires
    assert day.status == STATUS_SUGGESTED  # day_no itself is a plain rename


def test_trans_time_requires_calculation_and_forces_review(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["TRANS_TIME"], prefix="tx")
    result = map_csv(path, use_llm=False)
    mapping = result.mapping_for("TRANS_TIME")
    assert (mapping.target_table, mapping.target_column) == ("orders", "order_timestamp")
    assert REQUIRES_CALCULATION in mapping.requires
    assert mapping.status == STATUS_NEEDS_REVIEW


def test_store_id_carries_open_decision_and_forces_review(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["STORE_ID"], prefix="tx")
    result = map_csv(path, use_llm=False)
    mapping = result.mapping_for("STORE_ID")
    # target follows schema.sql (source_store_id), NOT docs' orders.store_id
    assert (mapping.target_table, mapping.target_column) == ("orders", "source_store_id")
    assert mapping.status == STATUS_NEEDS_REVIEW
    assert "Open Decision #3" in mapping.decision_note
    assert any(
        a.target_table == "causal_data" and a.target_column == "source_store_id"
        for a in mapping.alternate_targets
    )


def test_price_is_ambiguous_without_kind_but_resolved_with(csv_factory, tmp_path):
    # unknown kind -> two documented targets, must be surfaced, not guessed
    unknown = csv_factory(tmp_path, ["price"], prefix="p1")
    result = map_csv(unknown, use_llm=False)
    mapping = result.mapping_for("price")
    assert mapping.has_target is False
    assert mapping.status == STATUS_NEEDS_REVIEW
    assert mapping.method == METHOD_NONE
    names = {(c.target_table, c.target_column) for c in mapping.candidates}
    assert names == {("events", "price"), ("order_items", "unit_price")}

    # events file -> events.price
    events = csv_factory(tmp_path, ["price", "event_time", "user_id"], prefix="p2")
    ev_result = map_csv(events, use_llm=False)
    ev_price = ev_result.mapping_for("price")
    assert (ev_price.target_table, ev_price.target_column) == ("events", "price")
    assert ev_price.method == METHOD_EXACT

    # transactions file -> order_items.unit_price (Segment 2)
    tx = csv_factory(tmp_path, ["price", "BASKET_ID", "SALES_VALUE"], prefix="p3")
    tx_result = map_csv(tx, use_llm=False)
    tx_price = tx_result.mapping_for("price")
    assert (tx_price.target_table, tx_price.target_column) == ("order_items", "unit_price")
    assert tx_price.method == METHOD_ALIAS


def test_user_id_resolves_to_relationship_field(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["user_id", "user_session", "event_time"], prefix="ev")
    result = map_csv(path, use_llm=False)
    user = result.mapping_for("user_id")
    assert (user.target_table, user.target_column) == ("events", "customer_id")
    assert REQUIRES_RELATIONSHIP in user.requires
    assert user.status == STATUS_SUGGESTED  # documented mapping, apply defers it
    session = result.mapping_for("user_session")
    assert (session.target_table, session.target_column) == ("events", "session_id")


def test_coupon_discount_maps_to_schema_name_with_mismatch_note(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["coupon_discount"], prefix="cp")
    result = map_csv(path, use_llm=False)
    mapping = result.mapping_for("coupon_discount")
    # docs say order_items.coupon_discount; the schema says coupon_disc
    assert (mapping.target_table, mapping.target_column) == ("order_items", "coupon_disc")
    assert mapping.status == STATUS_NEEDS_REVIEW
    assert "coupon_disc" in mapping.decision_note


def test_customer_id_folds_documented_alternate(csv_factory, tmp_path):
    # coupon dataset kind: primary roster id + coupon_redemptions FK alternate
    path = csv_factory(tmp_path, ["customer_id", "coupon_upc", "campaign", "redemption_status"], prefix="cp2")
    result = map_csv(path, use_llm=False)
    mapping = result.mapping_for("customer_id")
    assert (mapping.target_table, mapping.target_column) == ("customers", "source_customer_id")
    assert any(
        a.target_table == "coupon_redemptions" and a.target_column == "customer_id"
        for a in mapping.alternate_targets
    )
    upc = result.mapping_for("coupon_upc")
    assert (upc.target_table, upc.target_column) == ("coupon_redemptions", "coupon_id")


def test_unknown_column_is_not_invented(csv_factory, tmp_path):
    path = csv_factory(tmp_path, ["zzz_internal_flag"], prefix="u")
    result = map_csv(path, use_llm=False)
    mapping = result.mapping_for("zzz_internal_flag")
    assert mapping.has_target is False
    assert mapping.method == METHOD_NONE
    assert "llm_skipped" in (mapping.review_note or "")  # LLM disabled, human decides
