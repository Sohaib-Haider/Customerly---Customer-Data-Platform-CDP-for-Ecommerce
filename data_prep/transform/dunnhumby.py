"""Dunnhumby: The Complete Journey -> canonical schema transform.

Source dataset feeds three segments — Replenishment-Ready (6), Cross-Sell
Opportunity (7), Seasonal Purchase (8). This module only reshapes raw
behavioural data into the canonical table shape. It does NOT aggregate and does
NOT compute ML features: feature computation belongs to the platform's
scheduled jobs over the loaded tables (final-schema.md, customer_features).

Column mappings are taken from:
  docs/notes/customer segmentation-notes/segments-schema-mapping.md  (Segments 6/7/8)
  docs/notes/customer segmentation-notes/segments/Replenishment-Ready.md
  docs/notes/customer segmentation-notes/segments/Cross-Sell Opportunity.md
  docs/notes/customer segmentation-notes/segments/Seasonal Purchase.md
  docs/notes/schema-notes/final-schema.md                            (target shape)

Output: one DataFrame per canonical table, in schema.sql column order, with
deterministic UUIDs already resolved so a loader can bulk-insert directly.

KNOWN GAPS are surfaced as `notes`, never silently invented — see DUNNHUMBY.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from common import (
    align_day,
    anchor_for,
    combine_timestamp,
    empty_string,
    source_id,
    to_bool,
    to_float,
    to_int,
    uid,
)

DATASET = "dunnhumby"

# This dataset's own declared anchor (day-number source). Passed explicitly to
# align_day so no other dataset can inherit it by accident — see common.py.
ANCHOR = anchor_for(DATASET)


def _align(day_no: object) -> date | None:
    """align_day bound to this dataset's anchor."""
    return align_day(day_no, ANCHOR)


# The 8 raw files named in the segment documentation for this dataset.
RAW_FILES = [
    "transaction_data.csv",
    "hh_demographic.csv",
    "product.csv",
    "causal_data.csv",
    "coupon.csv",
    "coupon_redempt.csv",
    "campaign_table.csv",
    "campaign_desc.csv",
]

# ---------------------------------------------------------------------------
# Canonical output column order (verbatim from backend/schema.sql)
# ---------------------------------------------------------------------------
COLUMNS = {
    "stores": ["id", "name", "plan", "created_at", "updated_at"],
    "customers": [
        "id", "store_id", "source_customer_id",
        "first_purchase_date", "tenure", "warehouse_to_home",
        "number_of_devices_registered", "satisfaction_score", "complain",
        "days_since_last_order", "cashback_amount",
        "preferred_order_category", "churn", "created_at", "updated_at",
    ],
    "products": [
        "id", "store_id", "source_product_id", "name", "brand", "category",
        "department", "commodity_desc", "unit_price", "created_at", "updated_at",
    ],
    "orders": [
        "id", "store_id", "source_order_id", "customer_id", "order_date",
        "order_timestamp", "week_no", "day_no", "source_store_id", "region",
        "created_at",
    ],
    "order_items": [
        "id", "order_id", "product_id", "quantity", "unit_price",
        "sales_value", "retail_disc", "coupon_disc", "coupon_match_disc",
        "other_discount",
    ],
    "causal_data": [
        "id", "store_id", "product_id", "source_store_id", "week_no",
        "display", "mailer",
    ],
    "coupon_campaigns": [
        "id", "store_id", "source_campaign_id", "campaign_type",
        "start_date", "end_date", "created_at",
    ],
    "coupon_redemptions": [
        "id", "store_id", "coupon_id", "campaign_id", "customer_id",
        "redemption_status", "created_at",
    ],
}


