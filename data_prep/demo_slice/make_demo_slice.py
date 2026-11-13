"""Build a small, referentially complete demo slice of the processed datasets.

The full processed output (data_prep/processed/) is ~21 GB, far beyond free
hosted PostgreSQL tiers (e.g. Supabase Free: 500 MB). For KPI cards and the
Customer 360 view we only need a few thousand *complete* customers, so this
script samples whole customers per source dataset and keeps everything that
belongs to them:

    customers -> orders -> order_items -> products
    customers -> events (behavior) -> products
    customers -> message_sends -> marketing_campaigns
    customers -> coupon_redemptions -> coupon_campaigns

Customers are drawn uniformly at random within each dataset (one-time buyers
and non-buyers included), so rates computed on the slice (repeat purchase,
conversion funnel, open rates, ...) stay representative of the full data.

Small reference tables (stores, marketing_campaigns, coupon_campaigns,
holidays) are kept whole. causal_data is limited to the best-selling sliced
Dunnhumby products. Every foreign key in the output resolves inside the
output, so the slice loads into backend/schema.sql without errors.

Output: one CSV per canonical table in data_prep/processed/demo_slice/,
ready for load_demo_slice.sh.

Usage (from the repo root):
    python data_prep/demo_slice/make_demo_slice.py
    python data_prep/demo_slice/make_demo_slice.py --behavior 1000 --messaging 1500
"""
from __future__ import annotations

import argparse
import random
from collections import Counter
from pathlib import Path

import pandas as pd

PROCESSED = Path("data_prep/processed")
DH = "dunnhumby-the-complete-journey"
BEH = "ecommerce-behavior-data-from-multi-category-store"
RET = "online-retail-ii-uci"
CPN = "predicting-coupon-redemption"
CHR = "e-commerce-customer-churn"
MSG = "direct-messaging"
CHUNK = 500_000


def path(dataset: str, table: str) -> Path:
    return PROCESSED / dataset / table / f"{table}.csv"


def read(dataset: str, table: str) -> pd.DataFrame:
    return pd.read_csv(path(dataset, table), dtype=str, keep_default_na=False)


def stream_filter(dataset: str, table: str, column: str, keep: set[str]) -> pd.DataFrame:
    parts = [
        chunk[chunk[column].isin(keep)]
        for chunk in pd.read_csv(path(dataset, table), dtype=str, keep_default_na=False, chunksize=CHUNK)
    ]
    return pd.concat(parts, ignore_index=True)


def bucket(ids: pd.Series, modulo: int) -> pd.Series:
    """Deterministic pseudo-random bucket from the UUID's first 8 hex digits."""
    return ids.str[:8].map(lambda h: int(h, 16) % modulo if h else -1)


def sample(ids, n: int, rng: random.Random) -> set[str]:
    ids = sorted(set(ids))
    return set(ids) if len(ids) <= n else set(rng.sample(ids, n))


