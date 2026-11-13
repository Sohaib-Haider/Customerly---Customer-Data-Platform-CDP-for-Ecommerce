"""Isolated integration coverage using test-owned canonical fixture tables."""

from datetime import date
from decimal import Decimal
import json
import uuid
from unittest.mock import patch

from django.db import connection
import pytest
from rest_framework.test import APIClient

from apps.segments.authentication import issue_token
from apps.segments.models import Customer, CustomerMembership, SegmentDefinition, SegmentPrediction, Store
from apps.segments.services.catalog import initialize_catalog
from apps.segments.services.execution import execute_customer


@pytest.fixture
def records(transactional_db):
    external_models = (Store, Customer, SegmentDefinition, CustomerMembership)
    existing = set(connection.introspection.table_names())
    with connection.schema_editor() as editor:
        for model in external_models:
            if model._meta.db_table not in existing:
                editor.create_model(model)
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE IF NOT EXISTS ml_models (segment_id varchar(32), version varchar(50), model_path text, trained_at timestamp, metrics text, is_active boolean, PRIMARY KEY (segment_id, version))")
        cursor.execute("CREATE TABLE IF NOT EXISTS products (id varchar(32) PRIMARY KEY, store_id varchar(32), source_product_id text)")
        cursor.execute("CREATE TABLE IF NOT EXISTS orders (id varchar(32) PRIMARY KEY, store_id varchar(32), customer_id varchar(32), source_order_id text, order_date date)")
        cursor.execute("CREATE TABLE IF NOT EXISTS order_items (id varchar(32) PRIMARY KEY, order_id varchar(32), product_id varchar(32), source_timestamp timestamp, quantity integer, unit_price numeric, sales_value numeric)")
    first = Store.objects.create(id=uuid.uuid4(), name="First store")
    second = Store.objects.create(id=uuid.uuid4(), name="Other store")
    churn = Customer.objects.create(id=uuid.uuid4(), store=first, source_customer_id="chr:example",
        tenure=15, warehouse_to_home=29, number_of_devices_registered=4,
        satisfaction_score=3, complain=False, cashback_amount=Decimal("143.32"))
    retail = Customer.objects.create(id=uuid.uuid4(), store=first, source_customer_id="ret:001")
    other = Customer.objects.create(id=uuid.uuid4(), store=second, source_customer_id="chr:other")
    product, order, item = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO products VALUES (%s, %s, %s)", [product.hex, first.id.hex, "ret:00123"])
        cursor.execute("INSERT INTO orders VALUES (%s, %s, %s, %s, %s)", [order.hex, first.id.hex, retail.id.hex, "ret:0001", date(2011, 12, 1)])
        cursor.execute("INSERT INTO order_items VALUES (%s, %s, %s, %s, %s, %s, %s)", [item.hex, order.hex, product.hex, "2011-12-01 10:00:00", 2, "10.50", "21.00"])
    initialize_catalog()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + issue_token(first.id))
    yield {"store": first, "other_store": second, "churn": churn, "retail": retail, "other": other, "client": client}
    SegmentPrediction.objects.all().delete()
    with connection.cursor() as cursor:
        for name in ("order_items", "orders", "products", "ml_models"):
            cursor.execute("DELETE FROM " + name)
    for model in (CustomerMembership, Customer, SegmentDefinition, Store):
        model.objects.all().delete()


def test_api_requires_a_valid_signed_token():
    client = APIClient()
    assert client.get("/api/segments/").status_code == 401
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
    assert client.get("/api/segments/").status_code == 401


def test_catalog_initialization_is_idempotent_and_store_scoped(records):
    initialize_catalog()
    assert SegmentDefinition.objects.count() == 2
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM ml_models WHERE is_active")
        assert cursor.fetchone()[0] == 2
    response = records["client"].get("/api/segments/")
    assert response.status_code == 200
    assert {row["key"] for row in response.json()["segments"]} == {"churn", "clv"}


def test_churn_reads_customer_values_and_persists_without_changing_source(records):
    before = Customer.objects.filter(id=records["churn"].id).values().get()
    response = records["client"].post("/api/segments/churn/run/", {"customer_ids": [str(records["churn"].id)]}, format="json")
    assert response.status_code == 200, response.data
    result = response.json()["results"][0]
    assert result["status"] == "predicted" and result["persisted"] is True
    assert 0 <= result["prediction"]["churn_probability"] <= 1
    assert Customer.objects.filter(id=records["churn"].id).values().get() == before
    assert SegmentPrediction.objects.count() == 1
    assert CustomerMembership.objects.count() == 0  # no fabricated threshold
    records["client"].post("/api/segments/churn/run/", {"customer_ids": [str(records["churn"].id)]}, format="json")
    assert SegmentPrediction.objects.count() == 1


def test_clv_reads_joined_purchase_history_and_stores_amount_separately(records):
    response = records["client"].post("/api/segments/clv/run/", {
        "customer_ids": [str(records["retail"].id)], "observation_date": "2011-12-09", "currency": "GBP",
        "update_memberships": True, "membership_threshold": "0",
    }, format="json")
    assert response.status_code == 200, response.data
    result = response.json()["results"][0]
    assert result["status"] == "predicted"
    assert result["prediction"]["currency"] == "GBP"
    assert result["prediction"]["predicted_revenue"] >= 0
    assert SegmentPrediction.objects.get().result["prediction"] == result["prediction"]
    assert CustomerMembership.objects.get().score is None


