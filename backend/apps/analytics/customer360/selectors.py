"""Parameterized, tenant-scoped reads of the canonical schema. No writes."""
from django.db import connection
from django.http import Http404


def rows(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def profile(store_id, customer_id):
    result = rows("SELECT c.* FROM customers c WHERE c.store_id = %s AND c.id = %s",
                  [str(store_id), str(customer_id)])
    if not result:
        raise Http404
    return result[0]


def page(sql, params, *, limit, offset):
    count = rows(f"SELECT count(*) AS count FROM ({sql}) records", params)[0]["count"]
    # Each caller supplies a deterministic ORDER BY with the primary key last.
    return {"count": count, "limit": limit, "offset": offset,
            "results": rows(sql + " LIMIT %s OFFSET %s", [*params, limit, offset])}


def customers(store_id, *, limit, offset, search="", source=None):
    sql = "SELECT c.* FROM customers c WHERE c.store_id = %s"
    params = [str(store_id)]
    if search:
        # Literal substring search: %, _ and backslash are not wildcards.
        escaped = search.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        sql += " AND (lower(c.source_customer_id) LIKE %s ESCAPE '!' OR lower(c.email) LIKE %s ESCAPE '!' OR lower(c.phone_number) LIKE %s ESCAPE '!')"
        params += [f"%{escaped}%"] * 3
    if source:
        sql += " AND split_part(c.source_customer_id, ':', 1) = %s"
        params.append(source)
    return page(sql + " ORDER BY c.source_customer_id, c.id", params, limit=limit, offset=offset)


ORDER_TOTALS = """
SELECT o.id, o.source_order_id, o.order_date, o.order_timestamp, o.region,
       split_part(o.source_order_id, ':', 1) AS source,
       count(oi.id) AS line_count, sum(oi.sales_value) AS net_revenue,
       sum(CASE WHEN coalesce(p.department, '') NOT IN ('KIOSK-GAS', 'MISC SALES TRAN')
                THEN oi.quantity ELSE 0 END) AS merchandise_units
FROM orders o
LEFT JOIN order_items oi ON oi.order_id = o.id
LEFT JOIN products p ON p.id = oi.product_id AND p.store_id = o.store_id
WHERE o.store_id = %s AND o.customer_id = %s
GROUP BY o.id, o.source_order_id, o.order_date, o.order_timestamp, o.region
"""


def purchase_summary(store_id, customer_id):
    return rows("""SELECT source, count(*) AS recorded_orders,
        sum(CASE WHEN net_revenue > 0 THEN 1 ELSE 0 END) AS purchase_orders,
        sum(CASE WHEN net_revenue <= 0 THEN 1 ELSE 0 END) AS nonpositive_orders,
        sum(CASE WHEN line_count = 0 THEN 1 ELSE 0 END) AS orders_without_lines,
        sum(net_revenue) AS observed_net_revenue,
        avg(CASE WHEN net_revenue > 0 THEN net_revenue END) AS average_order_value,
        min(CASE WHEN net_revenue > 0 THEN order_date END) AS first_purchase_date,
        max(CASE WHEN net_revenue > 0 THEN order_date END) AS last_purchase_date,
        min(order_date) AS observed_from, max(order_date) AS observed_to
        FROM (""" + ORDER_TOTALS + ") totals GROUP BY source ORDER BY source",
        [str(store_id), str(customer_id)])


def orders(store_id, customer_id, **pagination):
    return page(ORDER_TOTALS + " ORDER BY o.order_date DESC, o.id", [str(store_id), str(customer_id)], **pagination)


def order(store_id, customer_id, order_id):
    result = rows("SELECT * FROM (" + ORDER_TOTALS + ") totals WHERE id = %s",
                  [str(store_id), str(customer_id), str(order_id)])
    if not result:
        raise Http404
    return result[0]


def order_items(store_id, customer_id, order_id, **pagination):
    return page("""SELECT oi.*, p.source_product_id, p.name AS product_name,
        p.brand, p.category, p.department
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        LEFT JOIN products p ON p.id = oi.product_id AND p.store_id = o.store_id
        WHERE o.store_id = %s AND o.customer_id = %s AND o.id = %s
        ORDER BY oi.id""", [str(store_id), str(customer_id), str(order_id)], **pagination)


# Closed registry: route strings can never become SQL identifiers.
HISTORIES = {
    "events": ("events", "event_timestamp"),
    "messages": ("message_sends", "sent_at"),
    "coupons": ("coupon_redemptions", "created_at"),
}


def history(kind, store_id, customer_id, **pagination):
    table, timestamp = HISTORIES[kind]
    return page(f"SELECT h.* FROM {table} h WHERE h.store_id = %s AND h.customer_id = %s ORDER BY h.{timestamp} DESC, h.id",
                [str(store_id), str(customer_id)], **pagination)


def activity_summary(store_id, customer_id):
    params = [str(store_id), str(customer_id)]
    return {
        "events": rows("""SELECT event_type, count(*) AS count,
            min(event_timestamp) AS first_at, max(event_timestamp) AS last_at
            FROM events WHERE store_id = %s AND customer_id = %s
            GROUP BY event_type ORDER BY event_type""", params),
        "messages": rows("""SELECT channel_norm AS channel, count(*) AS sent,
            sum(CASE WHEN is_opened THEN 1 ELSE 0 END) AS opened,
            sum(CASE WHEN is_clicked THEN 1 ELSE 0 END) AS clicked,
            sum(CASE WHEN is_purchased THEN 1 ELSE 0 END) AS purchase_flags,
            sum(CASE WHEN is_unsubscribed THEN 1 ELSE 0 END) AS unsubscribed,
            max(sent_at) AS last_sent_at
            FROM message_sends WHERE store_id = %s AND customer_id = %s
            GROUP BY channel_norm ORDER BY channel_norm""", params),
        "coupons": rows("""SELECT count(*) AS recorded,
            sum(CASE WHEN redemption_status THEN 1 ELSE 0 END) AS redeemed
            FROM coupon_redemptions WHERE store_id = %s AND customer_id = %s""", params)[0],
    }


def segments(store_id, customer_id):
    params = [str(store_id), str(customer_id)]
    return {
        "predictions": rows("""SELECT sp.segment_id, s.name, sp.segment_key,
            sp.status, sp.result, sp.observation_date, sp.computed_at
            FROM segment_predictions sp JOIN segments s ON s.id = sp.segment_id
            WHERE sp.store_id = %s AND sp.customer_id = %s ORDER BY sp.segment_key, sp.segment_id""", params),
        "memberships": rows("""SELECT cs.segment_id, s.name, s.segment_type, cs.score, cs.assigned_at
            FROM customer_segments cs JOIN segments s ON s.id = cs.segment_id
            WHERE cs.store_id = %s AND cs.customer_id = %s ORDER BY s.name, cs.segment_id""", params),
    }


def features(store_id, customer_id):
    result = rows("SELECT f.* FROM customer_features f WHERE f.store_id = %s AND f.customer_id = %s",
                  [str(store_id), str(customer_id)])
    return result[0] if result else None
