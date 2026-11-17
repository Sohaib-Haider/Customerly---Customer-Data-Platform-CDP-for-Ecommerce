"""Repeatable live PostgreSQL integration check; all sample records roll back."""
import tempfile
import uuid
import hashlib
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from apps.segments.authentication import TOKEN_SALT


class Command(BaseCommand):
    help = "Verify multi-CSV ingestion, both real models, Customer 360 and KPI reads in a rolled-back PostgreSQL transaction."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("This live verification requires the configured PostgreSQL database.")
        store, user = uuid.uuid4(), uuid.uuid4()
        root = Path(settings.INGESTION_PRIVATE_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        files = {
            "customers.csv": "customer,email,tenure,distance,devices,satisfaction,complaint,cashback\n001,verify@example.invalid,12,5,2,4,false,100\n",
            "products.csv": "product,title\np1,Widget\n",
            "orders.csv": "order,customer,date\no1,001,2026-11-01\n",
            "lines.csv": "order,product,quantity,price,sales\no1,p1,2,10.25,20.50\n",
        }
        targets = {
            "customers.csv": [("customer", "customers", "source_customer_id"), ("email", "customers", "email"),
                ("tenure", "customers", "tenure"), ("distance", "customers", "warehouse_to_home"),
                ("devices", "customers", "number_of_devices_registered"), ("satisfaction", "customers", "satisfaction_score"),
                ("complaint", "customers", "complain"), ("cashback", "customers", "cashback_amount")],
            "products.csv": [("product", "products", "source_product_id"), ("title", "products", "name")],
            "orders.csv": [("order", "orders", "source_order_id"), ("date", "orders", "order_date")],
            "lines.csv": [("quantity", "order_items", "quantity"), ("price", "order_items", "unit_price"), ("sales", "order_items", "sales_value")],
        }
        bindings = {"orders.csv": [("customer", "orders", "customer_id")],
                    "lines.csv": [("order", "order_items", "order_id"), ("product", "order_items", "product_id")]}

        def checked(response, statuses=(200,)):
            if response.status_code not in statuses:
                raise CommandError(f"Verification endpoint returned HTTP {response.status_code}.")
            return response.json()

        def require(condition, message):
            if not condition:
                raise CommandError(message)

        with tempfile.TemporaryDirectory(dir=root) as private, override_settings(ALLOWED_HOSTS=["testserver"], INGESTION_PRIVATE_ROOT=private):
            with transaction.atomic():
                # The outer transaction always rolls back, including on exceptions.
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO stores (id,name,plan) VALUES (%s,%s,%s)", [str(store), "Temporary ingestion verification", "test"])
                    cursor.execute("INSERT INTO store_users (id,store_id,email,role) VALUES (%s,%s,%s,%s)", [str(user), str(store), "verify@example.invalid", "owner"])
                api = APIClient()
                api.credentials(HTTP_AUTHORIZATION="Bearer " + signing.dumps({"store_id": str(store), "store_user_id": str(user), "purpose": "segments"}, salt=TOKEN_SALT))
                namespace = "verify_" + uuid.uuid4().hex[:8]
                source = checked(api.post("/api/ingestion/sources/", {"namespace": namespace, "name": "Temporary verification", "currency": "GBP", "analytics_profile": "retail"}, format="json"), (201,))
                batch = checked(api.post("/api/ingestion/batches/", {"source_id": source["id"], "request_key": uuid.uuid4().hex,
                    "files": [SimpleUploadedFile(name, text.encode()) for name, text in files.items()]}, format="multipart"), (201,))
                base = f'/api/ingestion/batches/{batch["id"]}/'
                batch = checked(api.post(base + "map/", {"revision": 0, "use_llm": False}, format="json"))
                decisions = [{"file_id": file["id"], "set_targets": [{"source_column": col, "target_table": table, "target_column": target}
                    for col, table, target in targets[file["name"]]], "bindings": [{"source_column": col, "target_table": table, "target_column": target}
                    for col, table, target in bindings.get(file["name"], [])]} for file in batch["files"]]
                batch = checked(api.post(base + "review/", {"revision": batch["revision"], "files": decisions,
                    "segment_options": {"churn": {"enabled": True}, "clv": {"enabled": True, "observation_date": "2026-11-17"}}}, format="json"))
                checked(api.post(base + "validate/", {"revision": batch["revision"]}, format="json"))
                saved = checked(api.post(base + "process/", {"revision": batch["revision"]}, format="json"))
                require(saved["status"] == "completed", "Prediction jobs failed; initialize the segment registry and inspect saved job status.")
                import psycopg
                with psycopg.connect(**connection.get_connection_params()) as competitor:
                    with competitor.cursor() as cursor:
                        for name in (f"ingestion:create:{store}", f'ingestion:source:{store}:{source["id"]}'):
                            lock_id = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
                            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [lock_id])
                            require(cursor.fetchone()[0] is False, "A concurrent connection bypassed the ingestion serialization lock.")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM customers WHERE store_id = %s", [str(store)])
                    customer = cursor.fetchone()[0]
                    cursor.execute("SELECT unit_price,sales_value FROM order_items i JOIN orders o ON o.id=i.order_id WHERE o.store_id=%s", [str(store)])
                    price, sales = cursor.fetchone()
                require(str(price) == "10.25" and str(sales) == "20.50", "Original monetary precision changed.")
                profile = checked(api.get(f"/api/customers/{customer}/360/"))
                predictions = profile["segments"]["predictions"]
                require(len(predictions) == 2 and all(item["status"] == "predicted" for item in predictions), "Both real models must produce saved predictions.")
                require(profile["source"]["currency"] == "GBP", "Customer 360 did not resolve source metadata.")
                kpi = checked(api.get("/api/kpis/total_revenue/"))
                require(kpi["results"][namespace]["revenue"] == 20.5, "KPI did not include the declared source.")
                checked(api.post(base + "process/", {"revision": batch["revision"]}, format="json"))
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM orders WHERE store_id=%s", [str(store)])
                    require(cursor.fetchone()[0] == 1, "Repeated processing duplicated an order.")
                    cursor.execute("SELECT count(*) FROM pg_class WHERE relname IN ('ingestion_sources','ingestion_batches','ingestion_files','ingestion_reviews','ingestion_segment_jobs') AND relrowsecurity")
                    require(cursor.fetchone()[0] == 5, "Private ingestion tables must have RLS enabled.")
                transaction.set_rollback(True)
        self.stdout.write("Verified: four CSVs, exact amounts, relationships, two real model predictions, Customer 360, KPI, idempotency, concurrent-connection locks and RLS. Test rows rolled back; temporary files removed.")
