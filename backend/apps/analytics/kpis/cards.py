"""The 19 FYP1 KPI cards.

Scope and numbering follow the "FYP KPI scope" section of
docs/notes/kpi-notes/kpi-schema-mapping.md. Every query returns a ``source``
column (dataset namespace); the engine groups rows by it, keeps the sources a
card supports and reports the rest as unavailable with a reason.

Conventions
-----------
* ``%(store_id)s`` scopes every query to one tenant (NULL = all stores).
* A *purchase order* is an order whose net line total is positive. Online
  Retail II cancellations (negative totals) still net into revenue, but are not
  counted as orders or used for order averages.
* Rates are percentages rounded to two decimals.
* Dunnhumby's non-merchandise departments (fuel kiosk, miscellaneous sales
  transactions) store meter-style values in ``quantity``. Their sales count as
  revenue, but they are excluded from unit counts and product rankings.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .sources import NO_ORDERS, SYNTHETIC_ORDERS, ns

VALUE, SERIES, TABLE = "value", "series", "table"

STORE = "(%(store_id)s::uuid IS NULL OR {alias}.store_id = %(store_id)s::uuid)"

NON_MERCHANDISE = "('KIOSK-GAS', 'MISC SALES TRAN')"
IS_MERCHANDISE = f"coalesce(p.department, '') NOT IN {NON_MERCHANDISE}"

ORDER_TOTALS = f"""
order_totals AS (
    SELECT o.id, o.customer_id, o.order_date, o.week_no, o.region,
           {ns('o.source_order_id')} AS source,
           sum(oi.sales_value) AS total,
           sum(oi.quantity) FILTER (WHERE {IS_MERCHANDISE}) AS units,
           count(*) AS lines
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
    LEFT JOIN products p ON p.id = oi.product_id
    WHERE {STORE.format(alias='o')}
    GROUP BY o.id
)"""

CUSTOMER_TOTALS = f"""
{ORDER_TOTALS},
customer_totals AS (
    SELECT source, customer_id,
           sum(total) AS revenue,
           count(*) FILTER (WHERE total > 0) AS purchase_orders
    FROM order_totals
    GROUP BY source, customer_id
)"""


@dataclass(frozen=True)
class Card:
    id: int
    key: str
    name: str
    category: str
    kind: str  # value | series | table
    unit: str  # currency | percent | count | number | mixed
    sources: tuple[str, ...]
    sql: str
    unavailable: dict[str, str] = field(default_factory=dict)
    note: str = ""
    dimensions: tuple[str, ...] = ()  # optional breakdowns ("filters")


# Breakdowns offered as filters on cards 1 and 12 (merged KPI cards).
ORDER_DIMENSIONS = {
    "month": "date_trunc('month', o.order_date)::date",
    "week": "o.week_no",
    "region": "o.region",
}
ITEM_DIMENSIONS = {
    "brand": "p.brand",
    "department": "p.department",
    "category": "p.category",
}

CARDS: list[Card] = [
    # ---------------------------------------------------------------- Revenue
    Card(
        1, "total_revenue", "Total Revenue", "Revenue", VALUE, "currency",
        ("ret", "dnb", "cpn"),
        f"""WITH {ORDER_TOTALS}
        SELECT source, round(sum(total), 2) AS revenue,
               count(*) FILTER (WHERE total > 0) AS purchase_orders,
               count(*) FILTER (WHERE total <= 0) AS return_orders
        FROM order_totals GROUP BY source""",
        note="Net sales value as recorded by each source; returns are netted in.",
        dimensions=tuple(ORDER_DIMENSIONS) + tuple(ITEM_DIMENSIONS),
    ),
    Card(
        2, "revenue_by_month", "Revenue by Month", "Revenue", SERIES, "currency",
        ("ret", "dnb", "cpn"),
        f"""WITH {ORDER_TOTALS}
        SELECT source, date_trunc('month', order_date)::date AS month,
               round(sum(total), 2) AS revenue
        FROM order_totals GROUP BY source, month ORDER BY source, month""",
    ),
    Card(
        3, "average_order_value", "Average Order Value", "Revenue", VALUE, "currency",
        ("ret", "dnb"),
        f"""WITH {ORDER_TOTALS}
        SELECT source, round(avg(total), 2) AS average_order_value,
               count(*) AS purchase_orders
        FROM order_totals WHERE total > 0 GROUP BY source""",
        unavailable={"cpn": SYNTHETIC_ORDERS},
    ),
    Card(
        4, "historical_clv", "Historical CLV (Actual)", "Revenue", VALUE, "currency",
        ("ret",),
        f"""WITH {CUSTOMER_TOTALS}
        SELECT source, round(avg(revenue), 2) AS average_clv,
               round((percentile_cont(0.5) WITHIN GROUP (ORDER BY revenue))::numeric, 2) AS median_clv,
               count(*) AS customers
        FROM customer_totals WHERE purchase_orders > 0 GROUP BY source""",
        unavailable={
            "dnb": "FYP1 scope computes CLV on the Online Retail II population (the CLV dataset).",
            "cpn": SYNTHETIC_ORDERS,
        },
        note="Actual revenue per customer over the observed window (not the ML prediction).",
    ),
    Card(
        5, "top20_revenue_share", "Top 20% Customers' Revenue Share", "Revenue", VALUE, "percent",
        ("ret",),
        f"""WITH {CUSTOMER_TOTALS},
        ranked AS (
            SELECT source, revenue,
                   row_number() OVER (PARTITION BY source ORDER BY revenue DESC) AS rn,
                   count(*) OVER (PARTITION BY source) AS n
            FROM customer_totals WHERE revenue > 0
        )
        SELECT source,
               round(100.0 * sum(revenue) FILTER (WHERE rn <= ceil(0.2 * n)) / sum(revenue), 2) AS top20_share,
               max(n) AS customers
        FROM ranked GROUP BY source""",
        unavailable={
            "dnb": "FYP1 scope computes this on the Online Retail II population (the CLV dataset).",
            "cpn": SYNTHETIC_ORDERS,
        },
    ),
    # --------------------------------------------------------------- Customer
    Card(
        6, "total_customers", "Total Customers", "Customer", VALUE, "count",
        ("ret", "dnb", "cpn", "evb", "msg", "chr"),
        f"""SELECT {ns('c.source_customer_id')} AS source, count(*) AS customers
        FROM customers c WHERE {STORE.format(alias='c')} GROUP BY 1""",
        note="Counts may be added across sources: populations are disjoint.",
    ),
    Card(
        7, "new_customers_by_month", "New Customers by Month", "Customer", SERIES, "count",
        ("ret", "dnb", "cpn"),
        f"""WITH {ORDER_TOTALS},
        firsts AS (
            SELECT source, customer_id, min(order_date) AS first_order
            FROM order_totals WHERE total > 0 GROUP BY source, customer_id
        )
        SELECT source, date_trunc('month', first_order)::date AS month, count(*) AS new_customers
        FROM firsts GROUP BY source, month ORDER BY source, month""",
        note="First purchase inside each source's observed window; the first month also contains earlier customers.",
    ),
    Card(
        8, "repeat_purchase_rate", "Repeat Purchase Rate", "Customer", VALUE, "percent",
        ("ret", "dnb"),
        f"""WITH {CUSTOMER_TOTALS}
        SELECT source,
               round(100.0 * count(*) FILTER (WHERE purchase_orders >= 2) / count(*), 2) AS repeat_rate,
               count(*) AS customers
        FROM customer_totals WHERE purchase_orders >= 1 GROUP BY source""",
        unavailable={"cpn": SYNTHETIC_ORDERS},
    ),
    Card(
        9, "churn_rate", "Customer Churn Rate", "Customer", VALUE, "percent",
        ("chr",),
        f"""SELECT {ns('c.source_customer_id')} AS source,
               round(100.0 * avg(c.churn::int), 2) AS churn_rate,
               count(*) AS customers
        FROM customers c WHERE {STORE.format(alias='c')} AND c.churn IS NOT NULL GROUP BY 1""",
        note="Actual churn label recorded in the source (not the ML churn-risk score).",
    ),
    Card(
        10, "churn_rate_by_category", "Churn Rate by Preferred Category", "Customer", TABLE, "percent",
        ("chr",),
        f"""SELECT {ns('c.source_customer_id')} AS source,
               c.preferred_order_category AS category,
               round(100.0 * avg(c.churn::int), 2) AS churn_rate,
               count(*) AS customers
        FROM customers c
        WHERE {STORE.format(alias='c')} AND c.churn IS NOT NULL AND c.preferred_order_category IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, churn_rate DESC""",
    ),
    Card(
        11, "satisfaction_and_complaints", "Avg Satisfaction & Complaint Rate", "Customer", VALUE, "mixed",
        ("chr",),
        f"""SELECT {ns('c.source_customer_id')} AS source,
               round(avg(c.satisfaction_score), 2) AS avg_satisfaction,
               round(100.0 * avg(c.complain::int), 2) AS complaint_rate,
               count(*) AS customers
        FROM customers c
        WHERE {STORE.format(alias='c')} AND (c.satisfaction_score IS NOT NULL OR c.complain IS NOT NULL)
        GROUP BY 1""",
        note="Satisfaction is on a 1-5 scale; complaint rate is a percentage.",
    ),
    # ------------------------------------------------------------------ Order
    Card(
        12, "total_orders", "Total Orders", "Order", VALUE, "count",
        ("ret", "dnb"),
        f"""WITH {ORDER_TOTALS}
        SELECT source, count(*) FILTER (WHERE total > 0) AS purchase_orders,
               count(*) FILTER (WHERE total <= 0) AS return_orders
        FROM order_totals GROUP BY source""",
        unavailable={"cpn": SYNTHETIC_ORDERS},
        dimensions=tuple(ORDER_DIMENSIONS),
    ),
    Card(
        13, "avg_items_per_order", "Avg Items per Order", "Order", VALUE, "number",
        ("ret", "dnb"),
        f"""WITH {ORDER_TOTALS}
        SELECT source, round(avg(units), 2) AS avg_units, round(avg(lines), 2) AS avg_lines,
               count(*) AS purchase_orders
        FROM order_totals WHERE total > 0 GROUP BY source""",
        unavailable={"cpn": SYNTHETIC_ORDERS},
        note="avg_units = units bought per order (excluding fuel and miscellaneous transactions); avg_lines = order lines per order.",
    ),
    # ---------------------------------------------------------------- Product
    Card(
        14, "top_products_by_revenue", "Top 10 Products by Revenue", "Product", TABLE, "currency",
        ("ret", "dnb"),
        f"""WITH product_revenue AS (
            SELECT {ns('o.source_order_id')} AS source, p.id,
                   split_part(p.source_product_id, ':', 2) AS product_id,
                   coalesce(p.name, p.source_product_id) AS product,
                   round(sum(oi.sales_value), 2) AS revenue,
                   sum(oi.quantity) AS units
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            JOIN products p ON p.id = oi.product_id
            WHERE {STORE.format(alias='o')} AND {IS_MERCHANDISE}
            GROUP BY 1, p.id
        ), ranked AS (
            SELECT *, row_number() OVER (PARTITION BY source ORDER BY revenue DESC) AS rank
            FROM product_revenue
        )
        SELECT source, rank, product_id, product, revenue, units FROM ranked WHERE rank <= 10 ORDER BY source, rank""",
        unavailable={"cpn": "Coupon-dataset products have no names (only item ids)."},
        note="Dunnhumby product names are commodity descriptions, so several products can share a name; product_id tells them apart. Fuel and miscellaneous transactions are excluded.",
    ),
    # --------------------------------------------------------- Event/Behavior
    Card(
        15, "conversion_funnel", "View > Cart > Purchase Funnel", "Event/Behavior", VALUE, "mixed",
        ("evb",),
        f"""SELECT {ns('c.source_customer_id')} AS source,
               count(*) FILTER (WHERE e.event_type = 'view') AS views,
               count(*) FILTER (WHERE e.event_type = 'cart') AS carts,
               count(*) FILTER (WHERE e.event_type = 'purchase') AS purchases,
               round(100.0 * count(*) FILTER (WHERE e.event_type = 'cart')
                     / nullif(count(*) FILTER (WHERE e.event_type = 'view'), 0), 2) AS view_to_cart_rate,
               round(100.0 * count(*) FILTER (WHERE e.event_type = 'purchase')
                     / nullif(count(*) FILTER (WHERE e.event_type = 'cart'), 0), 2) AS cart_to_purchase_rate,
               count(DISTINCT e.session_id) AS sessions
        FROM events e JOIN customers c ON c.id = e.customer_id
        WHERE {STORE.format(alias='e')}
        GROUP BY 1""",
        note="Event-level rates. A product can be bought without a recorded cart event, so cart-to-purchase can exceed 100%.",
    ),
    # ----------------------------------------------------- Marketing/Channel
    Card(
        16, "open_rate", "Open Rate (Email / Push)", "Marketing/Channel", TABLE, "percent",
        ("msg",),
        f"""SELECT {ns('m.source_message_id')} AS source, m.channel,
               round(100.0 * avg(m.is_opened::int), 2) AS open_rate, count(*) AS messages
        FROM message_sends m WHERE {STORE.format(alias='m')} AND m.is_opened IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2""",
        note="The messaging source has no SMS sends, so no SMS open rate.",
    ),
    Card(
        17, "click_through_rate", "Click-Through Rate", "Marketing/Channel", TABLE, "percent",
        ("msg",),
        f"""SELECT {ns('m.source_message_id')} AS source, m.channel,
               round(100.0 * avg(m.is_clicked::int), 2) AS click_through_rate, count(*) AS messages
        FROM message_sends m WHERE {STORE.format(alias='m')} AND m.is_clicked IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2""",
    ),
    Card(
        18, "campaign_conversion_rate", "Campaign Conversion Rate", "Marketing/Channel", TABLE, "percent",
        ("msg",),
        f"""SELECT {ns('m.source_message_id')} AS source, m.channel,
               round(100.0 * avg(m.is_purchased::int), 2) AS conversion_rate, count(*) AS messages
        FROM message_sends m WHERE {STORE.format(alias='m')} AND m.is_purchased IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2""",
        note="Purchase flag recorded by the messaging source; it cannot be linked to order revenue.",
    ),
    # ----------------------------------------------------------------- Coupon
    Card(
        19, "coupon_redemption_rate", "Coupon Redemption Rate", "Coupon", VALUE, "percent",
        ("cpn",),
        f"""SELECT {ns('c.source_customer_id')} AS source,
               round(100.0 * avg(r.redemption_status::int), 2) AS redemption_rate,
               count(*) FILTER (WHERE r.redemption_status) AS redeemed,
               count(*) AS offered
        FROM coupon_redemptions r JOIN customers c ON c.id = r.customer_id
        WHERE {STORE.format(alias='r')} GROUP BY 1""",
        unavailable={"dnb": "Dunnhumby records only redeemed coupons, not coupons offered, so the rate has no denominator."},
    ),
]

BY_ID = {card.id: card for card in CARDS}
BY_KEY = {card.key: card for card in CARDS}


def breakdown_sql(card: Card, dimension: str) -> str:
    """SQL for card 1 (revenue) or 12 (orders) broken down by one dimension."""
    if dimension not in card.dimensions:
        raise ValueError(f"card {card.id} has no '{dimension}' breakdown; options: {', '.join(card.dimensions)}")
    if dimension in ORDER_DIMENSIONS:
        expr = ORDER_DIMENSIONS[dimension]
        measure = (
            "round(sum(total), 2) AS revenue" if card.key == "total_revenue"
            else "count(*) FILTER (WHERE total > 0) AS purchase_orders"
        )
        return f"""WITH {ORDER_TOTALS.replace('o.region,', f'o.region, {expr} AS dim,')}
        SELECT source, dim AS {dimension}, {measure}
        FROM order_totals WHERE dim IS NOT NULL GROUP BY source, dim
        ORDER BY source, {"dim" if dimension in ("month", "week") else "3 DESC"}"""
    expr = ITEM_DIMENSIONS[dimension]
    return f"""SELECT {ns('o.source_order_id')} AS source, {expr} AS {dimension},
           round(sum(oi.sales_value), 2) AS revenue
    FROM order_items oi
    JOIN orders o ON o.id = oi.order_id
    JOIN products p ON p.id = oi.product_id
    WHERE {STORE.format(alias='o')} AND {expr} IS NOT NULL
    GROUP BY 1, 2 ORDER BY 1, 3 DESC"""
