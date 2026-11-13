"""Tests for the FYP1 KPI cards.

Definition tests run anywhere. Database tests run every card against a real
PostgreSQL loaded with backend/schema.sql and the demo slice; they are skipped
unless KPI_TEST_DATABASE_URL is set, e.g.

    KPI_TEST_DATABASE_URL=postgresql://... python -m pytest apps/analytics/tests   (from backend/)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apps.analytics.kpis.cards import CARDS, breakdown_sql  # noqa: E402
from apps.analytics.kpis.sources import SOURCES  # noqa: E402

DB_URL = os.environ.get("KPI_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="KPI_TEST_DATABASE_URL not set")


# --------------------------------------------------------------- definitions
def test_fyp1_has_19_cards_numbered_1_to_19():
    assert [c.id for c in CARDS] == list(range(1, 20))
    assert len({c.key for c in CARDS}) == 19


@pytest.mark.parametrize("card", CARDS, ids=lambda c: c.key)
def test_card_definition_is_consistent(card):
    assert card.kind in ("value", "series", "table")
    assert card.sources, "a card must support at least one source"
    assert set(card.sources) <= set(SOURCES)
    assert set(card.unavailable) <= set(SOURCES)
    assert not set(card.sources) & set(card.unavailable), "a source cannot be both supported and unavailable"
    assert "%(store_id)s" in card.sql, "every card must be scoped to a store"
    assert " source" in card.sql.lower(), "every card must return a source column"


def test_coupon_orders_never_feed_basket_metrics():
    basket_cards = {"average_order_value", "repeat_purchase_rate", "total_orders", "avg_items_per_order"}
    for card in CARDS:
        if card.key in basket_cards:
            assert "cpn" not in card.sources and "cpn" in card.unavailable


def test_breakdowns_only_on_cards_1_and_12():
    assert {c.id for c in CARDS if c.dimensions} == {1, 12}
    card1 = next(c for c in CARDS if c.id == 1)
    for dim in card1.dimensions:
        assert "%(store_id)s" in breakdown_sql(card1, dim)
    with pytest.raises(ValueError):
        breakdown_sql(card1, "colour")


# ------------------------------------------------------------------ database
@pytest.fixture(scope="module")
def conn():
    from apps.analytics.kpis.engine import connect

    with connect(DB_URL) as connection:
        yield connection


@needs_db
@pytest.mark.parametrize("card", CARDS, ids=lambda c: c.key)
def test_card_runs_and_reports_every_source(conn, card):
    from apps.analytics.kpis.engine import run_card

    result = run_card(conn, card)
    assert set(result["results"]) | set(result["unavailable"]) >= set(card.sources)
    assert not set(result["results"]) & set(result["unavailable"])
    assert result["results"], f"card {card.id} returned no data for any source"


@needs_db
@pytest.mark.parametrize("card", [c for c in CARDS if c.unit == "percent"], ids=lambda c: c.key)
def test_percentages_are_between_0_and_100(conn, card):
    from apps.analytics.kpis.engine import run_card

    for rows in run_card(conn, card)["results"].values():
        for row in rows if isinstance(rows, list) else [rows]:
            for key, value in row.items():
                if key.endswith(("_rate", "_share")) and value is not None:
                    assert 0 <= value <= 100, (key, value)


@needs_db
@pytest.mark.parametrize("dimension", ["month", "week", "region", "brand", "department", "category"])
def test_revenue_breakdowns_run(conn, dimension):
    from apps.analytics.kpis.engine import run_card

    card = next(c for c in CARDS if c.id == 1)
    result = run_card(conn, card, dimension=dimension)
    assert result["dimension"] == dimension
    assert set(result["results"]) | set(result["unavailable"]) >= set(card.sources)


@needs_db
def test_store_filter_excludes_other_stores(conn):
    from apps.analytics.kpis.engine import run_card

    card = next(c for c in CARDS if c.key == "total_customers")
    result = run_card(conn, card, store_id="00000000-0000-0000-0000-000000000000")
    assert result["results"] == {}
