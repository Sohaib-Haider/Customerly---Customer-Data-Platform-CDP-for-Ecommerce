"""Prepare one customer's purchase lines exactly as the existing CLV notebook.

PurchaseLine fields are the public Python input contract, not database columns.
The caller must keep each customer, tenant and source population separate.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from numbers import Number

import numpy as np
import pandas as pd

from ...services.results import SegmentError
from .contract import EXCLUDED_CODES, FEATURES, HISTORY_DAYS


@dataclass(frozen=True)
class PurchaseLine:
    invoice_id: str
    product_code: str
    timestamp: object
    quantity: object
    unit_price: object


@dataclass(frozen=True)
class PreparedCLV:
    frame: pd.DataFrame
    observation_date: str
    valid_invoices: int
    excluded_lines: dict
    history_lines: int


def _identifier(value):
    # Do not cast numeric identifiers or lose leading zeros.
    return isinstance(value, str) and value.strip().lower() not in {"", "nan", "none", "null", "<na>", "nat"}


def _decimal(value):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        number = Decimal(str(value).strip())
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def observation_cutoff(value):
    if value is None or isinstance(value, Number):
        raise SegmentError("invalid_observation_date", "Provide an explicit source-local observation date.")
    try:
        cutoff = pd.Timestamp(value)
        if pd.isna(cutoff) or cutoff.tzinfo is not None or cutoff != cutoff.normalize():
            raise ValueError
        # Force bounds checking before arithmetic.
        cutoff - pd.Timedelta(days=HISTORY_DAYS)
        return cutoff
    except (ValueError, TypeError, OverflowError):
        raise SegmentError("invalid_observation_date", "Observation date must be a timezone-free calendar date at midnight.") from None


def prepare_features(purchases, observation_date):
    cutoff = observation_cutoff(observation_date)
    if not isinstance(purchases, (list, tuple)) or any(not isinstance(line, PurchaseLine) for line in purchases):
        raise SegmentError("invalid_purchase_input", "Provide a list or tuple of PurchaseLine records for one customer.")
    rows = []
    excluded = Counter()
    for line in purchases:
        if not _identifier(line.invoice_id) or not _identifier(line.product_code):
            excluded["missing_identifier"] += 1
            continue
        invoice, product = line.invoice_id.strip(), line.product_code.strip()
        if product.upper() in EXCLUDED_CODES:
            excluded["excluded_product_code"] += 1
            continue
        if invoice.upper().startswith("C"):
            excluded["cancellation"] += 1
            continue
        try:
            if line.timestamp is None or isinstance(line.timestamp, Number):
                raise ValueError
            timestamp = pd.Timestamp(line.timestamp)
            if pd.isna(timestamp) or timestamp.tzinfo is not None:
                raise ValueError
            date = timestamp.normalize()
        except (TypeError, ValueError, OverflowError):
            excluded["invalid_date"] += 1
            continue
        quantity, price = _decimal(line.quantity), _decimal(line.unit_price)
        if quantity is None or quantity <= 0 or quantity != quantity.to_integral_value():
            excluded["invalid_quantity"] += 1
            continue
        if price is None or price <= 0:
            excluded["invalid_price"] += 1
            continue
        try:
            revenue = float(quantity * price)
            finite = math.isfinite(revenue) and math.isfinite(float(quantity)) and math.isfinite(float(price))
        except (InvalidOperation, OverflowError, ValueError):
            finite = False
        if not finite:
            excluded["invalid_amount"] += 1
            continue
        rows.append({"invoice": invoice, "product": product, "date": date, "quantity": float(quantity), "revenue": revenue})

    lines = pd.DataFrame(rows, columns=["invoice", "product", "date", "quantity", "revenue"])
    if not lines.empty and lines.groupby("invoice")["date"].nunique().gt(1).any():
        raise SegmentError("conflicting_invoice", "A purchase invoice contains conflicting calendar dates.")
    if not lines.empty:
        in_history = (lines["date"] >= cutoff - pd.Timedelta(days=HISTORY_DAYS)) & (lines["date"] < cutoff)
        excluded["outside_history_window"] += int((~in_history).sum())
        lines = lines.loc[in_history]
    if lines.empty:
        return PreparedCLV(pd.DataFrame(columns=FEATURES, dtype=float), cutoff.date().isoformat(), 0, dict(excluded), 0)

    invoices = lines.groupby(["invoice", "date"], as_index=False).agg(order_value=("revenue", "sum"))
    invoices = invoices.sort_values(["date", "invoice"])
    gaps = invoices["date"].diff().dt.days
    monetary = invoices["order_value"].sum()
    frequency = invoices["invoice"].nunique()
    features = {
        "recency": (cutoff - invoices["date"].max()).days,
        "frequency": frequency,
        "monetary": monetary,
        "avg_order_value": monetary / frequency,
        "customer_tenure": (cutoff - invoices["date"].min()).days,
        "total_items_bought": lines["quantity"].sum(),
        "unique_products_bought": lines["product"].nunique(),
        "max_single_order_value": invoices["order_value"].max(),
        "min_single_order_value": invoices["order_value"].min(),
        "avg_days_between_orders": gaps.mean(),
        "purchase_regularity": gaps.std(ddof=1),
    }
    frame = pd.DataFrame([features], columns=FEATURES, dtype=float)
    return PreparedCLV(frame, cutoff.date().isoformat(), int(frequency), dict(excluded), len(lines))
