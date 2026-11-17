"""Exercise the real selectors and HTTP API against isolated canonical tables."""
import json
from pathlib import Path
import re
import uuid
from unittest.mock import patch

from django.db import connection, OperationalError
import pytest
from rest_framework.test import APIClient

from apps.segments.authentication import issue_token

STORE, OTHER_STORE, CUSTOMER, OTHER_CUSTOMER = [str(uuid.uuid4()) for _ in range(4)]
ORDER, RETURN, OTHER_ORDER, SEGMENT = [str(uuid.uuid4()) for _ in range(4)]


@pytest.fixture
def canonical_tables(transactional_db):
    # Unmanaged canonical tables are installed externally in production. Build
    # their actual SQL definitions in the isolated SQLite test database only.
    schema = (Path(__file__).resolve().parents[3] / "schema.sql").read_text()
    statements = re.findall(r"CREATE TABLE \w+ \([\s\S]*?\n\);", schema)
    # This suite owns its schema for the duration of each test. Do not leave
    # full canonical definitions in the shared test DB for suites using partial
    # unmanaged model fixtures (and never touch the configured Supabase DB).
    assert connection.vendor == "sqlite"
    names = [re.match(r"CREATE TABLE (\w+)", statement)[1] for statement in statements]
    with connection.constraint_checks_disabled(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM segment_predictions")
        cursor.execute("DELETE FROM segment_preparation_jobs")
        for name in reversed(names):
            cursor.execute(f'DROP TABLE IF EXISTS "{name}"')
        for statement in statements:
            statement = statement.replace("DEFAULT gen_random_uuid()", "").replace("DEFAULT now()", "")
            cursor.execute(statement)
    yield
    with connection.constraint_checks_disabled(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM segment_predictions")
        cursor.execute("DELETE FROM segment_preparation_jobs")
        for name in reversed(names):
            cursor.execute(f'DROP TABLE IF EXISTS "{name}"')
    # Keep the unmanaged FK targets present for Django's post-test flush and
    # restore the partial models expected by the segmentation test fixtures.
    from apps.segments.models import Store, Customer, SegmentDefinition, CustomerMembership
    with connection.schema_editor() as editor:
        for model in (Store, Customer, SegmentDefinition, CustomerMembership):
            editor.create_model(model)


def insert(table, **values):
    # All identifiers below are test-owned constants.
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values()))


@pytest.fixture
def seeded(canonical_tables):
    connection.ensure_connection()
    connection.connection.create_function("split_part", 3, lambda value, delimiter, position: value.split(delimiter)[position - 1])
    for store in (STORE, OTHER_STORE):
        insert("stores", id=store, name="test", plan="test", created_at="2026-01-01", updated_at="2026-01-01")
    for customer, store in ((CUSTOMER, STORE), (OTHER_CUSTOMER, OTHER_STORE)):
        insert("customers", id=customer, store_id=store, source_customer_id="ret:customer",
               email="person@example.com", phone_number="123", created_at="2026-01-01", updated_at="2026-01-01")
    product = str(uuid.uuid4())
    insert("products", id=product, store_id=STORE, source_product_id="ret:product", name="Product",
           created_at="2026-01-01", updated_at="2026-01-01")
    for order, customer, store, total, date in (
        (ORDER, CUSTOMER, STORE, "100.25", "2026-01-02"),
        (RETURN, CUSTOMER, STORE, "-10.25", "2026-01-03"),
        (OTHER_ORDER, OTHER_CUSTOMER, OTHER_STORE, "9000", "2026-01-02"),
    ):
        insert("orders", id=order, store_id=store, customer_id=customer, source_order_id="ret:" + order,
               order_date=date, created_at="2026-01-01")
        insert("order_items", id=str(uuid.uuid4()), order_id=order, product_id=product,
               quantity=1, unit_price=total, sales_value=total)
    insert("events", id=str(uuid.uuid4()), store_id=STORE, customer_id=CUSTOMER,
           session_id="session", event_type="view", event_timestamp="2026-01-03T10:00:00Z")
    insert("events", id=str(uuid.uuid4()), store_id=OTHER_STORE, customer_id=OTHER_CUSTOMER,
           session_id="other", event_type="cart", event_timestamp="2026-01-03T10:00:00Z")
    insert("segments", id=SEGMENT, name="Churn", segment_type="ml", created_at="2026-01-01")
    insert("segment_predictions", id=str(uuid.uuid4()), store_id=STORE, customer_id=CUSTOMER,
           segment_id=SEGMENT, segment_key="churn", status="predicted", computed_at="2026-01-04",
           result=json.dumps({"status": "predicted", "prediction": {"churn_probability": 0.75},
                              "metadata": {"model_version": "test"}}))
    insert("customer_segments", id=str(uuid.uuid4()), store_id=STORE, customer_id=CUSTOMER,
           segment_id=SEGMENT, score="0.7500", assigned_at="2026-01-04")
    return CUSTOMER


