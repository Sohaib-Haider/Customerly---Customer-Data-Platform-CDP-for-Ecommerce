import json
from decimal import Decimal
import uuid
from unittest.mock import patch

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, IntegrityError
from django.test import override_settings
import pytest
from rest_framework.test import APIClient

from apps.analytics.tests.test_customer360 import canonical_tables, insert
from apps.segments.authentication import TOKEN_SALT, issue_token
from apps.ingestion.models import Batch, SegmentJob, ReviewVersion
from apps.ingestion.compiler import compile_batch

STORE, USER, OTHER_STORE, OTHER_USER = [str(uuid.uuid4()) for _ in range(4)]


def token(store=STORE, user=USER):
    return signing.dumps({"store_id": store, "store_user_id": user, "purpose": "segments"}, salt=TOKEN_SALT)


@pytest.fixture
def client(canonical_tables, tmp_path):
    connection.connection.create_function("split_part", 3, lambda value, delimiter, position: value.split(delimiter)[position - 1])
    for store, user in ((STORE, USER), (OTHER_STORE, OTHER_USER)):
        insert("stores", id=store, name="test", plan="test", created_at="2026-01-01", updated_at="2026-01-01")
        insert("store_users", id=user, store_id=store, email=user + "@example.com", role="owner", created_at="2026-01-01")
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer " + token())
    with override_settings(INGESTION_PRIVATE_ROOT=tmp_path / "private"):
        yield api


def source(client, namespace="shop", currency="GBP", profile="retail"):
    result = client.post("/api/ingestion/sources/", {"namespace": namespace, "name": namespace,
                         "currency": currency, "analytics_profile": profile}, format="json")
    assert result.status_code == 201, result.data
    return result.json()["id"]


def create(client, source_id, files, key="request-1"):
    response = client.post("/api/ingestion/batches/", {"source_id": source_id, "request_key": key,
        "files": [SimpleUploadedFile(name, content.encode(), content_type="text/csv") for name, content in files]}, format="multipart")
    assert response.status_code in {200, 201}, response.data
    return response


def mapped(client, files, key="request-1", source_id=None):
    body = create(client, source_id or source(client), files, key).json()
    result = client.post(f'/api/ingestion/batches/{body["id"]}/map/', {"revision": 0, "use_llm": False}, format="json")
    assert result.status_code == 200, result.data
    return result.json()


def review(client, body, targets, *, bindings=None, segment_options=None, identities=None, deduplicate=False):
    decisions = []
    for file in body["files"]:
        assignments = targets.get(file["name"], [])
        decisions.append({"file_id": file["id"], "set_targets": [{"source_column": col, "target_table": table, "target_column": target} for col, table, target in assignments],
            "bindings": (bindings or {}).get(file["name"], []), "identity_columns": (identities or {}).get(file["name"], {}), "deduplicate": deduplicate})
    result = client.post(f'/api/ingestion/batches/{body["id"]}/review/', {"revision": body["revision"], "files": decisions,
                         "segment_options": segment_options or {"churn": {"enabled": False}}}, format="json")
    assert result.status_code == 200, result.data
    return result.json()


def validate(client, body):
    return client.post(f'/api/ingestion/batches/{body["id"]}/validate/', {"revision": body["revision"]}, format="json")


def process(client, body):
    return client.post(f'/api/ingestion/batches/{body["id"]}/process/', {"revision": body["revision"]}, format="json")


def customer_review(client, key="request-1", content="customer,email,extra\n001,one@example.com,discard\n002,two@example.com,discard\n", source_id=None):
    body = mapped(client, [("customers.csv", content)], key, source_id)
    return review(client, body, {"customers.csv": [("customer", "customers", "source_customer_id"), ("email", "customers", "email")]})


