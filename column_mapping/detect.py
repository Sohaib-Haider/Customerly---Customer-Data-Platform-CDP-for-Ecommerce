"""Source/dataset kind detection — reviewable, multi-entity aware.

A single store CSV can mix entities (a Dunnhumby-style transactions file
carries customers, orders, order_items and products columns at once), so the
detector returns a RANKED list of kinds with scores, not one opaque label:

* ``primary`` — highest-scoring kind, or "unknown" below the floor.
* ``scores``  — everything that matched, for reviewer inspection.
* ``review_required`` — set when a runner-up is close (multi-entity file) or
  nothing clears the floor.
* ``column_kinds`` — which kind each column's signals contributed, so a
  reviewer can see WHY the file was classified as it was.

The primary kind can be overridden at map time (``--kind``) and at review
time (``review --kind``); overrides are recorded in the output document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

KINDS: tuple[str, ...] = (
    "customers",
    "transactions",
    "orders",
    "products",
    "events",
    "campaigns",
    "messages",
    "coupons",
    "holidays",
    "unknown",
)

KIND_UNKNOWN = "unknown"

# Normalized substring signals per kind. A column matches a kind when any
# signal occurs in its normalized name. Chosen from documented source columns
# (segments-schema-mapping.md "Dataset Files & Attributes" tables).
SIGNALS: dict[str, tuple[str, ...]] = {
    "customers": (
        "household", "customer", "client", "tenure", "satisfaction", "cashback",
        "complain", "churn", "marital", "income", "age", "address", "device",
        "warehouse", "firstpurchasedate", "lastname", "firstname", "phone",
        "email", "loyalty",
    ),
    "transactions": (
        "basket", "ticket", "txn", "transaction", "salesvalue", "netsales",
        "retaildisc", "coupondisc", "unitssold", "quantity", "weekno", "dayno",
        "storeno", "storeid", "transno", "basketid",
    ),
    "orders": ("invoice", "orderno", "orderdate", "purchaseorder"),
    "products": (
        "product", "prodsku", "sku", "item", "brand", "department", "dept",
        "commodity", "category", "description", "desc", "price", "stockcode",
    ),
    "events": ("event", "session", "view", "cart", "click", "browse", "user"),
    "campaigns": ("campaign", "topic", "abtest", "warmup"),
    "messages": (
        "message", "sentat", "opened", "clicked", "bounce", "unsubscribe",
        "blocked", "recipient",
    ),
    "coupons": ("coupon", "redemption", "upc"),
    "holidays": ("holiday", "festive"),
}

# Signals that are too generic to vote on their own — they only count when
# another column of the same kind already matched (avoids `date` voting for
# holidays in every file).
_WEAK_SIGNALS = frozenset({"date", "name", "status", "type", "channel", "price"})

MIN_KIND_SCORE = 0.15  # below this the primary is "unknown"
REVIEW_RATIO = 0.7  # runner-up >= 70% of top score => review_required


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class KindDetection:
    primary: str = KIND_UNKNOWN
    scores: dict[str, float] = field(default_factory=dict)
    review_required: bool = True
    column_kinds: dict[str, str] = field(default_factory=dict)
    override: str | None = None

    @property
    def effective_kind(self) -> str:
        return self.override or self.primary

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_kind": self.primary,
            "override_kind": self.override,
            "effective_kind": self.effective_kind,
            "review_required": self.review_required,
            "scores": {k: round(v, 3) for k, v in sorted(self.scores.items()) if v > 0},
            "column_kinds": dict(self.column_kinds),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KindDetection":
        return cls(
            primary=data.get("primary_kind", KIND_UNKNOWN),
            scores=dict(data.get("scores", {})),
            review_required=bool(data.get("review_required", True)),
            column_kinds=dict(data.get("column_kinds", {})),
            override=data.get("override_kind"),
        )


def detect_kinds(columns: list[str]) -> KindDetection:
    """Score entity kinds from column-name signals. Pure, no I/O."""
    votes: dict[str, set[str]] = {kind: set() for kind in KINDS if kind != KIND_UNKNOWN}
    column_kinds: dict[str, str] = {}

    normalized = [(original, _normalize(original)) for original in columns]
    for original, norm in normalized:
        best_kind: str | None = None
        best_len = 0
        for kind, signals in SIGNALS.items():
            for signal in signals:
                if signal in norm and (signal not in _WEAK_SIGNALS or _has_stronger_sibling(norm, signal)):
                    # strongest (longest) matching signal wins for this column
                    if len(signal) > best_len:
                        best_len, best_kind = len(signal), kind
        if best_kind is None:
            # weak-signal-only columns: attach to no kind (reported as unknown)
            column_kinds[original] = KIND_UNKNOWN
            continue
        votes[best_kind].add(original)
        column_kinds[original] = best_kind

    total = max(1, len(columns))
    scores = {kind: len(voters) / total for kind, voters in votes.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    primary, top = ranked[0] if ranked else (KIND_UNKNOWN, 0.0)
    if top < MIN_KIND_SCORE:
        primary = KIND_UNKNOWN

    runner_up = 0.0
    for kind, score in ranked[1:]:
        if kind != primary:
            runner_up = score
            break
    review_required = (
        primary == KIND_UNKNOWN
        or runner_up >= REVIEW_RATIO * max(top, 1e-9)
        or top < 0.5 and len(columns) > 3  # mixed file with weak signal coverage
    )

    return KindDetection(
        primary=primary,
        scores={k: v for k, v in scores.items() if v > 0},
        review_required=review_required,
        column_kinds=column_kinds,
    )


def _has_stronger_sibling(norm: str, signal: str) -> bool:
    """Weak signals only vote when paired with a non-weak signal in the name.

    Example: 'start_date' — 'date' is weak, 'start' is not part of any kind
    signal, so coupon campaigns don't win votes from every dated file, while
    'holiday_date' still votes for holidays because... it does not either;
    holidays are detected via the strong 'holiday' signal. This keeps weak
    words from flapping the primary kind on generic columns.
    """
    if signal not in _WEAK_SIGNALS:
        return True
    # weak signal must co-occur with a strong signal of the same kind
    for kind, signals in SIGNALS.items():
        if signal in signals:
            return any(s in norm for s in signals if s not in _WEAK_SIGNALS)
    return False