@pytest.fixture
def client():
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer " + issue_token(STORE))
    return api


def test_authentication_required():
    api = APIClient()
    assert api.get("/api/customers/").status_code == 401
    api.credentials(HTTP_AUTHORIZATION="Bearer invalid")
    assert api.get(f"/api/customers/{CUSTOMER}/360/").status_code == 401


def test_profile_uses_customer_totals_and_saved_results(client, seeded):
    response = client.get(f"/api/customers/{CUSTOMER}/360/", {"store_id": OTHER_STORE})
    assert response.status_code == 200, response.data
    body = response.json()
    summary = body["purchases"]["by_source"][0]
    assert body["customer"]["id"] == CUSTOMER
    assert body["source"]["currency"] == "GBP"
    assert summary["observed_net_revenue"] == 90
    assert summary["purchase_orders"] == 1
    assert summary["average_order_value"] == 100.25
    assert summary["last_purchase_date"] == "2026-01-02"
    assert body["segments"]["predictions"][0]["result"]["prediction"]["churn_probability"] == 0.75
    assert len(body["segments"]["memberships"]) == 1
    assert body["feature_snapshot"]["status"] == "not_computed"
    assert body["activity"]["events"][0]["event_type"] == "view"
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("suffix", ["360/", "orders/", "events/", "messages/", "coupons/", f"orders/{ORDER}/"])
def test_foreign_and_missing_customer_indistinguishable(client, seeded, suffix):
    foreign = client.get(f"/api/customers/{OTHER_CUSTOMER}/{suffix}")
    absent = client.get(f"/api/customers/{uuid.uuid4()}/{suffix}")
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json() == absent.json()


def test_customer_search_and_source_filter(client, seeded):
    response = client.get("/api/customers/", {"search": "PERSON@", "source": "ret", "limit": 1})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["id"] == CUSTOMER
    assert client.get("/api/customers/", {"search": "%"}).json()["count"] == 0
    assert client.get("/api/customers/", {"source": "dnb"}).json()["count"] == 0


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "abc"}, {"source": "ret' OR 1=1"}])
def test_invalid_queries(client, query):
    assert client.get("/api/customers/", query).status_code == 400


def test_paginated_orders_and_detail(client, seeded):
    url = f"/api/customers/{CUSTOMER}/orders/"
    first = client.get(url, {"limit": 1}).json()
    second = client.get(url, {"limit": 1, "offset": 1}).json()
    assert first["count"] == 2
    assert first["results"][0]["id"] == RETURN
    assert second["results"][0]["id"] == ORDER
    detail = client.get(url + ORDER + "/").json()
    assert detail["items"]["count"] == 1
    assert detail["items"]["results"][0]["product_name"] == "Product"
    assert client.get(url + OTHER_ORDER + "/").status_code == 404
    assert client.get(url, {"offset": 100}).json()["results"] == []


@pytest.mark.parametrize("kind,count", [("events", 1), ("messages", 0), ("coupons", 0)])
def test_histories(client, seeded, kind, count):
    response = client.get(f"/api/customers/{CUSTOMER}/{kind}/")
    assert response.status_code == 200
    assert response.json()["count"] == count


def test_empty_profile_is_not_zero_prediction(client, seeded):
    customer = str(uuid.uuid4())
    insert("customers", id=customer, store_id=STORE, source_customer_id="chr:empty",
           created_at="2026-01-01", updated_at="2026-01-01")
    body = client.get(f"/api/customers/{customer}/360/").json()
    assert body["purchases"] == {"status": "no_records", "by_source": []}
    assert body["segments"] == {"predictions": [], "memberships": []}
    assert body["customer"]["email"] is None


