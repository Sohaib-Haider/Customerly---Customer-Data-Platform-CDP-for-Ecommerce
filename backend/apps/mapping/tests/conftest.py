"""Shared fixtures + import bootstrap for column_mapping tests.

Run from the repo root:
    .venv\\Scripts\\python -m pytest apps/mapping/tests   (from backend/)

All Gemini interactions are mocked (FakeLLM below). The suite touches the
network never and the real API key never.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

# Make `import apps.mapping` work regardless of pytest's rootdir handling:
# the Django project root (backend/) goes on sys.path.
BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apps.mapping.llm import LLMOutcome, LLMSuggestion  # noqa: E402


@pytest.fixture(scope="session")
def catalog():
    from apps.mapping.schema_catalog import get_catalog

    return get_catalog()


@pytest.fixture
def sample_csv() -> Path:
    return BACKEND_DIR / "apps" / "mapping" / "samples" / "store_upload_sample.csv"


def make_csv(tmp_path: Path, columns: list[str], rows: int = 3, prefix: str = "src") -> Path:
    """Write a tiny CSV with the given header and filler rows."""
    path = tmp_path / f"{prefix}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for index in range(rows):
            # deterministic filler values per column
            writer.writerow([f"{col}_{index}" for col in columns])
    return path


@pytest.fixture
def csv_factory(tmp_path):
    return make_csv


class FakeLLM:
    """In-process stand-in for Gemini implementing the same `suggest` contract.

    answers: source_column -> (table, column, confidence, explanation) OR
             (None, None, confidence, explanation) for an explicit "no target".
             Columns without an entry get no suggestion at all.
    """

    def __init__(self, answers: dict | None = None, *, model: str = "fake-llm",
                 dataset_kind: str | None = None, error: Exception | None = None):
        self.answers = answers or {}
        self.model = model
        self.dataset_kind = dataset_kind
        self.error = error
        self.calls: list[list[str]] = []  # chunks of source columns seen

    def suggest(self, pending, catalog, detection):
        self.calls.append([p.source_column for p in pending])
        if self.error is not None:
            raise self.error
        suggestions = []
        for column in pending:
            answer = self.answers.get(column.source_column)
            if answer is None:
                continue
            table, target, confidence, explanation = answer
            suggestions.append(
                LLMSuggestion(
                    source_column=column.source_column,
                    target_table=table,
                    target_column=target,
                    confidence=confidence,
                    explanation=explanation,
                )
            )
        return LLMOutcome(suggestions=suggestions, dataset_kind=self.dataset_kind)


@pytest.fixture
def fake_llm_factory():
    return FakeLLM
