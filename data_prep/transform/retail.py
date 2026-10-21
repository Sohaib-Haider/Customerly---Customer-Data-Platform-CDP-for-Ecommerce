"""Online Retail II -> canonical schema.

Member B, part 2 of 2. This dataset feeds Future High-Value / CLV (Segment 2);
its canonical targets are `customers`, `products`, `orders` and `order_items`
(segments-schema-mapping.md Segment 2).

Column mappings are taken from:
  docs/notes/customer segmentation-notes/segments-schema-mapping.md  (Segment 2)
  docs/notes/customer segmentation-notes/segments/Future High-Value - CLV.md
  docs/notes/schema-notes/final-schema.md                            (target shape)

Two source shapes are accepted, because the file is distributed both ways:
  * UCI `online_retail_II.xlsx`  -> Invoice / Price / Customer ID   (the documented names)
  * Kaggle `online_retail_II.csv` -> InvoiceNo / UnitPrice / CustomerID
The two are normalised to the documented names before mapping.

`Country` is deliberately NOT mapped to `orders.region`: Segment 2 excludes it
("Not needed for this segment (no segment/KPI uses geo)") and the only geo
column in any source is unresolved — final-schema.md Open Decision #4. Left NULL.

NOT a source for this work: docs/decisions/data-transformation-plan.md
(excluded by its own header).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from common import (
    SHARED_WINDOW_START,
    shift_date,
    source_id,
    to_float,
    to_int,
    uid,
)

DATASET = "online_retail_ii"

RAW_FILES = ["online_retail_II.csv"]
RAW_FILES_XLSX = ["online_retail_II.xlsx"]

# Kaggle-name -> documented-name. Both spellings exist in the wild; the real
# header wins (kaggle-dataset-files skill rule), so this is a rename of the
# Kaggle variant onto the documented one, never a guess.
_COLUMN_ALIASES = {
    "InvoiceNo": "Invoice",
    "UnitPrice": "Price",
    "CustomerID": "Customer ID",
}

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
        "preferred_order_category", "email", "phone_number",
        "churn", "created_at", "updated_at",
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
}


@dataclass
class TransformResult:
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    dropped_by_source: dict[str, int] = field(default_factory=dict)
    raw_counts: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, count: int, source: str = "online_retail_II") -> None:
        if count:
            self.dropped[reason] = self.dropped.get(reason, 0) + count
            self.dropped_by_source[source] = self.dropped_by_source.get(source, 0) + count

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def resolve_raw_file(raw_dir) -> Path:
    raw_dir = Path(raw_dir)
    for candidate in (RAW_FILES_XLSX, RAW_FILES):
        path = raw_dir / candidate[0]
        if path.exists():
            return path
    raise FileNotFoundError(
        f"No Online Retail II source in {raw_dir}. Expected online_retail_II.xlsx "
        "(UCI) or online_retail_II.csv (Kaggle). Download from "
        "https://archive.ics.uci.edu/dataset/502/online+retail+ii or Kaggle "
        "mashlyn/online-retail-ii-uci."
    )


def load_raw(raw_dir) -> dict[str, pd.DataFrame]:
    """Read the source (xlsx sheets or csv) and normalise its column names."""
    path = resolve_raw_file(raw_dir)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        sheets = pd.read_excel(path, sheet_name=None)
        frame = pd.concat(sheets.values(), ignore_index=True)
        source_name = f"{path.name} ({len(sheets)} sheets)"
    else:
        frame = pd.read_csv(path, dtype=str, low_memory=False)
        source_name = path.name

    renamed = {dst: src for src, dst in _COLUMN_ALIASES.items() if src in frame.columns}
    if renamed:
        frame = frame.rename(columns=renamed)
    return {source_name: frame}


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------
def transform(raw: dict[str, pd.DataFrame], store_id: str) -> TransformResult:
    """Run the Online Retail II transform and return canonical frames."""
    result = TransformResult()
    source_name, src = next(iter(raw.items()))
    result.raw_counts[source_name] = len(src)

    # ---- normalise types (ids as text, money/qty as numbers, dates as ts) --
    invoice = src["Invoice"].map(lambda v: source_id(v, DATASET))
    stock = src["StockCode"].map(lambda v: source_id(v, DATASET))
    customer = src["Customer ID"].map(lambda v: source_id(v, DATASET))
    quantity = src["Quantity"].map(to_int)
    unit_price = src["Price"].map(to_float)
    invoice_ts = pd.to_datetime(src["InvoiceDate"], utc=True, errors="coerce")

    # ---- drops: rows that cannot satisfy a NOT NULL or an FK ----------------
    result.drop("lines with empty Customer ID (customer_id NOT NULL)",
                int((customer == "").sum()), source=source_name)
    result.drop("lines with empty Invoice (source_order_id NOT NULL)",
                int((invoice == "").sum()), source=source_name)
    result.drop("lines with empty StockCode (product FK)",
                int((stock == "").sum()), source=source_name)
    result.drop("lines with unparseable InvoiceDate (order_date NOT NULL)",
                int(invoice_ts.isna().sum()), source=source_name)
    result.drop("lines with unparseable Quantity (quantity NOT NULL)",
                int(quantity.isna().sum()), source=source_name)
    result.drop("lines with unparseable Price (unit_price NOT NULL)",
                int(unit_price.isna().sum()), source=source_name)

    keep = (
        (customer != "") & (invoice != "") & (stock != "")
        & invoice_ts.notna() & quantity.notna() & unit_price.notna()
    )
    src = src[keep].copy()
    invoice, stock, customer = invoice[keep], stock[keep], customer[keep]
    quantity, unit_price, invoice_ts = quantity[keep], unit_price[keep], invoice_ts[keep]

    # ---- date alignment (real dates -> shared timeline) ---------------------
    offset = (SHARED_WINDOW_START - invoice_ts.min().date()).days
    order_date = [shift_date(t, offset).date() for t in invoice_ts]
    order_timestamp = [shift_date(t, offset) for t in invoice_ts]

    # ---- customers ---------------------------------------------------------
    cust_keys = sorted(set(customer))
    customers = pd.DataFrame({"source_customer_id": cust_keys})
    customers["id"] = customers["source_customer_id"].map(lambda k: uid("customers", k))
    for col in COLUMNS["customers"]:
        if col not in customers.columns and col != "store_id":
            customers[col] = None
    customers = customers[[c for c in COLUMNS["customers"] if c != "store_id"]]
    result.note(
        "customers.first_purchase_date left NULL — derivation would be aggregation; "
        "the platform computes it (DUNNHUMBY.md §7.1(d))"
    )

    # ---- products ----------------------------------------------------------
    # One row per StockCode. Description is the product name; where it is blank
    # the source id is used as a fallback (name is NOT NULL [S]). brand/category
    # have no source column in this dataset -> NULL (final-schema.md Open Decision
    # #11 is about unit_price; brand/category simply do not exist here).
    prod = pd.DataFrame({
        "source_product_id": stock.values,
        "name": src["Description"].values if "Description" in src.columns else None,
    })
    prod = prod.dropna(subset=["source_product_id"])
    first = prod.drop_duplicates(subset="source_product_id", keep="first")
    keys = first["source_product_id"].tolist()
    raw_names = first["name"].tolist()
    products = pd.DataFrame({"source_product_id": keys})
    products["id"] = products["source_product_id"].map(lambda k: uid("products", k))
    products["name"] = [_fallback_text(n, k) for n, k in zip(raw_names, keys)]
    products["brand"] = None
    products["category"] = None
    products["department"] = None
    products["commodity_desc"] = None
    products["unit_price"] = None
    products["created_at"] = None
    products["updated_at"] = None
    products = products[[c for c in COLUMNS["products"] if c != "store_id"]]
    result.note("products.unit_price left NULL — Open Decision #11 (price is line-level here)")
    result.note("products.brand/category left NULL — no brand/category column in this source")

    # ---- orders (one row per Invoice) --------------------------------------
    order_frame = pd.DataFrame({
        "_invoice": invoice.values,
        "_customer": customer.values,
        "_date": order_date,
        "_ts": order_timestamp,
    })
    grp = order_frame.groupby("_invoice", sort=False)
    orders = pd.DataFrame({
        "source_order_id": list(grp.groups.keys()),
        "_customer": grp["_customer"].first().values,
        "order_date": grp["_date"].first().values,
        "order_timestamp": grp["_ts"].first().values,
    })
    orders["id"] = orders["source_order_id"].map(lambda k: uid("orders", k))
    orders["customer_id"] = orders["_customer"].map(lambda k: uid("customers", k))
    orders["week_no"] = None
    orders["day_no"] = None
    orders["source_store_id"] = None
    orders["region"] = None  # Open Decision #4 — Country excluded by Segment 2
    orders["created_at"] = None
    orders = orders[[c for c in COLUMNS["orders"] if c != "store_id"]]
    dup_orders = int(orders["id"].duplicated().sum())
    if dup_orders:
        result.drop("duplicate Invoice order headers (kept first)", dup_orders, source=source_name)
        orders = orders.drop_duplicates(subset="id", keep="first")

    # ---- order_items (one row per line) ------------------------------------
    items = pd.DataFrame({
        "_invoice": invoice.values,
        "_product": stock.values,
        "quantity": quantity.values,
        "unit_price": unit_price.values,
    })
    items["_line"] = items.groupby(["_invoice", "_product"]).cumcount()
    items["id"] = [
        uid("order_items", f"{i}|{p}|{n}")
        for i, p, n in zip(items["_invoice"], items["_product"], items["_line"])
    ]
    items["order_id"] = items["_invoice"].map(lambda k: uid("orders", k))
    items["product_id"] = items["_product"].map(lambda k: uid("products", k))
    # quantity is INTEGER in schema.sql; the map() above upcast it to float
    # because of the None cells that have since been dropped.
    items["quantity"] = items["quantity"].astype("int64")
    # Segment 2 pricing note: this source has no discount columns, so the net
    # amount is quantity * unit_price (mapping L113/L124). Discounts stay 0.
    items["sales_value"] = [
        round(q * p, 4) if (q is not None and p is not None) else 0.0
        for q, p in zip(items["quantity"], items["unit_price"])
    ]
    items["retail_disc"] = 0.0
    items["coupon_disc"] = 0.0
    items["coupon_match_disc"] = 0.0
    items["other_discount"] = 0.0
    items = items[COLUMNS["order_items"]]

    result.frames = {
        "stores": _stores(store_id),
        "customers": _attach_store(customers, store_id),
        "products": _attach_store(products, store_id),
        "orders": _attach_store(orders, store_id),
        "order_items": items,
    }
    result.note(
        f"dates shifted by {offset} days so the earliest InvoiceDate "
        f"({invoice_ts.min().date()}) lands on {SHARED_WINDOW_START} (shared timeline)"
    )
    result.note(
        "orders.region left NULL — Segment 2 excludes Country; no geo source "
        "resolved (Open Decision #4)"
    )
    result.note(
        "cancellations and negative Quantity/Price are preserved as-is (real behaviour)"
    )
    return result


def _fallback_text(value, fallback: str) -> str:
    """Stripped text, or `fallback` when the cell is NaN/blank.

    Read with dtype=str, a blank Description arrives as NaN, and str(NaN) is the
    literal 'nan' — which must never become a product name.
    """
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or fallback


def _stores(store_id: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "id": store_id,
        "name": "Member B Demo Store",
        "plan": "pro",
        "created_at": None,
        "updated_at": None,
    }])[COLUMNS["stores"]]


def _attach_store(frame: pd.DataFrame, store_id: str) -> pd.DataFrame:
    if "store_id" in frame.columns:
        return frame
    out = frame.copy()
    out.insert(1 if "id" in out.columns else 0, "store_id", store_id)
    return out
