"""Source/dataset kind detection: reviewable, multi-entity aware, overridable."""

from __future__ import annotations

from column_mapping.detect import KIND_UNKNOWN, KindDetection, detect_kinds
from column_mapping.mapper import map_csv


def test_transactions_file_detection(sample_csv):
    detection = detect_kinds(
        ["hh_id", "ticket_no", "txn_day", "week_no", "store_no", "prod_sku",
         "item_desc", "dept_name", "units_sold", "net_sales", "retail_promo_amt",
         "coupon_amt", "price", "tx_time", "cust_mail", "loyalty_pts", "zzz_internal_flag"]
    )
    assert detection.primary == "transactions"
    assert detection.review_required is True  # multi-entity file -> flag for review
    assert "transactions" in detection.scores
    # per-column evidence is exposed for the reviewer
    assert detection.column_kinds["ticket_no"] == "transactions"
    assert detection.column_kinds["prod_sku"] == "products"
    # columns with no recognisable signal vote for nothing (honest unknowns)
    assert detection.column_kinds["hh_id"] == KIND_UNKNOWN


def test_events_file_detection():
    detection = detect_kinds(
        ["event_time", "event_type", "user_id", "user_session", "brand", "price"]
    )
    assert detection.primary == "events"


def test_coupons_file_detection():
    detection = detect_kinds(["coupon_upc", "campaign", "redemption_status", "customer_id"])
    assert detection.primary == "coupons"


def test_unrecognised_columns_yield_unknown_and_review():
    detection = detect_kinds(["qqq_one", "www_two", "eee_three"])
    assert detection.primary == KIND_UNKNOWN
    assert detection.review_required is True


def test_effective_kind_prefers_override():
    detection = KindDetection(primary="customers", review_required=False)
    assert detection.effective_kind == "customers"
    detection.override = "events"
    assert detection.effective_kind == "events"


def test_roundtrip_dict():
    detection = detect_kinds(["user_id", "event_time"])
    restored = KindDetection.from_dict(detection.to_dict())
    assert restored.primary == detection.primary
    assert restored.scores == detection.scores
    assert restored.column_kinds == detection.column_kinds


def test_kind_override_is_recorded_and_used(csv_factory, tmp_path):
    # 'date' alone is ambiguous (orders vs holidays); an override resolves it
    # deterministically and lands in the reviewable JSON document.
    path = csv_factory(tmp_path, ["date", "holiday"], prefix="hd")
    result = map_csv(path, use_llm=False)  # detection: holidays (holiday signal)
    mapping = result.mapping_for("date")
    assert (mapping.target_table, mapping.target_column) == ("holidays", "date")

    overridden = map_csv(path, use_llm=False, kind_override="transactions")
    od = overridden.mapping_for("date")
    assert (od.target_table, od.target_column) == ("orders", "order_date")
    detection = overridden.source_profile["kind_detection"]
    assert detection["override_kind"] == "transactions"
    assert detection["effective_kind"] == "transactions"
