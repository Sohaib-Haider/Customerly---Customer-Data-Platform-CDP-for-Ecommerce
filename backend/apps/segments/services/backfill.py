"""Bounded refresh-time scoring of database customers without saved results."""
from datetime import timedelta
import hashlib

from django.db import connection, transaction
from django.db.models import Exists, OuterRef

from ..models import Customer, SegmentPrediction
from .catalog import get_definition
from .database import db_uuid
from .execution import execute_customer
from .results import unavailable
from .logging import after_commit, logged, progress


@logged('backfill')
def fill_missing(store_id, limit=25):
    processed = 0
    with transaction.atomic():
        if connection.vendor == "postgresql":
            lock = int.from_bytes(hashlib.sha256(f"segment-backfill:{store_id}".encode()).digest()[:8], "big", signed=True)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [lock])
                if not cursor.fetchone()[0]:
                    progress('backfill.busy', 'Another preparation is running; this request will not duplicate it.')
                    return {"processed": 0, "remaining": True, "busy": True}
        for key in ("churn", "clv"):
            definition = get_definition(key)
            saved = SegmentPrediction.objects.filter(store_id=store_id, customer_id=OuterRef("pk"), segment=definition)
            missing = Customer.objects.filter(store_id=store_id).annotate(saved=Exists(saved)).filter(saved=False).order_by("id")
            progress('backfill.scan', 'Selecting customers without saved results; existing outcomes are reused.', segment=key,
                     batch_capacity=max(0, limit - processed))
            for customer in missing[:max(0, limit - processed)]:
                # Serialize with explicit run/ingestion too, then recheck after locking.
                if connection.vendor == "postgresql":
                    lock = int.from_bytes(hashlib.sha256(f"segments:{store_id}:{key}:{customer.id}".encode()).digest()[:8], "big", signed=True)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock])
                if SegmentPrediction.objects.filter(store_id=store_id, customer=customer, segment=definition).exists():
                    progress('backfill.skipped', 'A result was already saved by another request; keeping it.', segment=key, customer_id=customer.id)
                    continue
                options = {}
                if key == "clv":
                    from apps.ingestion.source_metadata import metadata
                    prefix = customer.source_customer_id.split(":", 1)[0]
                    declaration = metadata(store_id, prefix) if prefix != "ret" else {"currency": "GBP"}
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT MAX(order_date) FROM orders WHERE store_id = %s AND customer_id = %s", [db_uuid(store_id), db_uuid(customer.id)])
                        last_date = cursor.fetchone()[0]
                    if not declaration or declaration["currency"] != "GBP" or last_date is None:
                        progress('clv.data.unavailable', 'Skipping model prediction: declared GBP purchase history is missing.',
                                 customer_id=customer.id)
                        payload = unavailable(key, "data_unavailable", "CLV requires declared GBP purchase history.").as_dict()
                        SegmentPrediction.objects.create(store_id=store_id, customer=customer, segment=definition, segment_key=key, status="unavailable", result=payload)
                        after_commit('prediction.save.committed', 'CLV unavailable outcome committed to database.', customer_id=customer.id, segment=key)
                        processed += 1
                        continue
                    from datetime import date
                    if isinstance(last_date, str):
                        last_date = date.fromisoformat(last_date)
                    options = {"currency": "GBP", "observation_date": last_date + timedelta(days=1)}
                    progress('clv.cutoff.selected', 'Using the day after the latest recorded purchase as historical observation cutoff.', customer_id=customer.id)
                execute_customer(key, store_id, customer.id, **options)
                processed += 1
        remaining = any(Customer.objects.filter(store_id=store_id).exclude(
            id__in=SegmentPrediction.objects.filter(store_id=store_id, segment_key=key).values("customer_id")
        ).exists() for key in ("churn", "clv"))
        after_commit('backfill.committed', 'Prediction batch committed.', processed=processed, remaining=remaining)
    return {"processed": processed, "remaining": remaining, "busy": False}