@dataclass
class TransformResult:
    """Emitted frames plus everything the transform refused to do."""

    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    # source file each drop came from -- tagging beats re-parsing reason strings,
    # which is how redemption drops leaked into the transaction line accounting.
    dropped_by_source: dict[str, int] = field(default_factory=dict)
    raw_counts: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, count: int, source: str = "transaction_data.csv") -> None:
        if count:
            self.dropped[reason] = self.dropped.get(reason, 0) + count
            self.dropped_by_source[source] = self.dropped_by_source.get(source, 0) + count

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_raw(raw_dir) -> dict[str, pd.DataFrame]:
    """Read every expected source CSV, keeping source column names intact."""
    from pathlib import Path

    raw_dir = Path(raw_dir)
    missing = [f for f in RAW_FILES if not (raw_dir / f).exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing source files in {raw_dir}: {', '.join(missing)}. "
            "Download 'frtgnn/dunnhumby-the-complete-journey' from Kaggle into "
            "data_prep/raw/dunnhumby/ (kaggle://datasets/download/frtgnn/"
            "dunnhumby-the-complete-journey)."
        )
    # causal_data.csv is ~700 MB; pinning dtypes up front keeps it from being
    # re-parsed as wide float/object columns (and stops PRODUCT_ID arriving as 2.7e+04).
    # TRANS_TIME must stay a string: it is zero-padded HHMM, and reading it as int64
    # silently drops the leading zero ("0955" -> 955 -> 3 digits), which cost 25,188
    # real orders their timestamp before this was caught.
    dtypes = {
        "causal_data.csv": {
            "PRODUCT_ID": "str",
            "STORE_ID": "Int64",
            "WEEK_NO": "Int64",
            "display": "str",
            "mailer": "str",
        },
        "transaction_data.csv": {
            "TRANS_TIME": "str",
        },
    }
    return {
        name: pd.read_csv(
            raw_dir / name,
            low_memory=False,
            dtype=dtypes.get(name),
        )
        for name in RAW_FILES
    }


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------
def _products(raw: dict[str, pd.DataFrame], result: TransformResult) -> pd.DataFrame:
    """product.csv -> products.

    Mapped: PRODUCT_ID, BRAND, DEPARTMENT, COMMODITY_DESC (segments-schema-mapping
    Segments 6/7/8). MANUFACTURER/SUB_COMMODITY_DESC/CURR_SIZE_OF_PRODUCT are
    documented as 'Not needed for this segment'.
    """
    src = raw["product.csv"]
    result.raw_counts["product.csv"] = len(src)

    frame = src.copy()
    frame["_key"] = frame["PRODUCT_ID"].map(lambda v: source_id(v, DATASET))
    bad = frame["_key"] == ""
    result.drop("products with empty PRODUCT_ID", int(bad.sum()), source="product.csv")
    frame = frame[~bad]

    dupes = int(frame["_key"].duplicated().sum())
    result.drop("duplicate PRODUCT_ID rows (kept first)", dupes, source="product.csv")
    frame = frame.drop_duplicates(subset="_key", keep="first")

    out = pd.DataFrame(index=frame.index)
    out["id"] = frame["_key"].map(lambda k: uid("products", k))
    out["source_product_id"] = frame["_key"]
    # products.name is NOT NULL but no column in this dataset is a product name.
    # COMMODITY_DESC is the closest documented descriptive attribute (mapped to
    # commodity_desc). FLAGGED: derived, not sourced — see DUNNHUMBY.md.
    name = frame.get("COMMODITY_DESC", pd.Series(index=frame.index, dtype=object))
    fallback = frame.get("SUB_COMMODITY_DESC", pd.Series(index=frame.index, dtype=object))
    out["name"] = name.where(name.notna() & (name.astype(str).str.strip() != ""), fallback)
    out["name"] = out["name"].where(
        out["name"].notna() & (out["name"].astype(str).str.strip() != ""),
        frame["_key"],
    )
    out["brand"] = frame.get("BRAND")
    out["category"] = None  # no Dunnhumby source column documented (only coupon dataset has category)
    out["department"] = frame.get("DEPARTMENT")
    out["commodity_desc"] = frame.get("COMMODITY_DESC")
    # products.unit_price is [S] but no catalog-level price exists in this source;
    # price here is transaction-level only (final-schema.md Open Decision #11).
    out["unit_price"] = None
    out["created_at"] = None  # set by the application, not the transform
    out["updated_at"] = None

    if out["name"].isna().any():
        result.note("products.name derived from COMMODITY_DESC/SUB_COMMODITY_DESC (no name column in source)")
    result.note("products.unit_price left NULL — no catalog price column in this dataset (Open Decision #11)")
    result.note("products.category left NULL — no documented Dunnhumby source column")
    return out