def test_engagement_features_and_unavailable_prediction(client, seeded):
    campaign = str(uuid.uuid4())
    insert("marketing_campaigns", id=campaign, store_id=STORE, source_campaign_id="msg:campaign",
           campaign_type="test", channel="email", created_at="2026-01-01")
    insert("message_sends", id=str(uuid.uuid4()), store_id=STORE, customer_id=CUSTOMER,
           source_message_id="msg:message", campaign_id=campaign, message_type="test", channel="email",
           channel_norm="email", sent_at="2026-01-03T10:00:00", is_opened=True, is_clicked=True,
           is_purchased=True, created_at="2026-01-01")
    insert("coupon_campaigns", id=campaign, store_id=STORE, source_campaign_id="cpn:campaign",
           campaign_type="test", start_date="2026-01-01", end_date="2026-02-01", created_at="2026-01-01")
    insert("coupon_redemptions", id=str(uuid.uuid4()), store_id=STORE, customer_id=CUSTOMER,
           campaign_id=campaign, coupon_id="coupon", redemption_status=True, created_at="2026-01-03")
    insert("customer_features", customer_id=CUSTOMER, store_id=STORE,
           purchase_by_week=json.dumps({"1": 3}), computed_at="2026-01-02")
    with connection.cursor() as cursor:
        cursor.execute("UPDATE segment_predictions SET status = %s, result = %s WHERE customer_id = %s",
                       ["unavailable", json.dumps({"status": "unavailable", "prediction": None,
                        "reasons": [{"code": "data_unavailable"}]}), CUSTOMER])
    body = client.get(f"/api/customers/{CUSTOMER}/360/").json()
    assert body["activity"]["messages"][0]["opened"] == 1
    assert body["activity"]["messages"][0]["purchase_flags"] == 1
    assert body["activity"]["coupons"] == {"recorded": 1, "redeemed": 1}
    assert body["feature_snapshot"]["data"]["purchase_by_week"] == {"1": 3}
    prediction = body["segments"]["predictions"][0]
    assert prediction["status"] == "unavailable" and prediction["result"]["prediction"] is None
    assert len(body["segments"]["memberships"]) == 1  # Reading never reconciles membership.
    for kind in ("messages", "coupons"):
        assert client.get(f"/api/customers/{CUSTOMER}/{kind}/").json()["count"] == 1


def test_coupon_basket_average_unavailable_and_sources_separate(client, seeded):
    for source, total in (("cpn", 50), ("dnb", 20)):
        order = str(uuid.uuid4())
        insert("orders", id=order, store_id=STORE, customer_id=CUSTOMER, source_order_id=source + ":order",
               order_date="2026-01-03", created_at="2026-01-01")
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM products WHERE store_id = %s", [STORE])
            product = cursor.fetchone()[0]
        insert("order_items", id=str(uuid.uuid4()), order_id=order, product_id=product, quantity=1, sales_value=total)
    summaries = client.get(f"/api/customers/{CUSTOMER}/360/").json()["purchases"]["by_source"]
    by_source = {row["source"]["code"]: row for row in summaries}
    assert len(by_source) == 3
    assert by_source["cpn"]["average_order_value"] is None
    assert by_source["cpn"]["average_order_value_unavailable_reason"]
    assert by_source["dnb"]["source"]["currency"] == "USD"


def test_get_endpoints_only_read(client, seeded):
    with connection.execute_wrapper(lambda execute, sql, params, many, context: (
        pytest.fail("Customer 360 issued a write") if not sql.lstrip().upper().startswith("SELECT")
        else execute(sql, params, many, context)
    )):
        assert client.get(f"/api/customers/{CUSTOMER}/360/").status_code == 200
        assert client.get(f"/api/customers/{CUSTOMER}/orders/{ORDER}/").status_code == 200
    assert client.post(f"/api/customers/{CUSTOMER}/360/", {}).status_code == 405


@patch("apps.analytics.customer360.selectors.rows", side_effect=OperationalError("secret connection details"))
def test_database_errors_are_sanitized(mock_rows, client):
    response = client.get(f"/api/customers/{CUSTOMER}/360/")
    assert response.status_code == 503
    assert "secret" not in str(response.data)


def test_decimal_and_local_time_serialization():
    from datetime import datetime
    from decimal import Decimal
    from apps.analytics.customer360.services import json_values
    assert json_values({"money": Decimal("100000.123456789"), "time": datetime(2026, 1, 1, 12)}) == {
        "money": "100000.123456789", "time": "2026-01-01T12:00:00"}