def test_customer_upload_review_save_and_360(client):
    body = customer_review(client)
    checked = validate(client, body)
    assert checked.status_code == 200, checked.data
    assert checked.json()["report"]["counts"] == {"customers": 2}
    assert "extra" not in str(checked.json()["report"]["files"])
    saved = process(client, body)
    assert saved.status_code == 200, saved.data
    assert saved.json()["status"] == "completed"
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, source_customer_id FROM customers ORDER BY source_customer_id")
        customers = cursor.fetchall()
    assert customers[0][1] == "shop:001"
    profile = client.get(f"/api/customers/{customers[0][0]}/360/")
    assert profile.status_code == 200
    assert profile.json()["source"]["currency"] == "GBP"
    assert profile.json()["customer"]["email"] == "one@example.com"
    assert ReviewVersion.objects.count() == 1
    again = process(client, body)
    assert again.status_code == 200
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM customers")
        assert cursor.fetchone()[0] == 2


def test_multifile_orders_products_and_repeated_lines(client):
    files = [("customers.csv", "customer\n001\n"), ("products.csv", "product,title\np1,Product\n"),
             ("orders.csv", "order,customer,date\no1,001,2026-01-02\n"),
             ("lines.csv", "order,product,quantity,price,sales\no1,p1,2,100000.123456789,200000.246913578\no1,p1,2,100000.123456789,200000.246913578\n")]
    body = mapped(client, files)
    targets = {"customers.csv": [("customer", "customers", "source_customer_id")],
        "products.csv": [("product", "products", "source_product_id"), ("title", "products", "name")],
        "orders.csv": [("order", "orders", "source_order_id"), ("date", "orders", "order_date")],
        "lines.csv": [("quantity", "order_items", "quantity"), ("price", "order_items", "unit_price"), ("sales", "order_items", "sales_value")]}
    bindings = {"orders.csv": [{"source_column": "customer", "target_table": "orders", "target_column": "customer_id"}],
                "lines.csv": [{"source_column": "order", "target_table": "order_items", "target_column": "order_id"},
                              {"source_column": "product", "target_table": "order_items", "target_column": "product_id"}]}
    body = review(client, body, targets, bindings=bindings)
    checked = validate(client, body)
    assert checked.status_code == 200, checked.data
    records, report = compile_batch(Batch.objects.get(id=body["id"]))
    assert len(records["order_items"]) == 2
    assert next(iter(records["order_items"].values()))["unit_price"] == Decimal("100000.123456789")
    response = process(client, body)
    assert response.status_code == 200, response.data
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM customers")
        customer_id = cursor.fetchone()[0]
    orders = client.get(f"/api/customers/{customer_id}/orders/").json()
    assert orders["count"] == 1 and orders["results"][0]["line_count"] == 2
    # PostgreSQL-only KPI aggregate syntax is tested separately; source registry
    # declarations used by those aggregates are verified here.
    from apps.ingestion.source_metadata import metadata
    assert metadata(STORE, "shop")["template"] == "ret"


def test_unresolved_relationship_blocks_entire_batch(client):
    body = mapped(client, [("orders.csv", "order,customer,date\no1,missing,2026-01-02\n")])
    body = review(client, body, {"orders.csv": [("order", "orders", "source_order_id"), ("date", "orders", "order_date")]},
                  bindings={"orders.csv": [{"source_column": "customer", "target_table": "orders", "target_column": "customer_id"}]})
    response = validate(client, body)
    assert response.status_code == 422
    assert response.json()["status"] == "validation_failed"
    assert process(client, body).status_code == 409


def test_idempotent_creation_and_conflicting_key(client):
    source_id = source(client)
    files = [("c.csv", "id\n001\n")]
    first, second = create(client, source_id, files), create(client, source_id, files)
    assert first.json()["id"] == second.json()["id"]
    assert second.status_code == 200
    response = client.post("/api/ingestion/batches/", {"source_id": source_id, "request_key": "request-1",
        "files": [SimpleUploadedFile("c.csv", b"id\n002\n")]}, format="multipart")
    assert response.status_code == 409


