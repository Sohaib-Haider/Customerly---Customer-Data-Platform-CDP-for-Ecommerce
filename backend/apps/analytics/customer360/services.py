"""Assemble customer data without scoring models or changing stored values."""
from datetime import date, datetime
from decimal import Decimal
import json
from uuid import UUID

from . import selectors
from ..kpis.sources import SOURCES, SYNTHETIC_ORDERS


def source_info(code):
    source = SOURCES.get(code)
    return {"code": code, "name": source.name if source else None,
            "currency": source.currency if source else None}


def source_code(identifier):
    return identifier.split(":", 1)[0] if ":" in identifier else "unknown"


def json_values(value):
    """Preserve financial precision and source-local date/time representations."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_values(item) for item in value]
    return value


def customer_list(store_id, **query):
    result = selectors.customers(store_id, **query)
    for customer in result["results"]:
        customer["source"] = source_info(source_code(customer["source_customer_id"]))
    return result


def customer360(store_id, customer_id):
    customer = selectors.profile(store_id, customer_id)
    purchases = selectors.purchase_summary(store_id, customer_id)
    for summary in purchases:
        summary["source"] = source_info(summary["source"])
        summary["average_order_value_unavailable_reason"] = None
        if summary["source"]["code"] == "cpn":
            summary["average_order_value"] = None
            summary["average_order_value_unavailable_reason"] = SYNTHETIC_ORDERS
    saved_segments = selectors.segments(store_id, customer_id)
    # Django's raw cursor returns JSONField storage as text on some backends.
    for prediction in saved_segments["predictions"]:
        if isinstance(prediction["result"], str):
            prediction["result"] = json.loads(prediction["result"])
    snapshot = selectors.features(store_id, customer_id)
    if snapshot and isinstance(snapshot.get("purchase_by_week"), str):
        snapshot["purchase_by_week"] = json.loads(snapshot["purchase_by_week"])
    return {
        "customer": customer,
        "source": source_info(source_code(customer["source_customer_id"])),
        "purchases": {"status": "available" if purchases else "no_records", "by_source": purchases},
        "activity": selectors.activity_summary(store_id, customer_id),
        "segments": saved_segments,
        "feature_snapshot": {"status": "stored" if snapshot else "not_computed", "data": snapshot},
        "notes": [
            "Revenue is actual net sales over the observed data window; predictions are separate.",
            "No records means no stored evidence, not proof that the customer has never taken that action.",
            "Stored features and predictions may be older than the latest uploaded data; check their timestamps.",
            "Message purchase flags are not attributed order revenue. Coupon records may contain only redemptions.",
        ],
    }
