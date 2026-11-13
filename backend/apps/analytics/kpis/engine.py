"""Run KPI cards against PostgreSQL and shape the results for the dashboard.

Each result is plain JSON-friendly data::

    {
      "id": 3, "key": "average_order_value", "name": "Average Order Value",
      "category": "Revenue", "kind": "value", "unit": "currency",
      "results": {"ret": {...}, "dnb": {...}},      # per source
      "unavailable": {"cpn": "reason ..."},          # never shown as zero
      "sources": {"ret": {"name": ..., "currency": "GBP"}, ...},
      "note": "..."
    }

``value`` cards give one row per source, ``series``/``table`` cards a list.
"""
from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .cards import BY_ID, BY_KEY, CARDS, VALUE, Card, breakdown_sql
from .sources import NO_ORDERS, SOURCES

BACKEND_DIR = Path(__file__).resolve().parents[3]
ENV_FILES = (BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env")


def database_url() -> str:
    """DATABASE_URL from the environment, else from backend/.env (gitignored)."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for env_file in ENV_FILES:
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                key, sep, value = line.strip().partition("=")
                if sep and key.strip() == "DATABASE_URL":
                    return value.strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL is not set (environment or backend/.env)")


def connect(url: str | None = None):
    import psycopg  # imported lazily so the card definitions load without it

    return psycopg.connect(url or database_url(), autocommit=True)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and value.as_tuple().exponent >= 0 else float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _rows(conn, sql: str, store_id: str | None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, {"store_id": store_id})
        names = [d[0] for d in cur.description]  # works for psycopg and Django cursors
        return [{n: _plain(v) for n, v in zip(names, row)} for row in cur.fetchall()]


def resolve(card: int | str) -> Card:
    if isinstance(card, int) or str(card).isdigit():
        return BY_ID[int(card)]
    return BY_KEY[str(card)]


def run_card(conn, card: Card, *, store_id: str | None = None, dimension: str | None = None) -> dict[str, Any]:
    sql = breakdown_sql(card, dimension) if dimension else card.sql
    rows = _rows(conn, sql, store_id)

    results: dict[str, Any] = {}
    for row in rows:
        source = row.pop("source")
        if source not in card.sources:
            continue
        if card.kind == VALUE and not dimension:
            results[source] = row
        else:
            results.setdefault(source, []).append(row)

    unavailable = dict(card.unavailable)
    for source in card.sources:
        if source not in results:
            unavailable[source] = (
                f"No '{dimension}' values in this source." if dimension
                else NO_ORDERS if card.category in ("Revenue", "Order", "Product")
                else "No data for this source."
            )

    return {
        "id": card.id,
        "key": card.key,
        "name": card.name,
        "category": card.category,
        "kind": card.kind if not dimension else "table",
        "unit": card.unit,
        "dimension": dimension,
        "results": results,
        "unavailable": unavailable,
        "sources": {
            code: {"name": SOURCES[code].name, "currency": SOURCES[code].currency}
            for code in [*results, *unavailable]
        },
        "note": card.note,
    }


def run_cards(cards: Iterable[int | str] | None = None, *, store_id: str | None = None,
              url: str | None = None) -> list[dict[str, Any]]:
    selected = [resolve(c) for c in cards] if cards else CARDS
    with connect(url) as conn:
        return [run_card(conn, card, store_id=store_id) for card in selected]