def _customers(raw: dict[str, pd.DataFrame], result: TransformResult) -> pd.DataFrame:
    """Households -> customers.

    household_key -> customers.source_customer_id (Segment 6/7/8, ESSENTIAL).
    Every other customers column is fed by the *churn* dataset, which is
    deliberately not merged (2.7-constraints.md), so all are NULL here.

    hh_demographic.csv contributes only the roster: all 7 of its demographic
    attributes are documented as 'Not needed for this segment' /
    'All demographics — not included per user request' (Segments 6/7/8).
    """
    tx, demo = raw["transaction_data.csv"], raw["hh_demographic.csv"]
    result.raw_counts["transaction_data.csv"] = len(tx)
    result.raw_counts["hh_demographic.csv"] = len(demo)

    tx_keys = tx["household_key"].dropna().map(lambda v: source_id(v, DATASET))
    demo_keys = demo["household_key"].dropna().map(lambda v: source_id(v, DATASET))
    tx_keys = tx_keys[tx_keys != ""]
    demo_keys = demo_keys[demo_keys != ""]

    keys = sorted(set(tx_keys) | set(demo_keys))
    only_demo = len(set(demo_keys) - set(tx_keys))
    if only_demo:
        result.note(
            f"{only_demo} households exist in hh_demographic but never transacted; "
            "kept as customers with no orders"
        )
    if len(demo.columns) > 1:
        result.note(
            "hh_demographic.csv demographics (AGE_DESC, MARITAL_STATUS_CODE, "
            "INCOME_DESC, HOMEOWNER_DESC, HH_COMP_DESC, HOUSEHOLD_SIZE_DESC, "
            "KID_CATEGORY_DESC) have NO canonical column — excluded by "
            "segments-schema-mapping.md Segments 6/7/8 ('All demographics — not needed')"
        )

    out = pd.DataFrame({"source_customer_id": keys})
    out["id"] = out["source_customer_id"].map(lambda k: uid("customers", k))
    for col in COLUMNS["customers"]:
        if col not in out.columns and col != "store_id":
            out[col] = None
    # store_id is stamped on later by _attach_store, so select its complement here
    return out[[c for c in COLUMNS["customers"] if c != "store_id"]]


