# Behavior + Online Retail II Transform — Member B (Segments 1, 2, 9)

**Status:** Real Kaggle data run complete — **VALIDATION PASS (42 checks, 0 failures)** 2026-10-21.
**Last Updated:** 2026-10-21

Turns two Kaggle datasets into canonical-schema CSVs that can be bulk-loaded into
`backend/schema.sql` — shaped data only, no feature computation:

| Source | Files | Feeds | Canonical target(s) |
|---|---|---|---|
| eCommerce behavior data from multi category store | `2019-Oct.csv[.gz]`, `2019-Nov.csv[.gz]` | Predicted Purchase Intent (1), Cart Abandoners (9) | `events` (+ `customers`, `products`, see §3) |
| Online Retail II | `online_retail_II.xlsx` (UCI) / `online_retail_II.csv` (Kaggle) | Future High-Value / CLV (2) | `customers`, `products`, `orders`, `order_items` |

**Not a source for this work:** `docs/decisions/data-transformation-plan.md` (excluded
by its own header). Nothing here is derived from it.

---

## 1. How to run

```bash
python data_prep/transform/run_member_b.py \
    --behavior-raw data_prep/raw/behavior \
    --retail-raw  data_prep/raw/retail \
    --out         data_prep/processed/member_b
```

Options: `--store-id <uuid>` stamps one tenant on every row (default: a
deterministic placeholder, §4); `--chunk-size` bounds the behaviour streaming chunk;
`--skip-validate` forces output even when checks fail.

Real sources (gitignored, re-downloadable — fixture CSVs were removed from the repo;
sources are fetched directly from Kaggle/Rees46/UCI):

- behaviour → `https://data.rees46.com/datasets/marketplace/2019-Oct.csv.gz` and
  `2019-Nov.csv.gz` (or Kaggle `mkechinov/ecommerce-behavior-data-from-multi-category-store`)
- retail → `https://archive.ics.uci.edu/dataset/502/online+retail+ii` (xlsx) or
  Kaggle `mashlyn/online-retail-ii-uci` (csv)

Last validation (real data): **VALIDATION PASS — 42 checks, 0 failures.**
(10 fewer than the fixture run's 52 because `events.csv` is 29.7 GB — the 10
in-memory `events` checks are skipped when readback is off; events integrity
holds by construction: `customers`/`products` are built from the same rows.)

### 1.1 Output

Six CSVs + `audit.json` in `data_prep/processed/member_b/`:

| File | Source | Rows (real Kaggle run, 2026-10-21) |
|---|---|---:|
| `stores.csv` | platform tenant row | 1 |
| `customers.csv` | behaviour `user_id` roster + retail `Customer ID` | 5,322,591 |
| `products.csv` | behaviour event catalog + retail `StockCode` catalog | 211,522 |
| `orders.csv` | retail (one row per `Invoice`) | 44,876 |
| `order_items.csv` | retail (one row per line) | 824,364 |
| `events.csv` | behaviour (one row per event) | 109,950,731 |

Real run totals: behaviour offset +1553 days, retail offset +5144 days (both onto
2024-01-01); 243,019 source rows dropped with recorded reasons (243,007 retail lines
with empty `Customer ID` — `customer_id` is NOT NULL — and 12 events with an empty
`user_session`); events NOT NULL violations: none.

`events.csv` is **streamed to disk** (the real files are hundreds of millions of
rows); it is read back for validation only when it is small (smoke runs). The other
five tables are held in memory.

Load order (FK-safe): `stores -> customers -> products -> orders -> order_items -> events`.

```bash
# from repo root, against a database already holding backend/schema.sql
for t in stores customers products orders order_items events; do
  psql "$DATABASE_URL" -c "\copy ${t} FROM 'data_prep/processed/member_b/${t}.csv' WITH (FORMAT csv, HEADER true, NULL '')"
done
```

`events.csv` is large; `\copy` streams it, so no client-side buffering is needed.

---

## 2. Per-file column mapping

Citations: `segments-schema-mapping.md` (Segments 1/2/9), the segment files, and
`final-schema.md` (target shape).

### behavior `2019-Oct/Nov.csv` → `events`

| Source | Target | Note / citation |
|---|---|---|
| `event_time` | `events.event_timestamp` | ESSENTIAL (mapping L55); parsed as UTC and shifted to the shared timeline (§5) |
| `event_type` | `events.event_type` | view / cart / purchase / remove_from_cart (mapping L47) |
| `product_id` | `events.product_id` | STRONG (mapping L48); namespaced; FK → `products` |
| `category_id` | `events.category_id` | MODERATE (mapping L49) |
| `category_code` | `events.category_code` | MODERATE (mapping L50) |
| `brand` | `events.brand` | MODERATE (mapping L51) |
| `price` | `events.price` | STRONG (mapping L52) |
| `user_id` | `events.customer_id` | ESSENTIAL (mapping L53); namespaced; FK → `customers` |
| `user_session` | `events.session_id` | ESSENTIAL (mapping L54); namespaced |

