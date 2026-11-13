"""Existing CLV training contract; these labels do not describe database tables."""

from ...services.eligibility import EligibilityPolicy, HistoryRequirement, ValueRequirement
from ...services.validation import numeric

FEATURES = (
    "recency", "frequency", "monetary", "avg_order_value", "customer_tenure",
    "total_items_bought", "unique_products_bought", "max_single_order_value",
    "min_single_order_value", "avg_days_between_orders", "purchase_regularity",
)
HISTORY_DAYS = 288
HORIZON_DAYS = 90
CURRENCY = "GBP"
EXCLUDED_CODES = frozenset({"B", "ADJUST", "ADJUST2", "BANK CHARGES", "AMAZONFEE", "CRUK", "D", "M", "TEST001", "TEST002"})
PREDICTION_POLICY = "np.maximum(0.0, pipeline.predict(frame[features])); required after loading"
POLICY = EligibilityPolicy(
    values=tuple(ValueRequirement(name, allow_missing=name in {"avg_days_between_orders", "purchase_regularity"}, validator=numeric) for name in FEATURES),
    histories=(HistoryRequirement("valid_purchase_invoices"),),
)
