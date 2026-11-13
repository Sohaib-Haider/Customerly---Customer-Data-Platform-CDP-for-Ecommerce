"""KPI API: authentication, tenant scoping and request validation.

Card computation itself is covered by test_kpis.py against PostgreSQL; here
run_card is replaced so the tests stay isolated from any database.
"""
from unittest.mock import patch
import uuid

from django.db import OperationalError
import pytest
from rest_framework.test import APIClient

from apps.segments.authentication import issue_token

STORE = uuid.uuid4()


@pytest.fixture
def client():
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer " + issue_token(STORE))
    return api


def fake_result(conn, card, *, store_id=None, dimension=None):
    return {"id": card.id, "key": card.key, "store_id": store_id, "dimension": dimension,
            "results": {}, "unavailable": {}}


def test_requires_a_valid_token():
    api = APIClient()
    assert api.get("/api/kpis/").status_code == 401
    api.credentials(HTTP_AUTHORIZATION="Bearer invalid")
    assert api.get("/api/kpis/3/").status_code == 401


def test_lists_the_19_fyp1_cards(client):
    response = client.get("/api/kpis/")
    assert response.status_code == 200
    cards = response.json()["cards"]
    assert [c["id"] for c in cards] == list(range(1, 20))
    revenue = cards[0]
    assert revenue["key"] == "total_revenue" and "month" in revenue["dimensions"]


@patch("apps.analytics.views.run_card", side_effect=fake_result)
def test_card_is_scoped_to_the_token_store(run, client):
    for ref in ("3", "average_order_value"):
        body = client.get(f"/api/kpis/{ref}/", {"store_id": str(uuid.uuid4())}).json()
        assert body["key"] == "average_order_value"
        assert body["store_id"] == str(STORE)  # query parameters cannot change the store


@patch("apps.analytics.views.run_card", side_effect=fake_result)
def test_breakdown_is_passed_through(run, client):
    body = client.get("/api/kpis/total_revenue/", {"by": "month"}).json()
    assert body["dimension"] == "month"


@patch("apps.analytics.views.run_card", side_effect=fake_result)
def test_rejects_unknown_cards_and_breakdowns(run, client):
    assert client.get("/api/kpis/99/").status_code == 404
    assert client.get("/api/kpis/no_such_card/").status_code == 404
    response = client.get("/api/kpis/3/", {"by": "month"})
    assert response.status_code == 400
    assert "Options" in response.json()["detail"]
    run.assert_not_called()


@patch("apps.analytics.views.run_card", side_effect=OperationalError("down"))
def test_database_errors_return_503(run, client):
    assert client.get("/api/kpis/1/").status_code == 503
