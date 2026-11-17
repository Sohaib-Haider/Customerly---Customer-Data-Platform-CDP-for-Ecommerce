import json
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.db import connection, OperationalError
import pytest
from rest_framework.test import APIClient
from apps.analytics.tests.test_customer360 import canonical_tables, insert
from apps.segments.authentication import issue_token
from apps.segments.services.catalog import DEFINITIONS


@pytest.fixture
def audience_data(canonical_tables):
    store, foreign, customer, other, unassigned = [uuid.uuid4() for _ in range(5)]
    for tenant in (store, foreign):
        insert("stores", id=tenant.hex, name="test", plan="test", created_at="2026-01-01", updated_at="2026-01-01")
    for person, tenant, source in ((customer, store, "shop:001"), (unassigned, store, "shop:002"), (other, foreign, "shop:003")):
        insert("customers", id=person.hex, store_id=tenant.hex, source_customer_id=source, email=source + "@example.com",
               created_at="2026-01-01", updated_at="2026-01-01")
    definitions = {}
    for key, spec in DEFINITIONS.items():
        segment = uuid.uuid4()
        definitions[key] = segment
        insert("segments", id=segment.hex, name=spec["name"], segment_type="ml", created_at="2026-01-01")
    for person, tenant in ((customer, store), (other, foreign)):
        insert("customer_segments", id=uuid.uuid4().hex, store_id=tenant.hex, customer_id=person.hex,
               segment_id=definitions["churn"].hex, score="0.7500", assigned_at="2026-01-02")
    for person, value in ((customer, 0.75), (unassigned, 0.2)):
        insert("segment_predictions", id=uuid.uuid4().hex, store_id=store.hex, customer_id=person.hex,
               segment_id=definitions["churn"].hex, segment_key="churn", status="predicted", computed_at="2026-01-02",
               result=json.dumps({"prediction": {"churn_probability": value}, "membership_policy": {"requested": True, "threshold": "0.5"}}))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + issue_token(store))
    return client, store, customer, definitions


def test_two_cards_count_members_not_predictions(audience_data):
    client, store, customer, definitions = audience_data
    response = client.get("/api/audiences/")
    assert response.status_code == 200, response.data
    cards = {row["key"]: row for row in response.json()["audiences"]}
    assert set(cards) == {"churn", "clv"}
    churn = cards["churn"]
    assert churn["member_count"] == 1 and churn["predicted_count"] == 2
    assert churn["total_customers"] == 2 and churn["state"] == "ready"
    assert churn["membership_configured"] is True
    assert churn["campaign"] == {"enabled": False, "label": "Create campaign", "reason": "Coming soon"}
    assert cards["clv"]["state"] == "not_configured"
    assert response["Cache-Control"] == "private, no-store"


def test_member_list_and_search_are_tenant_scoped(audience_data):
    client, store, customer, definitions = audience_data
    body = client.get("/api/audiences/churn/customers/", {"limit": 1, "search": "001"}).json()
    assert body["count"] == 1
    assert body["results"][0]["customer_id"] == str(customer)
    assert Decimal(str(body["results"][0]["score"])) == Decimal("0.75")
    assert body["results"][0]["prediction"]["result"]["prediction"]["churn_probability"] == 0.75
    assert client.get("/api/audiences/churn/customers/", {"search": "%"}).json()["count"] == 0
    assert client.get("/api/audiences/churn/customers/", {"source": "other"}).json()["count"] == 0
    assert client.get("/api/audiences/churn/customers/", {"offset": 50}).json()["results"] == []


def test_bad_requests_and_authentication(audience_data):
    client, *_ = audience_data
    assert APIClient().get("/api/audiences/").status_code == 401
    assert client.get("/api/audiences/not-real/").status_code == 404
    assert client.get("/api/audiences/not-real/customers/").status_code == 404
    assert client.get("/api/audiences/churn/customers/", {"limit": 101}).status_code == 400
    assert client.post("/api/audiences/churn/", {}, format="json").status_code == 405


def test_get_does_not_score_or_write(audience_data):
    client, *_ = audience_data
    with patch("apps.segments.services.execution.execute_customer", side_effect=AssertionError("GET rescored")):
        with connection.execute_wrapper(lambda execute, sql, params, many, context: (
            pytest.fail("Audience GET wrote data") if not sql.lstrip().upper().startswith("SELECT") else execute(sql, params, many, context))):
            assert client.get("/api/audiences/").status_code == 200
            assert client.get("/api/audiences/churn/customers/").status_code == 200


def test_database_errors_are_sanitized(audience_data):
    client, *_ = audience_data
    with patch("apps.segments.services.audiences.describe", side_effect=OperationalError("private")):
        response = client.get("/api/audiences/churn/")
    assert response.status_code == 503 and "private" not in str(response.data)