def order_dataset(dataset: str, n_customers: int, rng: random.Random):
    """Sample customers uniformly among those with any order; keep their history."""
    orders = read(dataset, "orders")
    chosen = sample(orders["customer_id"].unique(), n_customers, rng)
    orders = orders[orders["customer_id"].isin(chosen)]
    items = stream_filter(dataset, "order_items", "order_id", set(orders["id"]))
    customers = read(dataset, "customers")
    customers = customers[customers["id"].isin(chosen)]
    return customers, orders, items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dunnhumby", type=int, default=200, help="households (of 2,500)")
    ap.add_argument("--retail", type=int, default=800, help="Online Retail II customers (of 5,942)")
    ap.add_argument("--coupon", type=int, default=150, help="coupon-dataset customers (of 1,582)")
    ap.add_argument("--behavior", type=int, default=2000, help="behavior-event customers (of 3.4M)")
    ap.add_argument("--messaging", type=int, default=3000, help="messaging customers (of 4.9M)")
    ap.add_argument("--churn", type=int, default=0, help="churn customers; 0 = all 3,941")
    ap.add_argument("--causal-products", type=int, default=300, help="top sliced Dunnhumby products kept in causal_data")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=PROCESSED / "demo_slice")
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)

    out: dict[str, list[pd.DataFrame]] = {}

    def add(table: str, frame: pd.DataFrame) -> None:
        out.setdefault(table, []).append(frame)

    # --- order-based datasets -------------------------------------------------
    product_ids: dict[str, set[str]] = {}
    for dataset, n in ((DH, args.dunnhumby), (RET, args.retail), (CPN, args.coupon)):
        customers, orders, items = order_dataset(dataset, n, rng)
        add("customers", customers)
        add("orders", orders)
        add("order_items", items)
        product_ids[dataset] = set(items["product_id"])
        print(f"{dataset:<52} customers {len(customers):>6,}  orders {len(orders):>8,}  items {len(items):>9,}")
        if dataset == DH:
            dh_items = items

    # --- behavior events -------------------------------------------------------
    # A deterministic ~2% customer bucket, then a uniform sample from it.
    pool = []
    for chunk in pd.read_csv(path(BEH, "events"), dtype=str, keep_default_na=False, chunksize=CHUNK):
        pool.append(chunk[bucket(chunk["customer_id"], 50) == 0])
    pool = pd.concat(pool, ignore_index=True)
    buyers = set(pool.loc[pool["event_type"] == "purchase", "customer_id"])
    chosen = sample(pool["customer_id"].unique(), args.behavior, rng)
    events = pool[pool["customer_id"].isin(chosen)]
    customers = stream_filter(BEH, "customers", "id", chosen)
    add("customers", customers)
    add("events", events)
    product_ids[BEH] = set(events["product_id"]) - {""}
    print(f"{BEH:<52} customers {len(customers):>6,}  events {len(events):>9,}  (buyers {len(buyers & chosen):,})")

    # --- products referenced by the slice -------------------------------------
    for dataset, ids in product_ids.items():
        products = stream_filter(dataset, "products", "id", ids)
        add("products", products)
        print(f"{dataset:<52} products  {len(products):>6,}")

    # --- coupons (both coupon datasets) ---------------------------------------
    sliced_customers = set(pd.concat(out["customers"])["id"])
    for dataset in (DH, CPN):
        add("coupon_campaigns", read(dataset, "coupon_campaigns"))
        red = read(dataset, "coupon_redemptions")
        add("coupon_redemptions", red[red["customer_id"].isin(sliced_customers)])

    # --- churn snapshot customers ---------------------------------------------
    churn = read(CHR, "customers")
    if args.churn:
        churn = churn.sample(n=min(args.churn, len(churn)), random_state=args.seed)
    add("customers", churn)
    print(f"{CHR:<52} customers {len(churn):>6,}")

    # --- messaging --------------------------------------------------------------
    pool = []
    for chunk in pd.read_csv(path(MSG, "message_sends"), dtype=str, keep_default_na=False, chunksize=CHUNK):
        pool.append(chunk[bucket(chunk["customer_id"], 20) == 0])
    pool = pd.concat(pool, ignore_index=True)
    chosen = sample(pool["customer_id"].unique(), args.messaging, rng)
    sends = pool[pool["customer_id"].isin(chosen)]
    customers = stream_filter(MSG, "customers", "id", chosen)
    add("customers", customers)
    add("message_sends", sends)
    add("marketing_campaigns", read(MSG, "marketing_campaigns"))
    add("holidays", read(MSG, "holidays"))
    print(f"{MSG:<52} customers {len(customers):>6,}  message_sends {len(sends):>8,}")

    # --- causal promotions for the best-selling sliced Dunnhumby products ------
    qty = Counter()
    for pid, q in zip(dh_items["product_id"], pd.to_numeric(dh_items["quantity"], errors="coerce").fillna(0)):
        qty[pid] += q
    top = {pid for pid, _ in qty.most_common(args.causal_products)}
    causal = stream_filter(DH, "causal_data", "product_id", top)
    add("causal_data", causal)
    print(f"{DH:<52} causal_data {len(causal):>8,} rows for {len(top)} products")

    # --- the single demo store --------------------------------------------------
    add("stores", read(DH, "stores"))

    # --- write -------------------------------------------------------------------
    args.out.mkdir(parents=True, exist_ok=True)
    total = 0
    print("\nwritten:")
    for table, frames in sorted(out.items()):
        frame = pd.concat(frames, ignore_index=True).drop_duplicates(subset="id")
        target = args.out / f"{table}.csv"
        frame.to_csv(target, index=False)
        size = target.stat().st_size
        total += size
        print(f"  {table:<22} {len(frame):>9,} rows  {size / 1e6:8.1f} MB")
    print(f"  {'TOTAL':<22} {'':>9}       {total / 1e6:8.1f} MB  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
