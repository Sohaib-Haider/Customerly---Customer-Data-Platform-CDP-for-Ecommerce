from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from .test_ingestion import client, canonical_tables, source, create, validate, process, customer_review, token, OTHER_STORE, OTHER_USER
from apps.ingestion.models import ActivityEvent, Batch
import uuid


def events(client, batch_id, **query):
    return client.get(f"/api/ingestion/batches/{batch_id}/events/", query)


def test_timeline_cursors_real_outcomes_and_save_replay(client):
    body = customer_review(client)
    assert validate(client, body).status_code == 200
    assert process(client, body).status_code == 200
    response = events(client, body["id"], limit=100)
    assert response.status_code == 200
    data = response.json()
    codes = [item["code"] for item in data["results"]]
    for code in ("upload.completed", "mapping.started", "mapping.completed", "review.completed",
                 "preprocessing.started", "preprocessing.completed", "saving.started", "saving.completed"):
        assert code in codes
    assert not any("missing_values" in item["message"] for item in data["results"])
    assert all(item["occurred_at"] for item in data["results"])
    assert response["Cache-Control"] == "private, no-store"
    first = events(client, body["id"], limit=2).json()
    second = events(client, body["id"], after=first["next_after"], limit=2).json()
    assert first["has_more"]
    assert first["results"][-1]["id"] < second["results"][0]["id"]
    count = ActivityEvent.objects.count()
    assert process(client, body).status_code == 200
    assert ActivityEvent.objects.count() == count
    assert events(client, body["id"], after=data["next_after"]).json()["results"] == []


def test_failure_is_durable_without_false_save_success(client):
    body = customer_review(client)
    assert validate(client, body).status_code == 200
    from django.db import IntegrityError
    with patch("apps.ingestion.services.save_records", side_effect=IntegrityError("test")):
        assert process(client, body).status_code == 409
    codes = list(ActivityEvent.objects.filter(batch_id=body["id"]).values_list("code", flat=True))
    assert "saving.failed" in codes
    assert "saving.completed" not in codes
    assert Batch.objects.get(id=body["id"]).active_operation_id is None


def test_start_visible_during_action_and_parallel_action_rejected(client):
    body = create(client, source(client), [("c.csv", "customer\n001\n")]).json()
    from apps.mapping.mapper import map_csv
    def observed(*args, **kwargs):
        polled = events(client, body["id"]).json()
        assert polled["activity"]["stage"] == "mapping"
        assert any(row["code"] == "mapping.started" for row in polled["results"])
        duplicate = client.post(f'/api/ingestion/batches/{body["id"]}/map/', {"revision": 0, "use_llm": False}, format="json")
        assert duplicate.status_code == 409
        return map_csv(*args, **kwargs)
    with patch("apps.ingestion.services.map_csv", side_effect=observed):
        response = client.post(f'/api/ingestion/batches/{body["id"]}/map/', {"revision": 0, "use_llm": False}, format="json")
    assert response.status_code == 200
    assert response.json()["activity"]["stage"] is None


def test_expired_operation_lease_can_recover(client):
    body = create(client, source(client), [("c.csv", "customer\n001\n")]).json()
    Batch.objects.filter(id=body["id"]).update(active_operation_id=uuid.uuid4(), active_stage="mapping",
        operation_started_at=timezone.now() - timedelta(hours=1))
    response = client.post(f'/api/ingestion/batches/{body["id"]}/map/', {"revision": 0, "use_llm": False}, format="json")
    assert response.status_code == 200
    assert ActivityEvent.objects.filter(batch_id=body["id"], code="operation.interrupted").exists()


def test_event_scope_and_query_validation(client):
    body = customer_review(client)
    assert events(client, body["id"], after=-1).status_code == 400
    assert events(client, body["id"], limit=101).status_code == 400
    client.credentials(HTTP_AUTHORIZATION="Bearer " + token(OTHER_STORE, OTHER_USER))
    assert events(client, body["id"]).status_code == 404