def test_foreign_batches_and_uploader_claim(client):
    body = customer_review(client)
    client.credentials(HTTP_AUTHORIZATION="Bearer " + token(OTHER_STORE, OTHER_USER))
    assert client.get(f'/api/ingestion/batches/{body["id"]}/').status_code == 404
    client.credentials(HTTP_AUTHORIZATION="Bearer " + issue_token(STORE))
    assert client.get("/api/ingestion/batches/").status_code == 401
    client.credentials(HTTP_AUTHORIZATION="Bearer " + token(STORE, OTHER_USER))
    assert client.get("/api/ingestion/batches/").status_code == 401


def test_stale_review_and_validation_required(client):
    body = customer_review(client)
    assert process(client, body).status_code == 409
    assert client.post(f'/api/ingestion/batches/{body["id"]}/validate/', {"revision": 1}, format="json").status_code == 409


def test_transaction_failure_rolls_back_all_business_records(client):
    body = customer_review(client)
    assert validate(client, body).status_code == 200
    from apps.ingestion.compiler import save_records
    def fail_after_save(records):
        save_records(records)
        raise IntegrityError("test")
    with patch("apps.ingestion.services.save_records", side_effect=fail_after_save):
        assert process(client, body).status_code == 409
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM customers")
        assert cursor.fetchone()[0] == 0
    assert Batch.objects.get(id=body["id"]).status == "validated"


def test_prediction_failure_keeps_import_and_can_retry(client):
    body = mapped(client, [("c.csv", "customer\n001\n")])
    body = review(client, body, {"c.csv": [("customer", "customers", "source_customer_id")]}, segment_options={"churn": {"enabled": True}})
    assert validate(client, body).status_code == 200
    with patch("apps.ingestion.jobs.execute_customer", side_effect=RuntimeError("offline")):
        saved = process(client, body)
    assert saved.status_code == 200 and saved.json()["status"] == "segments_failed"
    assert SegmentJob.objects.get().attempts == 1
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM customers")
        assert cursor.fetchone()[0] == 1
    with patch("apps.ingestion.jobs.execute_customer", return_value={"status": "unavailable", "reasons": [{"code": "data_unavailable"}]}):
        retry = client.post(f'/api/ingestion/batches/{body["id"]}/retry-segments/', {"revision": body["revision"]}, format="json")
    assert retry.json()["status"] == "completed"
    assert SegmentJob.objects.get().attempts == 2


@pytest.mark.parametrize("value", ["nan", "Infinity", "1.5", "99999999999999999999999999"])
def test_bad_numeric_values_rejected_without_imputing(client, value):
    body = mapped(client, [("c.csv", f"customer,tenure\n001,{value}\n")])
    body = review(client, body, {"c.csv": [("customer", "customers", "source_customer_id"), ("tenure", "customers", "tenure")]})
    assert validate(client, body).status_code == 422


def test_blank_patch_does_not_erase_existing_values(client):
    source_id = source(client)
    first = customer_review(client, source_id=source_id)
    assert validate(client, first).status_code == 200
    assert process(client, first).status_code == 200
    second = customer_review(client, key="request-2", source_id=source_id, content="customer,email,extra\n001,,discard\n")
    assert validate(client, second).status_code == 200
    assert process(client, second).status_code == 200
    with connection.cursor() as cursor:
        cursor.execute("SELECT email FROM customers WHERE source_customer_id = %s", ["shop:001"])
        assert cursor.fetchone()[0] == "one@example.com"


def test_private_download_and_source_contract(client):
    body = customer_review(client)
    file = body["files"][0]
    response = client.get(f'/api/ingestion/batches/{body["id"]}/files/{file["id"]}/')
    assert response.status_code == 200
    assert b"customer,email" in b"".join(response.streaming_content)
    assert response["Cache-Control"] == "private, no-store"
    assert client.post("/api/ingestion/sources/", {"namespace": "ret", "name": "spoof", "currency": "GBP", "analytics_profile": "retail"}, format="json").status_code == 400
    assert "storage_name" not in json.dumps(body)


