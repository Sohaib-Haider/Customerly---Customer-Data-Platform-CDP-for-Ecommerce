"""Command line: compute FYP1 KPI cards.

Run from backend/ (reads DATABASE_URL from the environment or backend/.env):

    python -m apps.analytics.kpis                  # all 19 cards
    python -m apps.analytics.kpis --card 3 9       # selected cards (id or key)
    python -m apps.analytics.kpis --category Revenue
    python -m apps.analytics.kpis --card 1 --by brand
    python -m apps.analytics.kpis --json > kpis.json
    python -m apps.analytics.kpis --sql 3          # print a card's SQL
"""
from __future__ import annotations

import argparse
import json
import sys

from .cards import CARDS, breakdown_sql
from .engine import connect, resolve, run_card


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _print(result: dict, limit: int) -> None:
    title = f"[{result['id']:>2}] {result['name']}"
    if result["dimension"]:
        title += f"  (by {result['dimension']})"
    print(f"\n{title}  -  {result['category']}")
    for source, data in result["results"].items():
        meta = result["sources"][source]
        label = f"{source} ({meta['name']}{', ' + meta['currency'] if meta['currency'] else ''})"
        if isinstance(data, dict):
            print(f"    {label}: " + ", ".join(f"{k}={_fmt(v)}" for k, v in data.items()))
        else:
            print(f"    {label}: {len(data)} rows")
            for row in data[:limit]:
                print("        " + ", ".join(f"{k}={_fmt(v)}" for k, v in row.items()))
            if len(data) > limit:
                print(f"        ... {len(data) - limit} more")
    for source, reason in result["unavailable"].items():
        print(f"    {source}: unavailable - {reason}")
    if result["note"]:
        print(f"    note: {result['note']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--card", nargs="+", help="card ids or keys (default: all)")
    ap.add_argument("--category", help="only cards in this category, e.g. Revenue")
    ap.add_argument("--by", help="breakdown for card 1 or 12: month, week, region, brand, department, category")
    ap.add_argument("--store-id", help="limit to one store (UUID)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--rows", type=int, default=12, help="rows shown per source for series/table cards")
    ap.add_argument("--sql", metavar="CARD", help="print the SQL of one card and exit")
    args = ap.parse_args(argv)

    if args.sql:
        card = resolve(args.sql)
        print(breakdown_sql(card, args.by) if args.by else card.sql)
        return 0

    cards = [resolve(c) for c in args.card] if args.card else CARDS
    if args.category:
        cards = [c for c in cards if c.category.lower() == args.category.lower()]
    if args.by and len(cards) != 1:
        ap.error("--by needs exactly one card (1 or 12)")

    with connect() as conn:
        results = [run_card(conn, c, store_id=args.store_id, dimension=args.by) for c in cards]

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        print()
    else:
        for result in results:
            _print(result, args.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
