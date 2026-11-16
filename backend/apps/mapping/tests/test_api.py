"""HTTP proposal/review/apply contract; no database or Gemini requests."""
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
import pytest
from rest_framework.test import APIClient

from apps.segments.authentication import issue_token

STORE = uuid.uuid4()
CSV = b"email,tenure\none@example.com,12\ntwo@example.com,24\n"


def upload(content=CSV):
    return SimpleUploadedFile("customers.csv", content, content_type="text/csv")


@pytest.fixture
def client():
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer " + issue_token(STORE))
    return api


def propose(client):
    response = client.post("/api/mapping/propose/", {"file": upload(), "use_llm": "false", "kind": "customers"}, format="multipart")
    assert response.status_code == 200, response.data
    return response.json()


def test_targets_are_schema_gated(client):
    body = client.get("/api/mapping/targets/").json()
    targets = {(row["table"], row["column"]) for row in body["targets"]}
    assert ("customers", "email") in targets
    assert ("customers", "store_id") not in targets
    assert ("customers", "id") not in targets


def test_end_to_end_review_apply_and_preprocess(client):
    body = propose(client)
    assert body["document"]["summary"]["by_status"]["approved"] == 0
    assert body["persisted"] is False
    reviewed = client.post("/api/mapping/review/", {"state": body["state"], "approve_suggested": True}, format="json")
    assert reviewed.status_code == 200, reviewed.data
    assert reviewed.json()["document"]["summary"]["by_status"]["approved"] == 2
    response = client.post("/api/mapping/apply/", {"state": reviewed.json()["state"], "file": upload()}, format="multipart")
    assert response.status_code == 200, response.data
    data = response.json()
    table = data["tables"]["customers"]
    assert table["columns"] == ["email", "tenure"]
    assert table["rows"][0] == ["one@example.com", "12"]
    assert data["report"]["output_dir"] is None
    assert data["report"]["output_files"] == []
    assert data["persisted"] is False
    cleaned = client.post("/api/preprocessing/run/", table, format="json")
    assert cleaned.status_code == 200, cleaned.data
    assert cleaned.json()["row_count"] == 2
    assert cleaned["Cache-Control"] == "private, no-store"


def test_unapproved_mappings_never_apply(client):
    body = propose(client)
    response = client.post("/api/mapping/apply/", {"state": body["state"], "file": upload()}, format="multipart")
    assert response.status_code == 200
    assert response.json()["tables"] == {}
    assert response.json()["status"] == "no_applicable_mappings"


def test_manual_correction_and_rejection(client):
    body = propose(client)
    response = client.post("/api/mapping/review/", {"state": body["state"],
        "set_targets": [{"source_column": "tenure", "target_table": "customers", "target_column": "tenure"}],
        "reject": ["email"]}, format="json")
    assert response.status_code == 200
    mappings = {row["source_column"]: row for row in response.json()["document"]["mappings"]}
    assert mappings["tenure"]["method"] == "manual"
    assert mappings["email"]["status"] == "rejected"


@pytest.mark.parametrize("decisions", [
    {"approve": ["nonexistent"]},
    {"set_targets": [{"source_column": "email", "target_table": "stores", "target_column": "id"}]},
    {"approve": ["email"], "reject": ["email"]},
    {"set_targets": [{"source_column": "email", "target_table": "customers", "target_column": "tenure"},
                     {"source_column": "tenure", "target_table": "customers", "target_column": "tenure"}]},
])
def test_invalid_review_is_rejected(client, decisions):
    body = propose(client)
    assert client.post("/api/mapping/review/", {"state": body["state"], **decisions}, format="json").status_code == 400


def test_signed_state_tampering_cross_store_and_expiry(client):
    body = propose(client)
    assert client.post("/api/mapping/review/", {"state": body["state"] + "tampered"}, format="json").status_code == 400
    with override_settings(SEGMENTS_TOKEN_MAX_AGE=7200), patch("apps.mapping.services.time.time", return_value=body["expires_at"] + 1):
        assert client.post("/api/mapping/review/", {"state": body["state"]}, format="json").status_code == 400
    client.credentials(HTTP_AUTHORIZATION="Bearer " + issue_token(uuid.uuid4()))
    assert client.post("/api/mapping/review/", {"state": body["state"]}, format="json").status_code == 400


def test_apply_bound_to_original_bytes(client):
    body = propose(client)
    response = client.post("/api/mapping/apply/", {"state": body["state"], "file": upload(CSV.replace(b"12", b"13"))}, format="multipart")
    assert response.status_code == 400


@pytest.mark.parametrize("raw", [b"", b"email,email\na,b\n", b"email\na,b\n", b"email\n", b"\xff\xfe", b"email\na\x00\n"])
def test_invalid_csv(client, raw):
    assert client.post("/api/mapping/propose/", {"file": upload(raw), "use_llm": False}, format="multipart").status_code == 400


def test_file_limit(client):
    with patch("core.api.MAX_BYTES", 10):
        assert client.post("/api/mapping/propose/", {"file": upload()}, format="multipart").status_code == 413


def test_llm_failure_degrades_to_review(client):
    from apps.mapping.llm import LLMUnavailable
    with patch("apps.mapping.llm.make_client", side_effect=LLMUnavailable("unavailable")):
        response = client.post("/api/mapping/propose/", {"file": upload(b"unknown_attribute\nhello\nworld\n")}, format="multipart")
    assert response.status_code == 200
    assert response.json()["document"]["mappings"][0]["status"] == "needs_review"


def test_relationship_mapping_is_not_silently_applied(client):
    response = client.post("/api/mapping/propose/", {"file": upload(b"customer_id\n1\n2\n"), "use_llm": False, "kind": "orders"}, format="multipart")
    reviewed = client.post("/api/mapping/review/", {"state": response.json()["state"], "set_targets": [
        {"source_column": "customer_id", "target_table": "orders", "target_column": "customer_id"}]}, format="json")
    applied = client.post("/api/mapping/apply/", {"state": reviewed.json()["state"], "file": upload(b"customer_id\n1\n2\n")}, format="multipart")
    assert applied.status_code == 200
    assert any("relationship_resolution" in row["reason"] for row in applied.json()["report"]["skipped"])


def test_auth_and_request_validation(client):
    assert APIClient().get("/api/mapping/targets/").status_code == 401
    assert client.post("/api/mapping/propose/", {"file": upload(), "kind": "bad"}, format="multipart").status_code == 400
    assert client.post("/api/mapping/review/", {"document": {}}, format="json").status_code == 400
    assert client.get("/api/mapping/propose/").status_code == 405
