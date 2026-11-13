"""Source populations in the demo tenant.

Every canonical row carries its source dataset as the namespace prefix of its
source identifier (``ret:``, ``dnb:`` ...). The six populations are distinct
customers, use different currencies and cover different time windows, so
monetary KPIs are always reported per source and never summed across sources
(see "Source-data feasibility rules" in docs/notes/kpi-notes/kpi-schema-mapping.md).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    code: str
    name: str
    currency: str | None  # None: not a monetary population, or currency unverified


SOURCES: dict[str, Source] = {
    s.code: s
    for s in (
        Source("ret", "Online Retail II", "GBP"),
        Source("dnb", "Dunnhumby: The Complete Journey", "USD"),
        Source("cpn", "Predicting Coupon Redemption", None),
        Source("evb", "eCommerce behavior events", None),
        Source("msg", "Direct Messaging", None),
        Source("chr", "E-commerce Customer Churn", None),
    )
}

# Reasons reused by several cards.
SYNTHETIC_ORDERS = (
    "Coupon-dataset orders are one transaction line each, not real baskets; "
    "basket-level metrics would be meaningless."
)
NO_ORDERS = "This source has no orders."


def ns(column: str) -> str:
    """SQL expression extracting the source namespace from a source id column."""
    return f"split_part({column}, ':', 1)"
