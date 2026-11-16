import json
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
import pytest
from rest_framework.test import APIClient

from apps.segments.authentication import issue_token


@pytest.fixture
def client():
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer " + issue_token(uuid.uuid4()))
    return api


def test_defaults_expose_original_ten_steps(client):
    response = client.get("/api/preprocessing/config/")
    assert response.status_code == 200
    body = response.json()
    assert len(body["steps"]) == 10
    assert all(body["default_config"][step]["enabled"] for step in body["steps"])
    assert body["model_scaling"] is body["category_encoding"] is False


def test_json_cleaning_deduplicates_fills_and_preserves_units(client):
    payload = {"columns": [" Revenue ", "Category"], "rows": [[100000, " A "], [200000, "B"], [None, "C"], [100000, " A "]]}
    response = client.post("/api/preprocessing/run/", payload, format="json")
    assert response.status_code == 200, response.data
    body = response.json()
    assert body["columns"] == ["revenue", "category"]
    assert body["rows"] == [[100000, "A"], [200000, "B"], [150000, "C"]]
    assert body["report"]["per_step"]["remove_duplicates"]["removed_rows"] == 1
    assert body["remaining_missing_values"] == 0
    assert body["persisted"] is False
    assert payload["rows"][0][1] == " A "


def test_csv_and_partial_step_options(client):
    raw = b"value,group\n1,a\n1,a\n2,b\n,c\n"
    config = {"remove_duplicates": {"keep": "last"}, "handle_missing_values": {"strategy": "constant", "fill_value": 8},
              "handle_outliers": {"enabled": False}}
    response = client.post("/api/preprocessing/run/", {"file": SimpleUploadedFile("input.csv", raw), "config": json.dumps(config)}, format="multipart")
    assert response.status_code == 200, response.data
    assert response.json()["rows"] == [[1, "a"], [2, "b"], [8, "c"]]


@pytest.mark.parametrize("payload", [
    {}, {"columns": ["a"]}, {"columns": ["a", "a"], "rows": [[1, 2]]},
    {"columns": ["a"], "rows": [[{}]]}, {"columns": ["a"], "rows": [[1, 2]]},
    {"columns": ["a"], "rows": []}, {"columns": ["a"], "rows": [[1]], "config": {"normalize_numeric": {}}},
    {"columns": ["a"], "rows": [[1]], "config": {"handle_outliers": {"method": "bad"}}},
    {"columns": ["a"], "rows": [[1]], "config": {"remove_duplicates": {"keep": True}}},
    {"columns": ["a"], "rows": [[1]], "config": {"handle_missing_values": {"fill_value": {"x": 1}}}},
    {"columns": ["a"], "rows": [[1]], "config": {"trim_whitespace": {"columns": ["missing"]}}},
    {"columns": ["a"], "rows": [[1]], "unexpected": True},
])
def test_bad_inputs(client, payload):
    assert client.post("/api/preprocessing/run/", payload, format="json").status_code == 400


def test_failed_step_not_successful_partial_output(client):
    payload = {"columns": ["a"], "rows": [[1], [2]], "config": {
        "validate_required_columns": {"required_columns": ["missing"]}}}
    response = client.post("/api/preprocessing/run/", payload, format="json")
    assert response.status_code == 422
    body = response.json()
    assert body["report"]["rows_before"] == 2
    assert body["report"]["per_step"]["validate_required_columns"]["status"] == "error"
    assert "rows" not in body


def test_constant_columns_do_not_silently_produce_empty_output(client):
    assert client.post("/api/preprocessing/run/", {"columns": ["a"], "rows": [[1]]}, format="json").status_code == 422
    response = client.post("/api/preprocessing/run/", {"columns": ["a"], "rows": [[1]],
        "config": {"remove_constant_columns": {"enabled": False}}}, format="json")
    assert response.status_code == 200


def test_required_column_removed_by_cleaning_is_rejected(client):
    response = client.post("/api/preprocessing/run/", {"columns": ["id", "amount"], "rows": [[1, 10], [1, 20]],
        "config": {"validate_required_columns": {"required_columns": ["id"]}}}, format="json")
    assert response.status_code == 422


def test_non_normalizing_outlier_behavior_is_preserved(client):
    payload = {"columns": ["amount"], "rows": [[1], [2], [3], [4], [100000]]}
    capped = client.post("/api/preprocessing/run/", payload, format="json").json()
    assert capped["rows"][-1][0] < 100000  # Existing IQR cap default is explicit.
    payload["config"] = {"handle_outliers": {"enabled": False}}
    assert client.post("/api/preprocessing/run/", payload, format="json").json()["rows"][-1] == [100000]


def test_row_limit_and_json_limit(client):
    with patch("core.api.MAX_ROWS", 1):
        assert client.post("/api/preprocessing/run/", {"columns": ["a"], "rows": [[1], [2]]}, format="json").status_code == 413
    with patch("core.api.MAX_BYTES", 10):
        assert client.post("/api/preprocessing/run/", {"columns": ["a"], "rows": [[1], [2]]}, format="json").status_code == 413


def test_missing_output_uses_json_null(client):
    payload = {"columns": ["a"], "rows": [[1], [None], [2]], "config": {"handle_missing_values": {"enabled": False}}}
    response = client.post("/api/preprocessing/run/", payload, format="json")
    assert response.status_code == 200
    assert response.json()["rows"][1] == [None]
    assert response.json()["remaining_missing_values"] == 1


def test_authentication_and_method(client):
    assert APIClient().get("/api/preprocessing/config/").status_code == 401
    assert client.get("/api/preprocessing/run/").status_code == 405
