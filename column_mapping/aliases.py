"""Exact/alias matching seeded from the EXISTING mapping documentation.

Every entry below is a documented source-to-target mapping — citations point
at the file and line that documents it (repo convention: evidence, not
invention, per ``data_prep/transform/README.md`` convention 5). The target
names are the ones in ``backend/schema.sql``; where the mapping documentation
names a *different* target than the schema, the entry carries a
``decision_note`` quoting the open decision and the mapping is forced to
``needs_review`` — the conflict is surfaced, never silently resolved.

Matching rules
--------------
* Keys are normalized (``household_key`` and ``household key`` and
  ``HOUSEHOLD KEY`` collide), see :func:`normalize`.
* The FIRST entry of an alias is its primary target (the one that can be
  persisted into ``ingestion_column_mappings``); later entries with different
  kind scopes are competing primaries for other kinds of files.
* ``alternates`` are ADDITIONAL targets the same source column feeds
  (``DAY`` -> day_no + order_date) — they live only in the JSON document
  because ``UNIQUE (upload_id, source_column)`` allows one persisted row.
* ``requires`` marks mappings the offline transforms prove need extra work:
  ``relationship_resolution`` (source key -> platform UUID) and
  ``calculation`` (anchor arithmetic, HHMM+day combination). Apply skips both.
* ``kinds`` restricts an entry to file types where it is documented; ``None``
  means any kind.

Not listed here on purpose: columns whose normalized source name equals the
canonical column name (``quantity``, ``complain``, ``satisfaction_score`` ...) —
those are resolved by exact matching against the parsed schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import REQUIRES_CALCULATION, REQUIRES_RELATIONSHIP


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass(frozen=True)
class AliasAlternate:
    """A second target the same source column feeds (kind-scoped)."""

    table: str
    column: str
    citation: str
    kinds: frozenset[str] | None = None
    requires: tuple[str, ...] = (REQUIRES_RELATIONSHIP,)


@dataclass(frozen=True)
class AliasTarget:
    table: str
    column: str
    citation: str
    kinds: frozenset[str] | None = None
    requires: tuple[str, ...] = ()
    decision_note: str | None = None
    alternates: tuple[AliasAlternate, ...] = ()


def _k(*names: str) -> tuple[str, ...]:
    return tuple(normalize(n) for n in names)


def _entries(*items: AliasTarget) -> tuple[AliasTarget, ...]:
    return items


# ---------------------------------------------------------------------------
# Documented mappings. Citations:
#   segments-schema-mapping.md  = docs/notes/customer segmentation-notes/...
#   DUNNHUMBY.md                = data_prep/transform/DUNNHUMBY.md
#   BEHAVIOR_AND_RETAIL.md      = data_prep/transform/BEHAVIOR_AND_RETAIL.md
#   final-schema.md             = docs/notes/schema-notes/final-schema.md
#   dunnhumby.py / behavior.py  = data_prep/transform/*.py (implemented mapping)
# ---------------------------------------------------------------------------
ALIASES: dict[str, tuple[AliasTarget, ...]] = {}

_RAW: dict[str, tuple[AliasTarget, ...]] = {
    # --- Dunnhumby transactions / roster ---------------------------------
    "household_key": _entries(
        AliasTarget(
            "customers", "source_customer_id",
            "segments-schema-mapping.md L319 (ESSENTIAL)",
            kinds=frozenset({"customers", "transactions"}),
            alternates=(
                AliasAlternate(
                    "orders", "customer_id",
                    "DUNNHUMBY.md L117: household_key -> orders.customer_id -> customers.id",
                    kinds=frozenset({"transactions", "orders"}),
                ),
            ),
        ),
        AliasTarget(
            "coupon_redemptions", "customer_id",
            "dunnhumby.py L493-494: household_key -> customer_id (coupon_redempt)",
            kinds=frozenset({"coupons"}),
            requires=(REQUIRES_RELATIONSHIP,),
            alternates=(
                AliasAlternate(
                    "customers", "source_customer_id",
                    "segments-schema-mapping.md L319",
                    kinds=frozenset({"coupons"}),
                    requires=(),  # direct roster id
                ),
            ),
        ),
    ),
    "BASKET_ID": _entries(
        AliasTarget(
            "orders", "source_order_id",
            "segments-schema-mapping.md L320 (ESSENTIAL)",
            kinds=frozenset({"transactions", "orders"}),
            alternates=(
                AliasAlternate(
                    "order_items", "order_id",
                    "DUNNHUMBY.md L212: order_items keys are (BASKET_ID, PRODUCT_ID, occurrence)",
                    kinds=frozenset({"transactions", "orders"}),
                ),
            ),
        ),
    ),
    "DAY": _entries(
        AliasTarget(
            "orders", "day_no",
            "segments-schema-mapping.md L321: native study day 1-711",
            kinds=frozenset({"transactions"}),
            alternates=(
                AliasAlternate(
                    "orders", "order_date",
                    "segments-schema-mapping.md L322: anchor_date + (day_no - 1)",
                    kinds=frozenset({"transactions"}),
                    requires=(REQUIRES_CALCULATION,),
                ),
            ),
        ),
    ),
    "WEEK_NO": _entries(
        AliasTarget(
            "orders", "week_no",
            "segments-schema-mapping.md L326 (MODERATE)",
            kinds=frozenset({"transactions"}),
        ),
    ),
    "STORE_ID": _entries(
        AliasTarget(
            "orders", "source_store_id",
            "DUNNHUMBY.md L121 (mapping L327 says orders.store_id; schema.sql "
            "renamed it source_store_id)",
            kinds=frozenset({"transactions"}),
            decision_note=(
                "Open Decision #3: segments-schema-mapping.md L327/L456 names the "
                "target orders.store_id (collides with the tenant FK); schema.sql "
                "emits source_store_id. Target follows schema.sql — reviewer must "
                "confirm before use."
            ),
            alternates=(
                AliasAlternate(
                    "causal_data", "source_store_id",
                    "DUNNHUMBY.md L159: STORE_ID -> causal_data.source_store_id",
                    kinds=frozenset({"transactions"}),
                    requires=(),  # [S] direct column
                ),
            ),
        ),
    ),
    "TRANS_TIME": _entries(
        AliasTarget(
            "orders", "order_timestamp",
            "DUNNHUMBY.md L122: DAY + TRANS_TIME (4-digit HHMM) combined",
            kinds=frozenset({"transactions"}),
            requires=(REQUIRES_CALCULATION,),
        ),
    ),
    "QUANTITY": _entries(
        AliasTarget(
            "order_items", "quantity",
            "segments-schema-mapping.md L324/L104 (ESSENTIAL)",
        ),
    ),
    "SALES_VALUE": _entries(
        AliasTarget(
            "order_items", "sales_value",
            "segments-schema-mapping.md L325 (STRONG)",
            kinds=frozenset({"transactions"}),
        ),
    ),
    "RETAIL_DISC": _entries(
        AliasTarget(
            "order_items", "retail_disc",
            "DUNNHUMBY.md L126 (carried: schema needs it)",
            kinds=frozenset({"transactions"}),
        ),
    ),
    "COUPON_DISC": _entries(
        AliasTarget(
            "order_items", "coupon_disc",
            "DUNNHUMBY.md L127",
            kinds=frozenset({"transactions"}),
        ),
    ),
    "COUPON_MATCH_DISC": _entries(
        AliasTarget(
            "order_items", "coupon_match_disc",
            "DUNNHUMBY.md L128",
            kinds=frozenset({"transactions"}),
        ),
    ),
    "PRODUCT_ID": _entries(
        AliasTarget(
            "products", "source_product_id",
            "segments-schema-mapping.md L323 (ESSENTIAL); behavior.py derives the "
            "products dimension from event product_id",
            kinds=frozenset({"products", "transactions", "orders", "events", "coupons"}),
            alternates=(
                AliasAlternate(
                    "events", "product_id",
                    "segments-schema-mapping.md L48",
                    kinds=frozenset({"events"}),
                ),
                AliasAlternate(
                    "order_items", "product_id",
                    "DUNNHUMBY.md L212",
                    kinds=frozenset({"transactions", "orders", "coupons"}),
                ),
                AliasAlternate(
                    "causal_data", "product_id",
                    "DUNNHUMBY.md L158",
                    kinds=frozenset({"transactions"}),
                ),
            ),
        ),
    ),
    "DEPARTMENT": _entries(
        AliasTarget(
            "products", "department",
            "segments-schema-mapping.md L328 (MODERATE)",
            kinds=frozenset({"products", "transactions"}),
        ),
    ),
    "BRAND": _entries(
        AliasTarget(
            "products", "brand",
            "segments-schema-mapping.md L329 (MODERATE)",
            kinds=frozenset({"products", "transactions"}),
        ),
        AliasTarget(
            "events", "brand",
            "segments-schema-mapping.md L51 (behavioural brand signal)",
            kinds=frozenset({"events"}),
        ),
    ),
    "COMMODITY_DESC": _entries(
        AliasTarget(
            "products", "commodity_desc",
            "segments-schema-mapping.md L330 (MODERATE)",
            kinds=frozenset({"products", "transactions"}),
        ),
    ),

    # --- Online Retail II --------------------------------------------------
    # 'Customer ID' (Online Retail II) and 'customer_id' (coupon dataset)
    # normalize to the same key — one entry covers both documented rows.
    "customer_id": _entries(
        AliasTarget(
            "customers", "source_customer_id",
            "segments-schema-mapping.md L99 (Online Retail II) and L155 (coupon)",
            kinds=frozenset({"customers", "orders", "transactions", "coupons", "messages"}),
            alternates=(
                AliasAlternate(
                    "coupon_redemptions", "customer_id",
                    "segments-schema-mapping.md L158",
                    kinds=frozenset({"coupons"}),
                ),
            ),
        ),
    ),
    "Invoice": _entries(
        AliasTarget(
            "orders", "source_order_id",
            "segments-schema-mapping.md L100",
            kinds=frozenset({"orders"}),
        ),
    ),
    "InvoiceDate": _entries(
        AliasTarget(
            "orders", "order_date",
            "segments-schema-mapping.md L101",
            kinds=frozenset({"orders"}),
            alternates=(
                AliasAlternate(
                    "orders", "order_timestamp",
                    "BEHAVIOR_AND_RETAIL.md L108: real time-of-day kept "
                    "(Open Decision #5)",
                    kinds=frozenset({"orders"}),
                    requires=(REQUIRES_CALCULATION,),
                ),
            ),
        ),
    ),
    "StockCode": _entries(
        AliasTarget(
            "products", "source_product_id",
            "segments-schema-mapping.md L102",
            kinds=frozenset({"orders", "products"}),
        ),
    ),
    "Description": _entries(
        AliasTarget(
            "products", "name",
            "segments-schema-mapping.md L103",
            kinds=frozenset({"products", "orders"}),
        ),
        AliasTarget(
            "coupon_campaigns", "campaign_type",
            "dunnhumby.py L479: campaign_desc DESCRIPTION -> campaign_type",
            kinds=frozenset({"coupons", "campaigns"}),
        ),
    ),
    "Price": _entries(
        AliasTarget(
            "order_items", "unit_price",
            "segments-schema-mapping.md L105",
            kinds=frozenset({"orders", "transactions"}),
        ),
        AliasTarget(
            "events", "price",
            "segments-schema-mapping.md L52",
            kinds=frozenset({"events"}),
        ),
    ),

    # --- Coupon redemption dataset ----------------------------------------
    "selling_price": _entries(
        AliasTarget(
            "order_items", "sales_value",
            "segments-schema-mapping.md L166 names target net_sales_value",
            kinds=frozenset({"coupons", "transactions"}),
            decision_note=(
                "Open Decision #9/#10: segments-schema-mapping.md L166/L177 uses "
                "target order_items.net_sales_value (schema.sql and all 17 Revenue "
                "KPI cards use sales_value), and whether selling_price is net or "
                "gross is unverified (segments L186). Target follows schema.sql — "
                "reviewer must confirm."
            ),
        ),
    ),
    "other_discount": _entries(
        AliasTarget(
            "order_items", "other_discount",
            "segments-schema-mapping.md L167",
            kinds=frozenset({"coupons", "transactions"}),
        ),
    ),
    "coupon_discount": _entries(
        AliasTarget(
            "order_items", "coupon_disc",
            "segments-schema-mapping.md L168 names target coupon_discount",
            kinds=frozenset({"coupons", "transactions"}),
            decision_note=(
                "segments-schema-mapping.md L168 names the target "
                "order_items.coupon_discount; schema.sql and kpi-schema-mapping.md "
                "(cards 2/10/11) use order_items.coupon_disc. Target follows "
                "schema.sql — reviewer must confirm."
            ),
        ),
    ),
    "redemption_status": _entries(
        AliasTarget(
            "coupon_redemptions", "redemption_status",
            "segments-schema-mapping.md L159",
            kinds=frozenset({"coupons"}),
        ),
    ),
    "campaign_id": _entries(
        AliasTarget(
            "coupon_campaigns", "source_campaign_id",
            "segments-schema-mapping.md L156",
            kinds=frozenset({"coupons", "campaigns"}),
            alternates=(
                AliasAlternate(
                    "coupon_redemptions", "campaign_id",
                    "dunnhumby.py L494: CAMPAIGN -> campaign_id",
                    kinds=frozenset({"coupons"}),
                ),
            ),
        ),
        AliasTarget(
            "marketing_campaigns", "source_campaign_id",
            "segments-schema-mapping.md L253",
            kinds=frozenset({"campaigns", "messages"}),
        ),
    ),
    "campaign": _entries(
        AliasTarget(
            "coupon_campaigns", "source_campaign_id",
            "dunnhumby.py L452: CAMPAIGN -> source_campaign_id",
            kinds=frozenset({"coupons", "transactions", "campaigns"}),
            alternates=(
                AliasAlternate(
                    "coupon_redemptions", "campaign_id",
                    "dunnhumby.py L494",
                    kinds=frozenset({"coupons", "transactions"}),
                ),
            ),
        ),
    ),
    "campaign_type": _entries(
        AliasTarget(
            "coupon_campaigns", "campaign_type",
            "segments-schema-mapping.md L160",
            kinds=frozenset({"coupons", "campaigns"}),
        ),
        AliasTarget(
            "marketing_campaigns", "campaign_type",
            "segments-schema-mapping.md L254",
            kinds=frozenset({"campaigns"}),
        ),
    ),
    "start_date": _entries(
        AliasTarget(
            "coupon_campaigns", "start_date",
            "segments-schema-mapping.md L161",
            kinds=frozenset({"coupons", "campaigns"}),
        ),
    ),
    "end_date": _entries(
        AliasTarget(
            "coupon_campaigns", "end_date",
            "segments-schema-mapping.md L162",
            kinds=frozenset({"coupons", "campaigns"}),
        ),
    ),
    "date": _entries(
        AliasTarget(
            "orders", "order_date",
            "segments-schema-mapping.md L163 (coupon transactions -> orders)",
            kinds=frozenset({"coupons", "transactions"}),
        ),
        AliasTarget(
            "holidays", "date",
            "segments-schema-mapping.md L274",
            kinds=frozenset({"holidays"}),
        ),
    ),
    "holiday": _entries(
        AliasTarget(
            "holidays", "holiday",
            "segments-schema-mapping.md L275",
            kinds=frozenset({"holidays"}),
        ),
    ),
    "item_id": _entries(
        AliasTarget(
            "products", "source_product_id",
            "segments-schema-mapping.md L164",
            kinds=frozenset({"coupons"}),
        ),
    ),
    "category": _entries(
        AliasTarget(
            "products", "category",
            "segments-schema-mapping.md L169",
            kinds=frozenset({"coupons", "products"}),
        ),
    ),
    "COUPON_UPC": _entries(
        AliasTarget(
            "coupon_redemptions", "coupon_id",
            "dunnhumby.py L493: COUPON_UPC -> coupon_id",
            kinds=frozenset({"coupons", "transactions"}),
        ),
    ),

    # --- Direct messaging dataset -----------------------------------------
    "client_id": _entries(
        AliasTarget(
            "customers", "source_customer_id",
            "segments-schema-mapping.md L252",
            kinds=frozenset({"customers", "campaigns", "messages"}),
            alternates=(
                AliasAlternate(
                    "message_sends", "customer_id",
                    "segments-schema-mapping.md L261",
                    kinds=frozenset({"messages"}),
                ),
            ),
        ),
    ),
    "first_purchase_date": _entries(
        AliasTarget(
            "customers", "first_purchase_date",
            "segments-schema-mapping.md L273",
            kinds=frozenset({"campaigns", "messages", "customers"}),
        ),
    ),
    "message_id": _entries(
        AliasTarget(
            "message_sends", "source_message_id",
            "segments-schema-mapping.md L260",
            kinds=frozenset({"messages"}),
        ),
    ),
    "message_type": _entries(
        AliasTarget(
            "message_sends", "message_type",
            "segments-schema-mapping.md L263",
            kinds=frozenset({"messages"}),
        ),
    ),
    "channel": _entries(
        AliasTarget(
            "marketing_campaigns", "channel",
            "segments-schema-mapping.md L255 (campaign channel)",
            kinds=frozenset({"campaigns"}),
        ),
        AliasTarget(
            "message_sends", "channel",
            "segments-schema-mapping.md L264 (message channel)",
            kinds=frozenset({"messages"}),
        ),
    ),
    "topic": _entries(
        AliasTarget(
            "marketing_campaigns", "topic",
            "segments-schema-mapping.md L256",
            kinds=frozenset({"campaigns"}),
        ),
    ),
    "started_at": _entries(
        AliasTarget(
            "marketing_campaigns", "started_at",
            "segments-schema-mapping.md L257",
            kinds=frozenset({"campaigns"}),
        ),
    ),
    "finished_at": _entries(
        AliasTarget(
            "marketing_campaigns", "finished_at",
            "segments-schema-mapping.md L258",
            kinds=frozenset({"campaigns"}),
        ),
    ),
    "total_count": _entries(
        AliasTarget(
            "marketing_campaigns", "total_count",
            "segments-schema-mapping.md L259",
            kinds=frozenset({"campaigns"}),
        ),
    ),
    "sent_at": _entries(
        AliasTarget(
            "message_sends", "sent_at",
            "segments-schema-mapping.md L265",
            kinds=frozenset({"messages"}),
        ),
    ),

    # --- Behaviour / events dataset ---------------------------------------
    "user_id": _entries(
        AliasTarget(
            "events", "customer_id",
            "segments-schema-mapping.md L53 (ESSENTIAL)",
            kinds=frozenset({"events"}),
            requires=(REQUIRES_RELATIONSHIP,),
            alternates=(
                AliasAlternate(
                    "customers", "source_customer_id",
                    "behavior.py L374-382: user_id roster -> customers",
                    kinds=frozenset({"events"}),
                ),
            ),
        ),
    ),
    "user_session": _entries(
        AliasTarget(
            "events", "session_id",
            "segments-schema-mapping.md L54 (ESSENTIAL)",
            kinds=frozenset({"events"}),
        ),
    ),
    "event_time": _entries(
        AliasTarget(
            "events", "event_timestamp",
            "segments-schema-mapping.md L55 (ESSENTIAL)",
            kinds=frozenset({"events"}),
        ),
    ),
    "event_type": _entries(
        AliasTarget(
            "events", "event_type",
            "segments-schema-mapping.md L47 (core signal)",
            kinds=frozenset({"events"}),
        ),
    ),
    "category_id": _entries(
        AliasTarget(
            "events", "category_id",
            "segments-schema-mapping.md L49",
            kinds=frozenset({"events"}),
        ),
    ),
    "category_code": _entries(
        AliasTarget(
            "events", "category_code",
            "segments-schema-mapping.md L50",
            kinds=frozenset({"events"}),
        ),
    ),

    # --- Churn dataset (source spelling of a schema column) ---------------
    "PreferedOrderCat": _entries(
        AliasTarget(
            "customers", "preferred_order_category",
            "final-schema.md L215 marks the column [S]; segments-schema-mapping.md "
            "L222 lists the source PreferedOrderCat as NOT needed for Segment 4",
            kinds=frozenset({"customers"}),
            decision_note=(
                "No mapping document assigns a source column to "
                "customers.preferred_order_category (segments L222 excludes it), "
                "yet final-schema.md marks it [S] — reviewer must confirm."
            ),
        ),
    ),
}

for _key, _value in _RAW.items():
    _norm = normalize(_key)
    if _norm in ALIASES:
        raise RuntimeError(f"duplicate normalized alias key {_norm!r} from {_key!r}")
    ALIASES[_norm] = _value


def lookup(source_column: str) -> tuple[AliasTarget, ...]:
    """Alias entries for a source column name, or () when undocumented."""
    return ALIASES.get(normalize(source_column), ())


def alias_index_by_target() -> dict[tuple[str, str], AliasTarget]:
    """(table, column) -> first alias entry declaring it as primary target.

    Used to attach an entry's kind-scoped alternates when the LLM or the
    reviewer picks one of its targets for an ambiguous column.
    """
    index: dict[tuple[str, str], AliasTarget] = {}
    for entries in ALIASES.values():
        for entry in entries:
            index.setdefault((entry.table, entry.column), entry)
    return index


# Which source-entity kinds each canonical table is documented against.
# Used ONLY to narrow competing candidates when a file's kind is known;
# with kind "unknown" every candidate survives and ambiguity is reported.
TABLE_KINDS: dict[str, frozenset[str]] = {
    "customers": frozenset({"customers", "transactions"}),
    "products": frozenset({"products", "transactions", "orders", "coupons"}),
    "orders": frozenset({"orders", "transactions"}),  # coupon files reach orders.* via aliases only
    "order_items": frozenset({"orders", "transactions", "coupons"}),
    "events": frozenset({"events"}),
    "marketing_campaigns": frozenset({"campaigns"}),
    "message_sends": frozenset({"messages"}),
    "coupon_campaigns": frozenset({"coupons", "campaigns"}),
    "coupon_redemptions": frozenset({"coupons"}),
    "holidays": frozenset({"holidays"}),
    "causal_data": frozenset({"transactions"}),
}


def kind_matches(kind: str, table: str, entry_kinds: frozenset[str] | None) -> bool:
    """Does this candidate survive filtering under the detected file kind?"""
    if kind == "unknown":
        return True
    if entry_kinds is not None:
        return kind in entry_kinds
    return kind in TABLE_KINDS.get(table, frozenset())


def candidates_as_dicts(candidates: list[Any]) -> list[dict[str, Any]]:
    return [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in candidates]
