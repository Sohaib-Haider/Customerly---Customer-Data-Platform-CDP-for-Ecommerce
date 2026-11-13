"""Tenant-scoped source reads; no source customer/order data is modified."""

from datetime import timedelta
from decimal import Decimal
import uuid

from django.db import connection
from django.db.models import UUIDField

from ..models import Customer
from ..predictors.clv.features import PurchaseLine, observation_cutoff
from .results import SegmentError

CHURN_FIELDS = {
    "Tenure": "tenure", "WarehouseToHome": "warehouse_to_home",
    "NumberOfDeviceRegistered": "number_of_devices_registered",
    "SatisfactionScore": "satisfaction_score", "Complain": "complain", "CashbackAmount": "cashback_amount",
}
DEMO_PREFIXES = {"chr", "cpn", "dnb", "evb", "msg", "ret"}
MAX_PURCHASE_LINES = 50000


def db_uuid(value):
    return UUIDField().get_db_prep_value(value, connection)


def get_customer(store_id, customer_id):
    return Customer.objects.get(store_id=store_id, id=customer_id)


def churn_inputs(customer):
    return {model_input: getattr(customer, column) for model_input, column in CHURN_FIELDS.items()}


def clv_purchases(customer, observation_date):
    prefix = customer.source_customer_id.split(":", 1)[0]
    if prefix in DEMO_PREFIXES and prefix != "ret":
        raise SegmentError("unsupported_population", "The current CLV model cannot mix this demo source population with Retail GBP history.")
    cutoff = observation_cutoff(observation_date)
    params = [db_uuid(customer.store_id),
              db_uuid(customer.id),
              (cutoff - timedelta(days=288)).date(), cutoff.date(), MAX_PURCHASE_LINES + 1]
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT o.source_order_id, p.source_product_id, o.order_date,
                   i.source_timestamp, i.quantity, i.unit_price, i.sales_value, p.store_id
            FROM orders o
            JOIN order_items i ON i.order_id = o.id
            JOIN products p ON p.id = i.product_id
            WHERE o.store_id = %s AND o.customer_id = %s
              AND o.order_date >= %s AND o.order_date < %s
            ORDER BY o.order_date, o.source_order_id, i.id
            LIMIT %s
        """, params)
        rows = cursor.fetchall()
    if len(rows) > MAX_PURCHASE_LINES:
        raise SegmentError("history_limit_exceeded", "This customer exceeds the synchronous purchase-history limit; history was not truncated for prediction.")
    purchases = []
    for invoice, product, order_date, source_timestamp, quantity, price, sales_value, product_store_id in rows:
        if uuid.UUID(str(product_store_id)) != customer.store_id:
            raise SegmentError("inconsistent_tenant_history", "Purchase records contain a product outside the customer's store.")
        if price is None:
            raise SegmentError("missing_unit_price", "Purchase history lacks the original unit price required by this CLV contract.")
        numbers = [Decimal(str(value)) for value in (quantity, price, sales_value)]
        if not all(value.is_finite() for value in numbers) or numbers[0] * numbers[1] != numbers[2]:
            raise SegmentError("inconsistent_purchase_amount", "Purchase amounts do not match the model's quantity-times-price contract.")
        if prefix == "ret" and (not invoice.startswith("ret:") or not product.startswith("ret:")):
            raise SegmentError("inconsistent_source_identity", "Purchase identifiers do not belong to the customer's Retail source population.")
        timestamp = source_timestamp or order_date
        if source_timestamp is not None:
            from pandas import Timestamp

            if Timestamp(source_timestamp).date() != Timestamp(order_date).date():
                raise SegmentError("inconsistent_purchase_date", "Purchase line and invoice calendar dates disagree.")
        purchases.append(PurchaseLine(invoice.removeprefix("ret:"), product.removeprefix("ret:"), timestamp, quantity, price))
    return purchases