def test_calculation_and_date_format_are_explicit(client):
    body = mapped(client, [("mixed.csv", "customer,order,product,title,date,quantity,price\n001,o1,p1,Widget,02/11/2026,2,10.25\n")])
    file = body["files"][0]
    targets = [("customer", "customers", "source_customer_id"), ("order", "orders", "source_order_id"),
               ("product", "products", "source_product_id"), ("title", "products", "name"),
               ("quantity", "order_items", "quantity"), ("price", "order_items", "unit_price")]
    result = client.post(f'/api/ingestion/batches/{body["id"]}/review/', {"revision": body["revision"], "segment_options": {"churn": {"enabled": False}},
        "files": [{"file_id": file["id"], "set_targets": [{"source_column": col, "target_table": table, "target_column": target} for col, table, target in targets],
            "bindings": [{"source_column": "customer", "target_table": "orders", "target_column": "customer_id"},
                         {"source_column": "order", "target_table": "order_items", "target_column": "order_id"},
                         {"source_column": "product", "target_table": "order_items", "target_column": "product_id"}],
            "calculations": [{"target_table": "orders", "target_column": "order_date", "operation": "date", "source_columns": ["date"], "format": "%d/%m/%Y"},
                             {"target_table": "order_items", "target_column": "sales_value", "operation": "multiply", "source_columns": ["quantity", "price"]}]}]}, format="json")
    assert result.status_code == 200, result.data
    body = result.json()
    assert validate(client, body).status_code == 200
    records, _ = compile_batch(Batch.objects.get(id=body["id"]))
    assert str(next(iter(records["orders"].values()))["order_date"]) == "2026-11-02"
    assert next(iter(records["order_items"].values()))["sales_value"] == Decimal("20.50")
    assert process(client, body).status_code == 200


def test_conflicting_customer_records_block_batch(client):
    body = mapped(client, [("one.csv", "customer,email\n001,one@example.com\n"), ("two.csv", "customer,email\n001,two@example.com\n")])
    targets = {file["name"]: [("customer", "customers", "source_customer_id"), ("email", "customers", "email")] for file in body["files"]}
    body = review(client, body, targets)
    assert validate(client, body).status_code == 422


def test_occurrence_identity_updates_without_duplicate_lines(client):
    # First import a full mixed row, then change only the amount under a stable
    # line key. Source-record identities are independent of upload bytes.
    source_id = source(client)
    def prepare(content, key):
        body = mapped(client, [("mixed.csv", content)], key, source_id)
        return review(client, body, {"mixed.csv": [("customer", "customers", "source_customer_id"),
            ("order", "orders", "source_order_id"), ("date", "orders", "order_date"),
            ("product", "products", "source_product_id"), ("title", "products", "name"),
            ("quantity", "order_items", "quantity"), ("sales", "order_items", "sales_value")]},
            bindings={"mixed.csv": [{"source_column": "customer", "target_table": "orders", "target_column": "customer_id"},
                {"source_column": "order", "target_table": "order_items", "target_column": "order_id"},
                {"source_column": "product", "target_table": "order_items", "target_column": "product_id"}]},
            identities={"mixed.csv": {"order_items": ["line"]}})
    text = "customer,order,date,product,title,quantity,sales,line\n001,o1,2026-01-02,p1,Widget,1,10,L001\n"
    for key, content in (("first", text), ("second", text.replace(",10,", ",20,"))):
        body = prepare(content, key)
        assert validate(client, body).status_code == 200
        assert process(client, body).status_code == 200
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*),sum(sales_value) FROM order_items")
        assert cursor.fetchone() == (1, 20)


def test_stored_file_checksum_prevents_modified_data(client):
    body = customer_review(client)
    batch = Batch.objects.get(id=body["id"])
    from apps.ingestion.storage import storage
    file = batch.files.get()
    with storage().open(file.storage_name, "wb") as handle:
        handle.write(b"modified\nfile\n")
    assert validate(client, body).status_code == 422


