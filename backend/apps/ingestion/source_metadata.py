"""Store-scoped source declarations for downstream analytics."""
PROFILE_TEMPLATE = {"retail": "ret", "transactions": "dnb", "churn": "chr", "events": "evb", "messages": "msg", "coupons": "cpn"}


def metadata(store_id, namespace, conn=None):
    if store_id is None:
        return None
    if conn is not None:
        # Also works for the standalone psycopg KPI CLI without Django setup.
        with conn.cursor() as cursor:
            import uuid
            stored_store = uuid.UUID(str(store_id)).hex if getattr(conn, "vendor", None) == "sqlite" else str(store_id)
            cursor.execute("SELECT name, currency, analytics_profile FROM ingestion_sources WHERE store_id = %s AND namespace = %s",
                           [stored_store, namespace])
            row = cursor.fetchone()
        if row is None:
            return None
        name, currency, profile = row
    else:
        from .models import Source
        source = Source.objects.filter(store_id=store_id, namespace=namespace).first()
        if source is None:
            return None
        name, currency, profile = source.name, source.currency, source.analytics_profile
    return {"name": name, "currency": currency, "profile": profile, "template": PROFILE_TEMPLATE.get(profile)}