All 9 documented attributes are mapped. `event_type` is **not** filtered: `view`,
`cart`, `purchase` and `remove_from_cart` are all kept, because Segment 9 (Cart
Abandoners) needs `remove_from_cart` and Segment 1 needs the full funnel.

### Online Retail II → `orders` + `order_items`

| Source | Target | Note / citation |
|---|---|---|
| `Invoice` / `InvoiceNo` | `orders.source_order_id` | ESSENTIAL (mapping L100) |
| `InvoiceDate` | `orders.order_date` + `orders.order_timestamp` | ESSENTIAL (mapping L101); real time-of-day kept (Open Decision #5) |
| `Customer ID` / `CustomerID` | `customers.source_customer_id` → `orders.customer_id` | ESSENTIAL (mapping L99) |
| `StockCode` | `products.source_product_id` → `order_items.product_id` | ESSENTIAL (mapping L102) |
| `Description` | `products.name` | ESSENTIAL (mapping L103); falls back to the source id when blank |
| `Quantity` | `order_items.quantity` | ESSENTIAL (mapping L104); cancellations (negative) kept |
| `Price` / `UnitPrice` | `order_items.unit_price` | ESSENTIAL (mapping L105) |
| — | `order_items.sales_value` | `quantity * unit_price` (mapping L113/L124: no discount columns in this source) |
| — | `order_items.retail_disc` / `coupon_disc` / `coupon_match_disc` / `other_discount` | `0.0` — no source column |
| `Country` | — | **NOT mapped** — Segment 2 excludes it; no geo source resolved (Open Decision #4) |

Two source spellings are normalised: UCI's `Invoice`/`Price`/`Customer ID` and
Kaggle's `InvoiceNo`/`UnitPrice`/`CustomerID`. The real header wins.

---

## 3. Why `events` also emits `customers` and `products`

The brief says the behaviour dataset "maps to the `events` table", and that is the
segment mapping. But `events.customer_id` is a **NOT NULL FK** to `customers`, and
`events.product_id` is an FK to `products`. Emitting `events` alone cannot be
loaded — the rows would violate the FKs. So this transform emits the two dimension
tables the events depend on, built **from the event rows themselves**:

- `customers` — one row per distinct `user_id` (roster only; every other column NULL).
- `products` — one row per distinct `product_id`, with `brand`/`category` taken from
  the event stream and `name` derived from `category_code` (no name column exists in
  this source).

This mirrors what Member A does for Dunnhumby households and Member C does for the
churn/messaging rosters, and it keeps the behaviour data loadable and faithful. It is
recorded here rather than left implicit; if the team prefers dangling references or a
separate behaviour tenant, only these two outputs change.

---

## 4. ID remapping / unified namespace

Implemented in `common.py`, the single source of truth.

- **Namespace prefixes** (added by this member): `behavior → evb`, `online_retail_ii → ret`.
  They sit alongside Member A's `dnb`. Because both Member B datasets are loaded into
  one demo store, the prefixes keep `evb:5001` and `ret:13085` distinct inside
  `UNIQUE (store_id, source_customer_id)` and `UNIQUE (store_id, source_product_id)`.
  Provenance is preserved: `source_*` still names the exact raw value (blueprint
  principle 4).
- **UUIDs** are `uuid5(fixed_namespace, table + "|" + namespaced_key)` — deterministic,
  so re-running produces byte-identical ids and reloads are idempotent. Keys derive
  from *(table, source key)* only, not from `store_id`.
- Behaviour event keys are `"{file}|{row_index}"`; retail order-item keys are
  `(invoice, product, occurrence)`; retail orders key on the `Invoice`; retail products
  on `StockCode`.
- `session_id` is namespaced too (`evb:sess-a`), for consistency — it carries no
  uniqueness constraint, but a second event dataset with its own sessions cannot
  collide.

## 5. Date alignment (real-date sources)

Both Member B datasets ship **real** timestamps, so they use the path Member A
provided for exactly this case — `common.window_offset()` / `common.shift_date()` —
and **not** a synthetic anchor. `common.DATASET_ANCHORS` deliberately has no entry
for either (a day-number source must never borrow a real-date source's timeline, and
vice versa).

```text
shared window start = common.SHARED_WINDOW_START = 2024-01-01
offset = (window_start - min(source dates)).days   # computed once, applied uniformly
behaviour: min 2019-10-01 -> +1553 days
retail:    min 2009-12-01 -> +5144 days
```

Both datasets' earliest rows therefore land on the same day, with every interval
preserved, and the two overlap on one calendar. Member C uses the same reference date.
`shift_date()` keeps the time-of-day for `orders.order_timestamp` (it no longer
truncates a datetime to its date — that was a silent data-loss bug).

`orders.week_no` / `day_no` / `source_store_id` are left NULL: those are native
Dunnhumby columns, and no retail/behaviour column maps to them.

---

## 6. Derived values (flagged, not silent)

| Column | Why | Status |
|---|---|---|
| `products.name` (behaviour) | `NOT NULL` `[S]`, but no name column; derived from `category_code` (else the source id). | derived — team to confirm |
| `products.name` (retail) | `Description` is the name; blank cells fall back to the source id. | sourced with a documented fallback |
| `order_items.sales_value` (retail) | `NOT NULL`; this source has no discount columns, so net = `quantity * unit_price` (mapping L113/L124). | derived per the mapping's pricing note |
| `products.unit_price` | no catalog-level price in either source (Open Decision #11). | NULL |
| `customers.*` (all except keys) | fed only by the churn dataset, which is held out (`docs/submission/2.7-constraints.md`). | NULL by design |
| `customers.first_purchase_date` | left NULL — deriving it would be aggregation (`MIN(order_date)`), which the brief rules out (DUNNHUMBY.md §7.1(d)). | NULL |

---

## 7. Files/columns with no canonical target (gaps, not invented)

| Source | Why nothing is emitted |
|---|---|
| retail `Country` | Segment 2 explicitly excludes it ("no segment/KPI uses geo"); no geo source is resolved (Open Decision #4). |
| behaviour `category_id` on a view with no product | kept as NULL on the event — the event itself is real, the product is unknown. |
| `orders.region` | NULL (Open Decision #4) — *Revenue by Region* / *Orders by Region* cards will be empty for this tenant. |
| `products.unit_price` | NULL (Open Decision #11) — *Average Product Price* card empty. |

These are printed and recorded in `audit.json → notes`. Nothing is dropped quietly.

---

## 8. Cleaning rules (behaviour preserved)

Rows are removed only when they could not be loaded or would break a constraint.
Nothing is rescaled, rebalanced or aggregated:

**behaviour**

- empty `user_id` (`customer_id` NOT NULL)
- empty `user_session` (`session_id` NOT NULL)
- empty `event_type` (`event_type` NOT NULL)
- unparseable `event_time` (`event_timestamp` NOT NULL)
- an empty `product_id` is **kept** as a NULL reference — the event is real, the
  product is simply unknown.

**retail**

- empty `Customer ID`, `Invoice` or `StockCode` (NOT NULL / FK)
- unparseable `InvoiceDate` (order_date NOT NULL)
- unparseable `Quantity` or `Price` (NOT NULL)
- **cancellations and negative `Quantity`/`Price` are kept** — they are real behaviour.
- duplicate `(Invoice, StockCode, occurrence)` line items are kept as separate rows
  (the schema has no unique constraint on `order_items`); duplicate `Invoice` headers
  collapse to one order.

Every drop is counted per reason and tagged with its source file in
`audit.json → dropped_by_source`.

---

## 9. Validation

`validate.py` (shared) re-checks the constraints `backend/schema.sql` declares for the
table set this member emits:

- column list + order matches every emitted table
- NOT NULL on all required columns
- `UNIQUE (store_id, source_customer_id)`, `UNIQUE (store_id, source_product_id)`,
  `UNIQUE (store_id, source_order_id)`
- foreign keys (including the three `events` FKs)
- calendar check is skipped for real-date sources (with a note), as designed
- row accounting (raw → emitted + recorded drops)

The shared `validate.py` was extended for this member in two backward-compatible ways:
`NOT_NULL`/`UNIQUE`/FK entries for `events`, and a `columns` argument so a transform
validates only the tables it actually emits. Member A's default path is unchanged.
The FK membership test now ignores NULL child keys (a nullable FK such as
`events.product_id` is a legal absent reference, not an orphan).

Not validated: value vocabularies (`event_type`, `stores.plan`, …) — `schema.sql`
declares no CHECK constraints for them because the sources conflict (Open Decision #6).

---

## 10. Open Decisions this transform touches

| # | Decision | What this transform does today |
|---|---|---|
| #4 | `orders.region` source | left NULL for both datasets (no resolved geo source) |
| #5 | `orders.order_timestamp` source | populated from real `InvoiceDate` time-of-day (retail); `event_time` is already a full timestamp (behaviour) |
| #6 | channel/plan value sets | `stores.plan = 'pro'` provisionally |
| #11 | `products.unit_price` source | left NULL (price is transaction/event-level here) |

Plus one scope question for the team (§3): the behaviour dataset must emit
`customers` and `products` for its `events` to be loadable.

---

## Changelog

| Date | Change |
|---|---|
| 2026-10-21 | **Real Kaggle data run complete — VALIDATION PASS (42 checks, 0 failures).** 109,950,731 events (offset +1553d), 5,322,591 customers, 211,522 products, 44,876 orders, 824,364 order_items (offset +5144d). 243,019 drops recorded with reasons (243,007 empty `Customer ID`, 12 empty `user_session`). `openpyxl` added to `transform/requirements.txt` (retail xlsx read failed without it). Fixture CSVs removed from the repo per team direction — sources fetched directly from Kaggle/Rees46/UCI. |
| 2026-10-20 | Initial draft: `common.py` (namespaces `evb`/`ret`, `SHARED_WINDOW_START`, `shift_date` datetime fix), `behavior.py` (streaming events + derived customers/products), `retail.py` (customers/products/orders/order_items), `run_member_b.py`, synthetic fixtures, `validate.py` extension. Fixtures validated green (52 checks). |