def test_model_unavailable_is_retryable_and_worker_resumes(client):
    body = mapped(client, [("c.csv", "customer\n001\n")])
    body = review(client, body, {"c.csv": [("customer", "customers", "source_customer_id")]}, segment_options={"churn": {"enabled": True}})
    assert validate(client, body).status_code == 200
    with patch("apps.ingestion.jobs.execute_customer", return_value={"status": "unavailable", "reasons": [{"code": "invalid_artifact"}]}):
        assert process(client, body).json()["status"] == "segments_failed"
    from apps.ingestion.jobs import run_jobs
    with patch("apps.ingestion.jobs.execute_customer", return_value={"status": "predicted", "reasons": []}):
        assert run_jobs(retry_failed=True) == 1
    assert Batch.objects.get(id=body["id"]).status == "completed"
    Batch.objects.filter(id=body["id"]).update(status="segments_pending")
    assert run_jobs() == 0
    assert Batch.objects.get(id=body["id"]).status == "completed"


def test_clv_requires_currency_and_date(client):
    source_id = source(client, currency="USD", profile="transactions")
    body = mapped(client, [("c.csv", "customer\n001\n")], source_id=source_id)
    request = {"revision": body["revision"], "files": [{"file_id": body["files"][0]["id"], "approve_suggested": True}],
               "segment_options": {"clv": {"enabled": True}}}
    url = f'/api/ingestion/batches/{body["id"]}/review/'
    assert client.post(url, request, format="json").status_code == 400
    request["segment_options"]["clv"]["observation_date"] = "2026-01-05"
    assert client.post(url, request, format="json").status_code == 400


def test_retention_purges_completed_uploads_only(client):
    from datetime import timedelta
    from django.utils import timezone
    from django.core.management import call_command
    import io
    from apps.ingestion.storage import storage
    body = customer_review(client)
    assert validate(client, body).status_code == 200
    assert process(client, body).status_code == 200
    batch = Batch.objects.get(id=body["id"])
    file = batch.files.get()
    name = file.storage_name
    Batch.objects.filter(id=batch.id).update(updated_at=timezone.now() - timedelta(days=31))
    call_command("purge_ingestion_uploads", stdout=io.StringIO())
    assert not storage().exists(name)
    file.refresh_from_db()
    assert file.storage_name == ""
    assert client.get(f'/api/ingestion/batches/{batch.id}/files/{file.id}/').status_code == 404
    assert process(client, body).status_code == 200


def test_job_api_and_source_aware_kpi_metadata(client):
    body = mapped(client, [("c.csv", "customer\n001\n")])
    body = review(client, body, {"c.csv": [("customer", "customers", "source_customer_id")]}, segment_options={"churn": {"enabled": True}})
    assert validate(client, body).status_code == 200
    with patch("apps.ingestion.jobs.execute_customer", return_value={"status": "predicted", "reasons": []}):
        assert process(client, body).status_code == 200
    response = client.get(f'/api/ingestion/batches/{body["id"]}/jobs/', {"limit": 1})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["attempts"] == 1
    from apps.analytics.kpis.engine import run_card
    from apps.analytics.kpis.cards import CARDS
    with patch("apps.analytics.kpis.engine._rows", return_value=[{"source": "shop", "revenue": 20.5}]):
        card = run_card(connection, CARDS[0], store_id=STORE)
    assert card["results"]["shop"]["revenue"] == 20.5
    assert card["sources"]["shop"]["currency"] == "GBP"
    client.credentials(HTTP_AUTHORIZATION="Bearer " + token(OTHER_STORE, OTHER_USER))
    assert client.get(f'/api/ingestion/batches/{body["id"]}/jobs/').status_code == 404


def test_duplicate_file_and_empty_record_rejection(client):
    source_id = source(client)
    duplicated = client.post("/api/ingestion/batches/", {"source_id": source_id, "request_key": "dup",
        "files": [SimpleUploadedFile("a.csv", b"customer\n001\n"), SimpleUploadedFile("b.csv", b"customer\n001\n")]}, format="multipart")
    assert duplicated.status_code == 400
    body = mapped(client, [("empty.csv", "customer,email\n,\n")], source_id=source_id)
    body = review(client, body, {"empty.csv": [("customer", "customers", "source_customer_id"), ("email", "customers", "email")]})
    assert validate(client, body).status_code == 422
