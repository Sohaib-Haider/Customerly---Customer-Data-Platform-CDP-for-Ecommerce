"""Validation for transformed canonical frames, before anything is loaded.

Enforces the constraints that backend/schema.sql actually declares (NOT NULL,
primary keys, unique constraints, foreign keys) plus the calendar invariants
this transform promises. Checks that cannot be satisfied are collected rather
than raised, so one bad constraint does not hide the other findings.

Deliberately NOT checked here: value vocabularies (stores.plan, role, event_type,
channel) — schema.sql declares no CHECK constraints for them because the source
documentation conflicts (final-schema.md Open Decision #6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def check(self, condition: bool, message: str, warn_only: bool = False) -> None:
        if condition:
            self.passed.append(message)
        elif warn_only:
            self.warnings.append(message)
        else:
            self.failures.append(message)

    def render(self) -> str:
        lines = [f"VALIDATION: {'PASS' if self.ok else 'FAIL'}"]
        lines.append(f"  {len(self.passed)} checks passed, "
                     f"{len(self.warnings)} warnings, {len(self.failures)} failures")
        for item in self.failures:
            lines.append(f"  [FAIL] {item}")
        for item in self.warnings:
            lines.append(f"  [WARN] {item}")
        return "\n".join(lines)


# NOT NULL columns per table, from backend/schema.sql (columns with DEFAULT
# excluded for non-key fields; keys and FKs are always required).
NOT_NULL = {
    "stores": ["id", "name", "plan"],
    "customers": ["id", "store_id", "source_customer_id"],
    "products": ["id", "store_id", "source_product_id", "name"],
    "orders": ["id", "store_id", "source_order_id", "customer_id", "order_date"],
    "order_items": ["id", "order_id", "product_id", "quantity", "unit_price", "sales_value",
                    "retail_disc", "coupon_disc", "coupon_match_disc", "other_discount"],
    "causal_data": ["id", "store_id", "product_id", "source_store_id", "week_no"],
    "coupon_campaigns": ["id", "store_id", "source_campaign_id", "campaign_type",
                         "start_date", "end_date"],
    "coupon_redemptions": ["id", "store_id", "coupon_id", "campaign_id", "customer_id",
                           "redemption_status"],
    # Member B: events (Segments 1/9). product_id is nullable, so it is not here.
    "events": ["id", "store_id", "customer_id", "session_id", "event_type",
               "event_timestamp"],
}

UNIQUE = {
    "stores": [["id"]],
    "customers": [["store_id", "source_customer_id"]],
    "products": [["store_id", "source_product_id"]],
    "orders": [["store_id", "source_order_id"]],
    "causal_data": [["store_id", "source_store_id", "product_id", "week_no"]],
    "coupon_campaigns": [["store_id", "source_campaign_id"]],
    "coupon_redemptions": [],
    "order_items": [],
    "events": [],
}

# (child table, child column, parent table, parent column)
FOREIGN_KEYS = [
    ("customers", "store_id", "stores", "id"),
    ("products", "store_id", "stores", "id"),
    ("orders", "store_id", "stores", "id"),
    ("orders", "customer_id", "customers", "id"),
    ("order_items", "order_id", "orders", "id"),
    ("order_items", "product_id", "products", "id"),
    ("causal_data", "store_id", "stores", "id"),
    ("causal_data", "product_id", "products", "id"),
    ("coupon_campaigns", "store_id", "stores", "id"),
    ("coupon_redemptions", "store_id", "stores", "id"),
    ("coupon_redemptions", "campaign_id", "coupon_campaigns", "id"),
    ("coupon_redemptions", "customer_id", "customers", "id"),
    # Member B: events
    ("events", "store_id", "stores", "id"),
    ("events", "customer_id", "customers", "id"),
    ("events", "product_id", "products", "id"),
]


def validate(frames: dict[str, pd.DataFrame], raw_counts: dict[str, int],
             dropped: dict[str, int],
             dropped_by_source: dict[str, int] | None = None,
             anchor: date | None = None,
             columns: dict[str, list[str]] | None = None) -> Report:
    """Check a dataset's output against schema.sql.

    `anchor` is that dataset's declared day-number anchor. Pass it only for a
    source with native day numbers; a source with real dates passes None and the
    calendar check is skipped (it has no anchor to be consistent with).

    `columns` is the table -> column-order map for the tables this transform is
    expected to emit. Defaults to the Dunnhumby set, so Member A's behaviour is
    unchanged. Member B passes its own subset (stores, customers, products,
    orders, order_items, events) so that tables it never emits are not reported
    as "missing".
    """
    report = Report()
    expected = columns if columns is not None else _default_columns()

    # ---- structure ------------------------------------------------------
    for table in expected:
        cols = NOT_NULL.get(table, [])
        frame = frames.get(table)
        if frame is None:
            report.failures.append(f"{table}: missing from output")
            continue
        report.check(
            list(frame.columns) == _expected_order(table, expected),
            f"{table}: column order matches schema.sql",
        )
        for col in cols:
            missing = int(frame[col].isna().sum()) if col in frame.columns else len(frame)
            if missing:
                report.failures.append(
                    f"{table}.{col}: {missing} NULL value(s) violate NOT NULL"
                )
            else:
                report.passed.append(f"{table}.{col}: NOT NULL satisfied")

    # ---- primary / unique keys -----------------------------------------
    for table, groups in UNIQUE.items():
        frame = frames.get(table)
        if frame is None:
            continue
        for cols in groups:
            if any(c not in frame.columns for c in cols):
                report.failures.append(f"{table}: unique key columns absent: {cols}")
                continue
            dupes = int(frame.duplicated(subset=cols).sum())
            report.check(
                dupes == 0,
                f"{table}: UNIQUE({'+'.join(cols)}) holds "
                f"({len(frame)} rows)",
            )
            if dupes:
                report.failures.append(
                    f"{table}: {dupes} duplicate row(s) on UNIQUE({'+'.join(cols)})"
                )

    # ---- foreign keys ---------------------------------------------------
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        if child not in frames or parent not in frames:
            continue
        cf, pf = frames[child], frames[parent]
        if ccol not in cf.columns or pcol not in pf.columns:
            report.failures.append(f"{child}.{ccol} -> {parent}.{pcol}: column missing")
            continue
        # A NULL child key is a legal absent reference (e.g. events.product_id is
        # nullable), not an orphan — exclude it before the membership test.
        child_values = cf[ccol].dropna()
        orphans = int((~child_values.isin(set(pf[pcol]))).sum())
        report.check(
            orphans == 0,
            f"{child}.{ccol} -> {parent}.{pcol}: no orphans",
        )
        if orphans:
            report.failures.append(
                f"{child}.{ccol}: {orphans} value(s) not present in {parent}.{pcol}"
            )

    # ---- calendar invariants (day-number sources only) ------------------
    orders = frames.get("orders")
    if orders is not None and len(orders):
        if anchor is None:
            report.warnings.append(
                "calendar check skipped — no anchor declared for this dataset "
                "(expected for a source with real dates)"
            )
        else:
            shifted = [
                d for d, n in zip(orders["order_date"], orders["day_no"])
                if d is not None and n == n and n is not None
                and pd.Timestamp(d).date() != anchor + pd.to_timedelta(int(n) - 1, unit="D")
            ]
            report.check(
                not shifted,
                f"orders.order_date == anchor({anchor.isoformat()}) + (day_no - 1) for every row",
            )
            if shifted:
                report.failures.append(
                    f"orders: {len(shifted)} row(s) off the {anchor.isoformat()} timeline"
                )

        week = orders["week_no"].dropna()
        bad_week = int(((week < 1) | (week > 104)).sum())
        report.check(bad_week == 0, "orders.week_no within documented 1-104 range", warn_only=True)
        if bad_week:
            report.warnings.append(f"orders: {bad_week} row(s) with week_no outside 1-104")

        day = orders["day_no"].dropna()
        bad_day = int(((day < 1) | (day > 711)).sum())
        report.check(bad_day == 0, "orders.day_no within documented 1-711 range", warn_only=True)
        if bad_day:
            report.warnings.append(f"orders: {bad_day} row(s) with day_no outside 1-711")

    # ---- source reconciliation: nothing lost without a recorded reason ---
    # Per-source drops come from the loader's source tag. Guessing from the reason
    # string misattributed coupon drops to transactions; keep the string fallback
    # only for callers that pass no tag map at all.
    dropped_by_source = dropped_by_source or {}
    accounted = sum(dropped.values())
    emitted_tx_lines = len(frames.get("order_items", []))
    if "transaction_data.csv" in raw_counts:
        raw_lines = raw_counts["transaction_data.csv"]
        tx_drops = dropped_by_source.get("transaction_data.csv")
        if tx_drops is None:
            tx_drops = sum(
                v for k, v in dropped.items()
                if not k.startswith(("causal", "campaigns", "redemptions", "duplicate PRODUCT"))
                and not k.startswith("duplicate (customer")
            )
        report.warnings.append(
            f"row accounting: transaction_data.csv {raw_lines:,} raw lines -> "
            f"{emitted_tx_lines:,} order_items, {tx_drops:,} line-level drops recorded"
        )
        if raw_lines - tx_drops != emitted_tx_lines:
            report.failures.append(
                f"transaction_data.csv accounting mismatch: {raw_lines:,} raw - "
                f"{tx_drops:,} dropped = {raw_lines - tx_drops:,}, but "
                f"{emitted_tx_lines:,} order_items were emitted"
            )

    # ---- soft warnings worth showing -----------------------------------
    if frames.get("orders") is not None and "region" in frames["orders"]:
        if int(frames["orders"]["region"].notna().sum()) == 0:
            report.warnings.append(
                "orders.region is entirely NULL — Open Decision #4 (no geo column in this dataset); "
                "Revenue by Region / Orders by Region KPI cards will be empty for this tenant"
            )
    if frames.get("products") is not None and "unit_price" in frames["products"]:
        if int(frames["products"]["unit_price"].notna().sum()) == 0:
            report.warnings.append(
                "products.unit_price is entirely NULL — Open Decision #11; "
                "Average Product Price KPI will be empty for this tenant"
            )
    if accounted:
        report.warnings.append(
            f"{accounted} source row(s) dropped in total across all inputs (see audit trail)"
        )
    if dropped_by_source:
        breakdown = ", ".join(
            f"{src}: {n:,}"
            for src, n in sorted(dropped_by_source.items(), key=lambda kv: -kv[1])
        )
        report.warnings.append(f"drops by source file -> {breakdown}")
    return report


def _default_columns() -> dict[str, list[str]]:
    from dunnhumby import COLUMNS

    return COLUMNS


def _expected_order(table: str, columns: dict[str, list[str]]) -> list[str]:
    return columns[table]