def _orders_and_items(
    raw: dict[str, pd.DataFrame], customers: pd.DataFrame, products: pd.DataFrame, result: TransformResult
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """transaction_data.csv -> orders (basket header) + order_items (line items).

    Mapped: BASKET_ID->orders.source_order_id, DAY->orders.day_no +
    orders.order_date, WEEK_NO->orders.week_no, STORE_ID->orders.source_store_id
    (renamed from the documented `store_id` to avoid colliding with the tenant
    FK — final-schema.md Open Decision #3), TRANS_TIME->orders.order_timestamp.
    QUANTITY/SALES_VALUE/RETAIL_DISC/COUPON_DISC/COUPON_MATCH_DISC -> order_items.

    All money stays at line level: financials live on order_items, never on
    orders (schema-blueprint.md principle 6).
    """
    tx = raw["transaction_data.csv"]
    needed = ["BASKET_ID", "DAY", "PRODUCT_ID", "household_key"]
    missing_cols = [c for c in needed if c not in tx.columns]
    if missing_cols:
        raise KeyError(f"transaction_data.csv missing expected columns: {missing_cols}")

    frame = tx.copy()
    for col in needed:
        if col not in {"household_key"}:
            pass
    frame["_basket"] = frame["BASKET_ID"].map(lambda v: source_id(v, DATASET))
    frame["_product"] = frame["PRODUCT_ID"].map(lambda v: source_id(v, DATASET))
    frame["_customer"] = frame["household_key"].map(lambda v: source_id(v, DATASET))

    result.drop("lines with empty BASKET_ID", int((frame["_basket"] == "").sum()))
    result.drop("lines with empty PRODUCT_ID", int((frame["_product"] == "").sum()))
    result.drop("lines with empty household_key", int((frame["_customer"] == "").sum()))
    result.drop("lines with missing DAY (order_date NOT NULL)", int(frame["DAY"].map(empty_string).sum()))
    frame = frame[
        (frame["_basket"] != "") & (frame["_product"] != "") & (frame["_customer"] != "")
    ].copy()
    frame = frame[~frame["DAY"].map(empty_string)].copy()

    cust_ids = dict(zip(customers["source_customer_id"], customers["id"]))
    prod_ids = set(products["source_product_id"])

    orphan_cust = ~frame["_customer"].isin(cust_ids)
    result.drop("lines whose household was not emitted as a customer", int(orphan_cust.sum()))
    frame = frame[~orphan_cust]

    orphan_prod = ~frame["_product"].isin(prod_ids)
    result.drop("lines whose product_id is absent from product.csv (FK would break)", int(orphan_prod.sum()))
    frame = frame[~orphan_prod]

    # ---- orders: one row per basket -------------------------------------
    grp = frame.groupby("_basket", sort=False)
    header = pd.DataFrame({
        "_basket": list(grp.groups.keys()),
        "_customer": grp["_customer"].first().values,
        "_day": grp["DAY"].first().values,
        "_week": grp["WEEK_NO"].first().values if "WEEK_NO" in frame.columns else None,
        "_store": grp["STORE_ID"].first().values if "STORE_ID" in frame.columns else None,
        "_time": grp["TRANS_TIME"].first().values if "TRANS_TIME" in frame.columns else None,
    })
    header["id"] = header["_basket"].map(lambda k: uid("orders", k))
    header["customer_id"] = header["_customer"].map(lambda k: cust_ids.get(k))
    header["order_date"] = header["_day"].map(_align)
    header["order_timestamp"] = [
        combine_timestamp(d, t) for d, t in zip(header["_day"].map(_align), header["_time"])
    ]
    header["week_no"] = header["_week"].map(to_int)
    header["day_no"] = header["_day"].map(to_int)
    header["source_order_id"] = header["_basket"]
    header["source_store_id"] = header["_store"].map(to_int)
    header["region"] = None  # Open Decision #4: no geo column in this dataset
    header["created_at"] = None  # set by the application, not the transform

    nodedup = int(header["id"].duplicated().sum())
    if nodedup:
        result.drop("duplicate BASKET_ID order headers", nodedup)
        header = header.drop_duplicates(subset="id", keep="first")

    no_time = int(header["order_timestamp"].isna().sum())
    if no_time:
        result.note(f"{no_time} orders have no usable TRANS_TIME -> order_timestamp NULL (Open Decision #5)")

    # ---- order_items: one row per transaction line ----------------------
    frame["_line_no"] = frame.groupby(["_basket", "_product"]).cumcount()
    items = frame.copy()
    items["id"] = [
        uid("order_items", f"{b}|{p}|{n}")
        for b, p, n in zip(items["_basket"], items["_product"], items["_line_no"])
    ]
    items["order_id"] = items["_basket"].map(lambda k: uid("orders", k))
    items["product_id"] = items["_product"].map(lambda k: uid("products", k))
    items["quantity"] = items["QUANTITY"].map(to_int)
    items["sales_value"] = items["SALES_VALUE"].map(to_float)
    items["retail_disc"] = items["RETAIL_DISC"].map(to_float) if "RETAIL_DISC" in items else 0.0
    items["coupon_disc"] = items["COUPON_DISC"].map(to_float) if "COUPON_DISC" in items else 0.0
    items["coupon_match_disc"] = (
        items["COUPON_MATCH_DISC"].map(to_float) if "COUPON_MATCH_DISC" in items else 0.0
    )
    items["other_discount"] = 0.0  # no source column in this dataset

    # SALES_VALUE is already the NET amount for this source (segments-schema-mapping
    # Segment 2 pricing note): do not subtract RETAIL_DISC/COUPON_MATCH_DISC again.
    # order_items.unit_price is NOT NULL but this source has no unit price column,
    # so it is derived as sales_value / quantity (net per-unit). FLAGGED.
    items["unit_price"] = [
        round(s / q, 4) if (s is not None and q not in (None, 0)) else 0.0
        for s, q in zip(items["sales_value"], items["quantity"])
    ]

    missing_qty = int(items["quantity"].isna().sum())
    if missing_qty:
        result.drop("order_items with unparseable QUANTITY", missing_qty)
        items = items[items["quantity"].notna()]
    result.note(
        "order_items.unit_price derived as sales_value / quantity — this source has "
        "no unit-price column (schema-blueprint principle 3: platform-computed)"
    )

    # store_id is stamped on later by _attach_store
    orders = header[[c for c in COLUMNS["orders"] if c != "store_id"]]
    order_items = items[COLUMNS["order_items"]]
    return orders, order_items


def _causal_data(
    raw: dict[str, pd.DataFrame], products: pd.DataFrame, result: TransformResult
) -> pd.DataFrame:
    """causal_data.csv -> causal_data (product x source-store x week promo flags).

    Mapped verbatim from Segment 8: PRODUCT_ID, STORE_ID, WEEK_NO, display, mailer.
    Joined later to orders on (product_id, source_store_id, week_no) with no
    re-mapping, which is what makes customer_features.promo_response_rate
    computable for the Seasonal Purchase Propensity card.

    NOTE (real data): `display`/`mailer` are NOT 0/1 flags as the segment docs
    describe them — in the source they are promotion *codes* (display 0-9,
    mailer A/D/F/H/J/L...). The schema types them VARCHAR(50), so raw values are
    stored verbatim; the `display/mailer = 1` predicate in the promo_response_rate
    formula is therefore a team decision, not something this transform resolves.
    """
    src = raw["causal_data.csv"]
    result.raw_counts["causal_data.csv"] = len(src)

    frame = src.copy()
    frame["_product"] = frame["PRODUCT_ID"].map(lambda v: source_id(v, DATASET))
    frame["_week"] = frame["WEEK_NO"].map(to_int)
    frame["_store"] = frame["STORE_ID"].map(to_int)

    result.drop("causal rows with empty PRODUCT_ID", int((frame["_product"] == "").sum()), source="causal_data.csv")
    result.drop("causal rows with missing STORE_ID (NOT NULL)", int(frame["STORE_ID"].map(empty_string).sum()), source="causal_data.csv")
    result.drop("causal rows with missing WEEK_NO (NOT NULL)", int(frame["WEEK_NO"].map(empty_string).sum()), source="causal_data.csv")
    frame = frame[
        (frame["_product"] != "")
        & ~frame["STORE_ID"].map(empty_string)
        & ~frame["WEEK_NO"].map(empty_string)
    ].copy()

    known = set(products["source_product_id"])
    orphan = ~frame["_product"].isin(known)
    result.drop("causal rows whose product is absent from product.csv", int(orphan.sum()), source="causal_data.csv")
    frame = frame[~orphan]

    before = len(frame)
    frame = frame.drop_duplicates(subset=["_store", "_product", "_week"], keep="first")
    result.drop(
        "causal rows violating UNIQUE(store, source_store, product, week)",
        before - len(frame),
        source="causal_data.csv",
    )

    out = pd.DataFrame(index=frame.index)
    out["id"] = [
        uid("causal_data", f"{s}|{p}|{w}")
        for s, p, w in zip(frame["_store"], frame["_product"], frame["_week"])
    ]
    out["product_id"] = frame["_product"].map(lambda k: uid("products", k))
    out["source_store_id"] = frame["_store"]
    out["week_no"] = frame["_week"]
    out["display"] = frame.get("display")
    out["mailer"] = frame.get("mailer")
    return out[[c for c in COLUMNS["causal_data"] if c != "store_id"]]


def _coupon_campaigns(raw: dict[str, pd.DataFrame], result: TransformResult) -> pd.DataFrame:
    """campaign_desc.csv -> coupon_campaigns.

    Mapped: CAMPAIGN->source_campaign_id, DESCRIPTION->campaign_type,
    START_DAY/END_DAY->start_date/end_date using the SAME shared anchor as
    orders, so campaign windows sit on the same timeline as purchases.
    """
    src = raw["campaign_desc.csv"]
    result.raw_counts["campaign_desc.csv"] = len(src)

    frame = src.copy()
    frame["_key"] = frame["CAMPAIGN"].map(lambda v: source_id(v, DATASET))
    result.drop("campaigns with empty CAMPAIGN", int((frame["_key"] == "").sum()), source="campaign_desc.csv")
    frame = frame[frame["_key"] != ""].copy()

    before = len(frame)
    frame = frame.drop_duplicates(subset="_key", keep="first")
    result.drop("duplicate CAMPAIGN rows (kept first)", before - len(frame), source="campaign_desc.csv")

    frame["_start"] = frame["START_DAY"].map(_align)
    frame["_end"] = frame["END_DAY"].map(_align)
    bad = frame["_start"].isna() | frame["_end"].isna()
    result.drop(
        "campaigns with unusable START_DAY/END_DAY (both NOT NULL)",
        int(bad.sum()),
        source="campaign_desc.csv",
    )
    frame = frame[~bad]

    out = pd.DataFrame(index=frame.index)
    out["id"] = frame["_key"].map(lambda k: uid("coupon_campaigns", k))
    out["source_campaign_id"] = frame["_key"]
    out["campaign_type"] = frame["DESCRIPTION"]
    out["start_date"] = frame["_start"]
    out["end_date"] = frame["_end"]
    out["created_at"] = None
    return out[[c for c in COLUMNS["coupon_campaigns"] if c != "store_id"]]


def _coupon_redemptions(
    raw: dict[str, pd.DataFrame], campaigns: pd.DataFrame, customers: pd.DataFrame, result: TransformResult
) -> pd.DataFrame:
    """coupon_redempt.csv -> coupon_redemptions.

    Mapped: household_key->customer_id, COUPON_UPC->coupon_id,
    CAMPAIGN->campaign_id. redemption_status is TRUE because every row in this
    file is by definition a redemption.

    DROPPED: DAY. coupon_redemptions has no redemption timestamp column
    (final-schema.md documents the source carries no redemption date and no KPI
    requires one), so the day is intentionally not persisted.
    """
    src = raw["coupon_redempt.csv"]
    result.raw_counts["coupon_redempt.csv"] = len(src)

    frame = src.copy()
    frame["_customer"] = frame["household_key"].map(lambda v: source_id(v, DATASET))
    frame["_coupon"] = frame["COUPON_UPC"].map(lambda v: source_id(v, DATASET))
    frame["_campaign"] = frame["CAMPAIGN"].map(lambda v: source_id(v, DATASET))
    result.drop("redemptions with empty COUPON_UPC", int((frame["_coupon"] == "").sum()), source="coupon_redempt.csv")
    frame = frame[frame["_coupon"] != ""].copy()

    camp_ids = dict(zip(campaigns["source_campaign_id"], campaigns["id"]))
    cust_ids = dict(zip(customers["source_customer_id"], customers["id"]))

    orphan_camp = ~frame["_campaign"].isin(camp_ids)
    result.drop("redemptions whose campaign is absent from campaign_desc.csv", int(orphan_camp.sum()), source="coupon_redempt.csv")
    frame = frame[~orphan_camp]

    orphan_cust = ~frame["_customer"].isin(cust_ids)
    result.drop("redemptions whose household is absent from customers", int(orphan_cust.sum()), source="coupon_redempt.csv")
    frame = frame[~orphan_cust]

    before = len(frame)
    frame = frame.drop_duplicates(subset=["_customer", "_coupon", "_campaign"], keep="first")
    result.drop(
        "duplicate (customer, coupon, campaign) redemptions",
        before - len(frame),
        source="coupon_redempt.csv",
    )

    out = pd.DataFrame(index=frame.index)
    out["id"] = [
        uid("coupon_redemptions", f"{c}|{p}|{m}")
        for c, p, m in zip(frame["_customer"], frame["_coupon"], frame["_campaign"])
    ]
    out["coupon_id"] = frame["_coupon"]
    out["campaign_id"] = frame["_campaign"].map(lambda k: camp_ids.get(k))
    out["customer_id"] = frame["_customer"].map(lambda k: cust_ids.get(k))
    out["redemption_status"] = True
    out["created_at"] = None
    return out[[c for c in COLUMNS["coupon_redemptions"] if c != "store_id"]]


def _stores(store_id: str) -> pd.DataFrame:
    """Tenant row for this dataset's demo store.

    `plan` must be NOT NULL but the value vocabulary is undocumented/conflicting
    (final-schema.md Open Decision #6); 'pro' is one of the three values listed
    in final-schema.md table 1. Replace at load time if the team disagrees.
    """
    return pd.DataFrame([{
        "id": store_id,
        "name": "Dunnhumby Demo Store",
        "plan": "pro",
        "created_at": None,
        "updated_at": None,
    }])[COLUMNS["stores"]]


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def transform(raw: dict[str, pd.DataFrame], store_id: str) -> TransformResult:
    """Run every Dunnhumby transform and return canonical frames + audit trail."""
    result = TransformResult()

    products = _products(raw, result)
    customers = _customers(raw, result)
    orders, order_items = _orders_and_items(raw, customers, products, result)
    causal = _causal_data(raw, products, result)
    campaigns = _coupon_campaigns(raw, result)
    redemptions = _coupon_redemptions(raw, campaigns, customers, result)

    result.frames = {
        "stores": _stores(store_id),
        "customers": _attach_store(customers, store_id),
        "products": _attach_store(products, store_id),
        "orders": _attach_store(orders, store_id),
        "order_items": order_items,
        "causal_data": _attach_store(causal, store_id),
        "coupon_campaigns": _attach_store(campaigns, store_id),
        "coupon_redemptions": _attach_store(redemptions, store_id),
    }
    # enforce exact schema.sql column order on every emitted table
    result.frames = {t: result.frames[t][COLUMNS[t]] for t in COLUMNS}

    # Explicit "this source has nothing here" statements, so absence is a
    # documented decision instead of a silent empty table.
    result.note("`events` NOT emitted — this dataset has no behavioural event stream (view/cart/purchase)")
    result.note("`marketing_campaigns`/`message_sends` NOT emitted — belongs to the direct-messaging dataset (Segment 5)")
    result.note("`holidays` NOT emitted — no holidays.csv in this dataset (documented in Seasonal Purchase.md)")

    for name in ("coupon.csv", "campaign_table.csv", "hh_demographic.csv"):
        result.raw_counts[name] = len(raw[name])
    result.note(
        "coupon.csv (COUPON_UPC, PRODUCT_ID, CAMPAIGN) NOT emitted — the canonical "
        "schema has no coupon->product table; no column mapping exists in any segment file"
    )
    result.note(
        "campaign_table.csv (DESCRIPTION, household_key, CAMPAIGN) NOT emitted — this is "
        "campaign *exposure*, not redemption; no canonical table holds 'customer was targeted'"
    )
    return result


def _attach_store(frame: pd.DataFrame, store_id: str) -> pd.DataFrame:
    if "store_id" in frame.columns:
        return frame
    out = frame.copy()
    out.insert(1 if "id" in out.columns else 0, "store_id", store_id)
    return out