def test_missing_attributes_produce_persisted_unavailable_result(records):
    result = execute_customer("churn", records["store"].id, records["retail"].id)
    assert result["status"] == "unavailable"
    assert result["eligibility"]["eligible"] is False
    assert SegmentPrediction.objects.get().status == "unavailable"


def test_demo_populations_do_not_share_clv_histories(records):
    result = execute_customer("clv", records["store"].id, records["churn"].id,
        observation_date=date(2011, 12, 9), currency="GBP")
    assert result["status"] == "unavailable"
    assert result["reasons"][0]["code"] == "unsupported_population"


def test_dry_run_does_not_save_results_or_memberships(records):
    result = execute_customer("churn", records["store"].id, records["churn"].id, persist=False)
    assert result["status"] == "predicted" and result["persisted"] is False
    assert not SegmentPrediction.objects.exists()
    assert not CustomerMembership.objects.exists()


def test_foreign_store_ids_are_rejected_before_any_scoring(records):
    with patch("apps.segments.views.execute_customer", side_effect=AssertionError("Do not run")):
        response = records["client"].post("/api/segments/churn/run/", {
            "customer_ids": [str(records["churn"].id), str(records["other"].id)],
        }, format="json")
    assert response.status_code == 404
    assert not SegmentPrediction.objects.exists()
    assert records["client"].get(f'/api/customers/{records["other"].id}/segments/').status_code == 404


def test_membership_is_replaced_when_customer_stops_qualifying(records):
    execute_customer("churn", records["store"].id, records["churn"].id, update_memberships=True, membership_threshold=0)
    assert CustomerMembership.objects.count() == 1
    Customer.objects.filter(id=records["churn"].id).update(cashback_amount=None)
    result = execute_customer("churn", records["store"].id, records["churn"].id, update_memberships=True, membership_threshold=0)
    assert result["status"] == "unavailable"
    assert not CustomerMembership.objects.exists()


def test_request_validation_and_results_pagination(records):
    client = records["client"]
    ids = [str(records["retail"].id)]
    assert client.post("/api/segments/clv/run/", {"customer_ids": ids}, format="json").status_code == 400
    assert client.post("/api/segments/churn/run/", {"customer_ids": ids, "update_memberships": True}, format="json").status_code == 400
    assert client.post("/api/segments/churn/run/", {"customer_ids": ids * 2}, format="json").status_code == 400
    assert client.get("/api/segments/churn/results/?limit=1000").status_code == 400
    execute_customer("churn", records["store"].id, records["churn"].id)
    execute_customer("churn", records["other_store"].id, records["other"].id)
    response = client.get("/api/segments/churn/results/?limit=1")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["customer_id"] == str(records["churn"].id)


def test_registry_mismatch_prevents_scoring_an_unregistered_artifact(records):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE ml_models SET is_active = FALSE")
    result = execute_customer("churn", records["store"].id, records["churn"].id)
    assert result["status"] == "unavailable"
    assert result["reasons"][0]["code"] == "model_registry_mismatch"


def test_invalid_or_mismatched_financial_data_is_not_predicted(records):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE order_items SET sales_value = 999")
    result = execute_customer("clv", records["store"].id, records["retail"].id,
        observation_date=date(2011, 12, 9), currency="GBP")
    assert result["status"] == "unavailable"
    assert result["reasons"][0]["code"] == "inconsistent_purchase_amount"


def test_expired_tokens_are_rejected(records):
    token = issue_token(records["store"].id)
    with patch("django.core.signing.time.time", return_value=99999999999):
        records["client"].credentials(HTTP_AUTHORIZATION="Bearer " + token)
        assert records["client"].get("/api/segments/").status_code == 401


def test_older_observation_date_cannot_overwrite_newer_current_result(records):
    execute_customer("clv", records["store"].id, records["retail"].id, observation_date=date(2011, 12, 9), currency="GBP")
    with pytest.raises(ValueError, match="older observation date"):
        execute_customer("clv", records["store"].id, records["retail"].id, observation_date=date(2011, 12, 8), currency="GBP")
    assert SegmentPrediction.objects.get().observation_date == date(2011, 12, 9)


def test_registry_failure_does_not_erase_existing_membership(records):
    execute_customer("churn", records["store"].id, records["churn"].id, update_memberships=True, membership_threshold=0)
    with connection.cursor() as cursor:
        cursor.execute("UPDATE ml_models SET is_active = FALSE")
    result = execute_customer("churn", records["store"].id, records["churn"].id, update_memberships=True, membership_threshold=0)
    assert result["status"] == "unavailable"
    assert result["membership"]["updated"] is False
    assert CustomerMembership.objects.exists()


def test_batch_failure_rolls_back_prior_customer_results(records):
    calls = []
    def fail_after_first(*args, **kwargs):
        if calls:
            raise ValueError("Invalid request policy")
        calls.append(True)
        return execute_customer(*args, **kwargs)
    with patch("apps.segments.views.execute_customer", side_effect=fail_after_first):
        response = records["client"].post("/api/segments/churn/run/", {
            "customer_ids": [str(records["churn"].id), str(records["retail"].id)],
        }, format="json")
    assert response.status_code == 400
    assert not SegmentPrediction.objects.exists()


def test_cross_store_product_is_rejected_without_partial_prediction(records):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE products SET store_id = %s", [records["other_store"].id.hex])
    result = execute_customer("clv", records["store"].id, records["retail"].id,
        observation_date=date(2011, 12, 9), currency="GBP")
    assert result["status"] == "unavailable"
    assert result["reasons"][0]["code"] == "inconsistent_tenant_history"
