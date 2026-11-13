# Complete Transformation Notes

This document consolidates every transformation decision, technique, mapping, and rule applied to the six Kaggle ecommerce datasets so they appear as one demo store in the platform's canonical multi-tenant PostgreSQL schema. Every statement below is taken directly from the project documentation and transformation code — no assumptions, no inventions.

## 1. Problem & Motivation

Six raw Kaggle ecommerce datasets from six different stores were transformed into canonical-schema CSVs for a single multi-tenant demo store ("Customerly Demo Store"). The raw data came from different stores with different schemas, ID spaces, date conventions, and currencies. The transformation preserves behavioral patterns without fabricating cross-store customer links. Raw source files are never modified. No ML feature computation or prediction happens in the transforms — features are computed later by the platform's scheduled jobs over the loaded tables.

## 2. Tech Stack & Techniques

| Area | Technology / Technique |
|------|------------------------|
| Language | Python 3.11 |
| Data processing | pandas (vectorized operations, chunked streaming for large files) |
| UUID generation | `uuid5` with fixed namespace, deterministic → byte-identical IDs on re-runs, idempotent reloads |
| ID namespacing | Dataset namespace prefixes (`dnb:`, `evb:`, `ret:`, `cpn:`, `chr:`, `msg:`) to prevent collisions in the unified namespace |
| Date alignment | Per-dataset strategy (no global anchor): synthetic anchor for day-number sources, uniform offset for real-date sources, synthesized dates for churn snapshot |
| Cleaning | Null handling, deduplication, type casting without fractional truncation, whitespace trimming, explicit boolean parsing (no truthiness) |
| Validation | `backend/schema.sql` as executable contract; NOT NULL, UNIQUE, foreign key, calendar invariant, and row accounting checks |
| Publication | Hidden staging directory → atomic directory swap → backup retention → manifest with SHA-256 checksums and KaggleHub version markers |
| Streaming | Chunked CSV read/write for `events` (109,950,731 rows) and `causal_data` (36,771,279 rows / 4.5 GB); partitioned Python key files for global uniqueness without a database |
| Sampling | Member B behavior data: 40% session-based sampling, seed 42, whole customer sessions preserved; raw files unchanged |
| Regression tests | `python -m unittest discover -s data_prep/transform -p "test_*.py"` |
| Orchestration | Three member notebooks + CLI entry points (`run_dunnhumby.py`, `run_member_b.py`); `member_c.py` invoked from notebook |

## 3. Canonical Target Schema

The transformation emits data shaped for the 23-table canonical schema defined in `docs/notes/schema-notes/final-schema.md`. Key design principles carried into the transforms:

- Multi-tenancy: `store_id` UUID on every tenant-owned row
- No redundant columns
- Computed columns are platform-owned (not uploaded by stores)
- Traceability: source columns and original IDs tracked via `source_*` columns
- One canonical schema: no per-store customization
- Financials at order item level: all monetary values and discounts live on `order_items`
- Field provenance markers: `[S]` uploaded/source data, `[P]` platform-generated, `[C]` computed/derived

Tables emitted by transforms include: `stores`, `customers`, `products`, `orders`, `order_items`, `events`, `causal_data`, `coupon_campaigns`, `coupon_redemptions`, `marketing_campaigns`, `message_sends`, `holidays`. Tables NOT emitted by transforms (platform-computed at runtime): `customer_features`, `segments`, `customer_segments`, `ml_models`, `kpi_definitions`, `kpi_snapshots`, `workflows`, `workflow_executions`, `ingestion_uploads`, `ingestion_column_mappings`.

## 4. Member A — Dunnhumby: The Complete Journey (Segments 6, 7, 8)

**Source dataset:** `dunnhumby-the-complete-journey` (8 CSV files)

**Entry point:** `data_prep/transform/run_dunnhumby.py` → `data_prep/transform/dunnhumby.py`

**Raw files processed:**
- `transaction_data.csv` → `orders` + `order_items`
- `hh_demographic.csv` → `customers` (roster only)
- `product.csv` → `products`
- `causal_data.csv` → `causal_data`
- `campaign_desc.csv` → `coupon_campaigns`
- `coupon_redempt.csv` → `coupon_redemptions`
- `coupon.csv` → NOT emitted (no canonical target)
- `campaign_table.csv` → NOT emitted (campaign exposure, not redemption)

### 4.1 Column Mappings

**`transaction_data.csv` → `orders`:**
| Source | Target | Notes |
|--------|--------|-------|
| `BASKET_ID` | `orders.source_order_id` | ESSENTIAL |
| `household_key` | `orders.customer_id` → `customers.id` | FK resolved via customer namespace |
| `DAY` | `orders.day_no` | Native 1–711, kept verbatim for interval math |
| `DAY` | `orders.order_date` | `anchor_date + (day_no - 1)`, anchor `2016-01-01` |
| `WEEK_NO` | `orders.week_no` | Native 1–104 |
| `STORE_ID` | `orders.source_store_id` | Renamed from documented `store_id` to avoid tenant FK collision (Open Decision #3) |
| `TRANS_TIME` | `orders.order_timestamp` | Real time-of-day; format verified as 4-digit HHMM |
| — | `orders.region` | NULL — no geo column in this dataset (Open Decision #4) |

**`transaction_data.csv` → `order_items`:**
| Source | Target | Notes |
|--------|--------|-------|
| `QUANTITY` | `order_items.quantity` | ESSENTIAL |
| `SALES_VALUE` | `order_items.sales_value` | Net amount as-is; RETAIL_DISC/COUPON_MATCH_DISC not subtracted again |
| `RETAIL_DISC` | `order_items.retail_disc` | Carried even though Segments 6/7/8 list it as not needed |
| `COUPON_DISC` | `order_items.coupon_disc` | Same |
| `COUPON_MATCH_DISC` | `order_items.coupon_match_disc` | Same |
| — | `order_items.other_discount` | 0.0 — no source column |
| — | `order_items.unit_price` | Derived as `sales_value / quantity` (no unit-price column in source) |
| — | `order_items.source_timestamp` | Derived from DAY + TRANS_TIME |

**`product.csv` → `products`:**
| Source | Target | Notes |
|--------|--------|-------|
| `PRODUCT_ID` | `products.source_product_id` | ESSENTIAL |
| `DEPARTMENT` | `products.department` | |
| `BRAND` | `products.brand` | |
| `COMMODITY_DESC` | `products.commodity_desc` | |
| `COMMODITY_DESC` (fallback `SUB_COMMODITY_DESC`, then `PRODUCT_ID`) | `products.name` | Derived — no name column in source |
| `MANUFACTURER`, `SUB_COMMODITY_DESC`, `CURR_SIZE_OF_PRODUCT` | — | Documented "not needed" |
| — | `products.category` | NULL — no documented Dunnhumby source column |
| — | `products.unit_price` | NULL — no catalog price column (Open Decision #11) |

**`hh_demographic.csv` → `customers`:**
- Used only to complete the customer roster (households that appear but never transact are kept)
- All 7 demographic attributes (`AGE_DESC`, `MARITAL_STATUS_CODE`, `INCOME_DESC`, `HOMEOWNER_DESC`, `HH_COMP_DESC`, `HOUSEHOLD_SIZE_DESC`, `KID_CATEGORY_DESC`) have no canonical column — excluded per user request

**`causal_data.csv` → `causal_data`:**
| Source | Target |
|--------|--------|
| `PRODUCT_ID` | `causal_data.product_id` |
| `STORE_ID` | `causal_data.source_store_id` (NOT NULL) |
| `WEEK_NO` | `causal_data.week_no` |
| `display` / `mailer` | `causal_data.display` / `mailer` (VARCHAR(50), raw codes, not 0/1 flags) |

**`campaign_desc.csv` → `coupon_campaigns`:**
| Source | Target |
|--------|--------|
| `CAMPAIGN` | `source_campaign_id` |
| `DESCRIPTION` | `campaign_type` |
| `START_DAY` / `END_DAY` | `start_date` / `end_date` via same anchor as orders |

**`coupon_redempt.csv` → `coupon_redemptions`:**
| Source | Target |
|--------|--------|
| `household_key` | `customer_id` |
| `COUPON_UPC` | `coupon_id` |
| `CAMPAIGN` | `campaign_id` |
| `redemption_status` | `TRUE` (every row in this file is by definition a redemption) |
| `DAY` | Dropped — `coupon_redemptions` has no redemption timestamp column |

### 4.2 ID Remapping
- Namespace prefix `dnb:` on every source ID
- UUIDs: `uuid5(fixed_namespace, table + "|" + namespaced_key)` — deterministic, idempotent
- `order_items` keys: `(BASKET_ID, PRODUCT_ID, occurrence)`
- `causal_data` keys: `(STORE_ID, PRODUCT_ID, WEEK_NO)` (natural key)
- `coupon_redemptions` keys: `(household, coupon, campaign)`

### 4.3 Date Alignment
Dunnhumby has no real dates — only native day numbers (`DAY` 1–711). Dates invented from declared anchor `2016-01-01`:
- `order_date = anchor(2016-01-01) + (day_no - 1)`
- `order_timestamp = order_date + TRANS_TIME` (real time, not synthesised)
- `start_date` / `end_date` = anchor + (START_DAY/END_DAY - 1) for campaigns
- `day_no` / `week_no` = native values, kept verbatim

The anchor is per-dataset only. `anchor_for("dunnhumby")` returns it; `anchor_for(<unknown>)` raises rather than defaulting.

### 4.4 Derived Values (Flagged, Not Silent)
| Column | Why | Status |
|--------|-----|--------|
| `order_items.unit_price` | NOT NULL, no unit-price column | Derived `sales_value / quantity` (0.0 when quantity = 0) |
| `products.name` | NOT NULL, no name column | Derived from `COMMODITY_DESC` (else `SUB_COMMODITY_DESC`, else source id) |
| `products.unit_price`, `products.category`, `orders.region` | No documented source column | NULL |

### 4.5 Files/Columns With No Canonical Target
| Source | Reason |
|--------|--------|
| `coupon.csv` | Canonical schema has no coupon→product table; no column mapping exists |
| `campaign_table.csv` | Campaign exposure, not redemption; no canonical table holds "customer was targeted" |
| `hh_demographic.csv` 7 demographic columns | Excluded per user request in all three segment files |
| `events`, `marketing_campaigns`, `message_sends`, `holidays` | No source columns in this dataset |

### 4.6 Cleaning Rules
- Empty/unparseable `BASKET_ID`, `PRODUCT_ID`, `household_key`, `DAY` dropped
- Line items whose product is absent from `product.csv` dropped (FK ON DELETE RESTRICT)
- Duplicate `PRODUCT_ID` rows: first kept, validator reports conflicting dimension rows
- Duplicate `(store, product, week)` causal rows dropped (schema UNIQUE constraint)
- Duplicate redemptions dropped
- Campaigns missing usable `START_DAY`/`END_DAY` dropped (both NOT NULL)
- Redemptions referencing missing campaign or household dropped
- Negative quantities / `SALES_VALUE` (returns) kept — real behavior
- `RETAIL_DISC` stored verbatim (negative = reduces value), not sign-flipped

### 4.7 Real-Data Findings
- `TRANS_TIME` is zero-padded `HHMM` (e.g. `0955`), not `HHMMSS`; pandas was eating the leading zero when read as int64 — fixed by pinning `dtype=str` and teaching `parse_trans_time` the 3-digit form
- `causal_data.display` / `mailer` are promotion codes, not 0/1 flags: `display` ∈ `0`–`9`,`A`; `mailer` ∈ `0`,`A`,`C`,`D`,`F`,`H`,`J`,`L`,`P`,`X`,`Z`
- `causal_data` UNIQUE key collisions: 15,245 source rows collide on `(store_id, source_store_id, product_id, week_no)` with different `display`/`mailer` values
- `causal_data.csv` volume: 4.5 GB / 36.8M rows
- 25,188 baskets in hours 00–09 initially lost their timestamp due to the TRANS_TIME parsing bug — fixed and re-verified: 0 NULL timestamps, all 24 hours present

### 4.8 Validation Results
Real-data run: **VALIDATION PASS — 71 checks, 0 failures**. Counts emitted:
- `order_items`: 2,595,732 (0 lost from 2,595,732 transaction lines)
- `orders`: 276,484 (from 2,595,732 lines → 276,484 baskets)
- `customers`: 2,500 (801 from hh_demographic + transacting households)
- `products`: 92,353 (from 92,353 product rows)
- `causal_data`: 36,771,279 (from 36,786,524 causal rows)
- `coupon_campaigns`: 30 (from 30 campaign_desc rows)
- `coupon_redemptions`: 2,288 (from 2,318 redemption rows)

Output layout: `data_prep/processed/dunnhumby-the-complete-journey/<table>/<table>.csv` + `manifest.json` + `source_preservation/` + `audit.json`.

## 5. Member B — eCommerce Behavior Data + Online Retail II (Segments 1, 2, 9)

**Source datasets:**
- `ecommerce-behavior-data-from-multi-category-store` (2019-Oct.csv, 2019-Nov.csv, possibly .gz)
- `online-retail-ii-uci` (online_retail_II.csv or online_retail_II.xlsx)

**Entry point:** `data_prep/transform/run_member_b.py` → `data_prep/transform/behavior.py` + `data_prep/transform/retail.py`

### 5.1 Behavior Data → `events` (plus derived `customers` and `products`)

All 9 documented attributes mapped:
| Source | Target | Notes |
|--------|--------|-------|
| `event_time` | `events.event_timestamp` | Parsed as UTC, shifted to shared timeline |
| `event_type` | `events.event_type` | view / cart / purchase / remove_from_cart (all kept, not filtered) |
| `product_id` | `events.product_id` | Namespaced; FK → products |
| `category_id` | `events.category_id` | |
| `category_code` | `events.category_code` | |
| `brand` | `events.brand` | |
| `price` | `events.price` | |
| `user_id` | `events.customer_id` | Namespaced; FK → customers |
| `user_session` | `events.session_id` | Namespaced |

**Why `customers` and `products` are also emitted:** `events.customer_id` is a NOT NULL FK to `customers`, and `events.product_id` is an FK to `products`. The events table cannot be loaded alone. One row per distinct `user_id` (roster only, all other columns NULL) and one row per distinct `product_id` (brand/category from event stream, name derived from `category_code`).

**Streaming:** Raw files (~115M rows) read in chunks (1,000,000 rows default), mapped events appended to CSV as they go. Dimension sets held in memory.

**Sampling:** 40% session-based sampling with seed 42. Whole customer sessions selected consistently across chunks and months via hash of `customer_id + session_id`. Raw files unchanged; no intermediate sample files written. `raw_counts` records all scanned rows; `sampled_counts` records selected input rows before cleaning.

**Date alignment:** Real source timestamps retained; no alignment scan needed (offset 0 applied). Event timestamps are TIMESTAMPTZ with UTC.

**Cleaning drops:** empty `user_id`, empty `user_session`, empty `event_type`, unparseable `event_time`. Empty `product_id` kept as NULL reference (event is real, product unknown).

### 5.2 Online Retail II → `customers`, `products`, `orders`, `order_items`

Two source spellings normalised: UCI (`Invoice` / `Price` / `Customer ID`) and Kaggle (`InvoiceNo` / `UnitPrice` / `CustomerID`).

| Source | Target | Notes |
|--------|--------|-------|
| `Invoice` / `InvoiceNo` | `orders.source_order_id` | ESSENTIAL |
| `InvoiceDate` | `orders.order_date` + `orders.order_timestamp` | Real time-of-day kept |
| `Customer ID` / `CustomerID` | `customers.source_customer_id` → `orders.customer_id` | ESSENTIAL |
| `StockCode` | `products.source_product_id` → `order_items.product_id` | ESSENTIAL |
| `Description` | `products.name` | Falls back to source id when blank |
| `Quantity` | `order_items.quantity` | Cancellations (negative) kept |
| `Price` / `UnitPrice` | `order_items.unit_price` | ESSENTIAL |
| — | `order_items.sales_value` | `quantity * unit_price` (no discount columns in this source) |
| — | `order_items.retail_disc` / `coupon_disc` / `coupon_match_disc` / `other_discount` | 0.0 — no source column |
| `Country` | — | NOT mapped — Segment 2 excludes it; no geo source resolved (Open Decision #4) |

**Date alignment:** Real `InvoiceDate` retained with offset 0 onto shared window. Timezone unknown (local wall time).

**Order construction:** One row per `Invoice`. Duplicate `Invoice` headers collapse to one order. Conflicting invoice `_customer`/`_date`/`_region` raise an error (no arbitrary header selection). Ambiguous timestamps (multiple line times per invoice) → `order_timestamp` NULL.

**Order items:** One row per line. `sales_value = quantity * unit_price` (net, no discounts). Cancellations and negative Quantity/Price preserved as-is.

**Customers:** One row per distinct `Customer ID`. `first_purchase_date` left NULL (derivation would be aggregation, ruled out). All other customer columns NULL by design.

**Products:** One row per `StockCode`. `brand`/`category` NULL (no such columns). `unit_price` NULL (Open Decision #11).

**Cleaning drops:** empty `Customer ID`, `Invoice`, or `StockCode`; unparseable `InvoiceDate`, `Quantity`, or `Price`. Duplicate `(Invoice, StockCode, occurrence)` line items kept as separate rows (no unique constraint on `order_items`).

### 5.3 Validation Results
Real Kaggle run (2026-10-21): **VALIDATION PASS — 42 checks, 0 failures**.
Counts:
- `events`: 109,950,731 (behavior offset +1553 days)
- `customers`: 5,322,591
- `products`: 211,522
- `orders`: 44,876 (retail offset +5144 days)
- `order_items`: 824,364
- Drops: 243,019 source rows (243,007 empty `Customer ID`, 12 empty `user_session`)

## 6. Member C — Coupon Redemption + Customer Churn + Direct Messaging (Segments 3, 4, 5)

**Source datasets:**
- `predicting-coupon-redemption` (train.csv, campaign_data.csv, coupon_item_mapping.csv, customer_demographics.csv, customer_transaction_data.csv, item_data.csv, test.csv)
- `e-commerce-customer-churn` (data_ecommerce_customer_churn.csv)
- `direct-messaging` (campaigns.csv, messages-demo.csv, client_first_purchase_date.csv, holidays.csv)

**Entry point:** `data_prep/transform/member_c.py` (orchestrated from notebook)

### 6.1 Coupon Redemption → `customers`, `products`, `orders`, `order_items`, `coupon_campaigns`, `coupon_redemptions`

**ID remapping:** Namespace prefix `cpn:` on all source IDs.

**`coupon_campaigns`:**
| Source | Target | Transform |
|--------|--------|-----------|
| `campaign_id` | `source_campaign_id` | Prefix `cpn:` |
| `campaign_type` | `campaign_type` | None |
| `start_date` / `end_date` | `start_date` / `end_date` | Shift by delta |

**`coupon_redemptions`:**
| Source | Target | Transform |
|--------|--------|-----------|
| `coupon_id` | `coupon_id` | Prefix `cpn:` |
| `campaign_id` | `campaign_id` | Prefix `cpn:` |
| `customer_id` | `customer_id` | Prefix `cpn:` |
| `redemption_status` | `redemption_status` | Boolean: 1→true, 0→false |

**`customers` (coupon subset):**
| Source | Target | Transform |
|--------|--------|-----------|
| `customer_id` | `source_customer_id` | Prefix `cpn:` |

**`products`:**
| Source | Target | Transform |
|--------|--------|-----------|
| `item_id` | `source_product_id` | Prefix `cpn:` |
| `brand` | `brand` | None |
| `category` | `category` | None |
| `brand_type` | — | Not needed |

**`orders` (one per transaction row):**
| Source | Target | Transform |
|--------|--------|-----------|
| Row index | `source_order_id` | `cpn:customer_transaction_data.csv|{row_index}` |
| `date` | `order_date` | Shift by delta |
| `customer_id` | `customer_id` | Prefix `cpn:` |

**`order_items`:**
| Source | Target | Transform |
|--------|--------|-----------|
| `quantity` | `quantity` | None |
| `selling_price` | `sales_value` | Treated as NET (confirmed from source analysis) |
| `other_discount` | `other_discount` | None |
| `coupon_discount` | `coupon_disc` | None |
| `unit_price` | `unit_price` | Derived as selling_price / quantity when possible |

**Not used:** `customer_demographics.csv` (all demographics excluded), `coupon_item_mapping.csv` (no target table), `test.csv` (no redemption_status)

**Date alignment:** For each dataset, compute `delta = reference_date(2024-01-01) - max_date_in_dataset` once, shift all date columns by the same delta. This preserves intervals between events exactly.

### 6.2 Customer Churn → `customers` (churn subset)

| Source | Target | Transform |
|--------|--------|-----------|
| Row index | `source_customer_id` | `chr:data_ecommerce_customer_churn.csv|{row_index}` |
| `Tenure` | `tenure` | None |
| `WarehouseToHome` | `warehouse_to_home` | None |
| `NumberOfDeviceRegistered` | `number_of_devices_registered` | None |
| `PreferedOrderCat` | `preferred_order_category` | None |
| `SatisfactionScore` | `satisfaction_score` | None |
| `Complain` | `complain` | Boolean: 1→true, 0→false |
| `DaySinceLastOrder` | `days_since_last_order` | None |
| `CashbackAmount` | `cashback_amount` | None |
| `Churn` | `churn` | Boolean: 1→true, 0→false |

**Synthesized dates:** `order_date = reference_date - DaySinceLastOrder`; `first_purchase_date = reference_date - Tenure`. No verified observation date or tenure unit; cannot acquire artificial purchase history.

**Not used:** `MaritalStatus`, `NumberOfAddress` (excluded per segments-schema-mapping.md)

### 6.3 Direct Messaging → `customers`, `marketing_campaigns`, `message_sends`, `holidays`

**ID remapping:** Namespace prefix `msg:` on client_id, campaign_id, message_id. Campaign IDs not globally unique in source — seven IDs occur as both bulk and transactional campaigns. Source campaign key is `(campaign_type, id)`; message reference is `(message_type, campaign_id)`.

**`marketing_campaigns`:**
| Source | Target | Transform |
|--------|--------|-----------|
| `id` | `source_campaign_id` | Prefix `msg:` via `(campaign_type, id)` key |
| `campaign_type` | `campaign_type` | None |
| `channel` | `channel` | None |
| `topic` | `topic` | None |
| `started_at` / `finished_at` | `started_at` / `finished_at` | Shift by delta |
| `total_count` | `total_count` | None |

**Not used:** `ab_test`, `warmup_mode`, `hour_limit`, `subject_length`, `subject_with_*`, `is_test`, `position` (A/B config not needed)

**`message_sends` (streamed, 250,000-row chunks):**
| Source | Target | Transform |
|--------|--------|-----------|
| `message_id` | `source_message_id` | Prefix `msg:` |
| `campaign_id` | `campaign_id` | Prefix `msg:` via `(message_type, campaign_id)` key |
| `client_id` | `customer_id` | Prefix `msg:` |
| `message_type` | `message_type` | None |
| `channel` | `channel` | None |
| `channel_norm` | `channel_norm` | email→email, sms→sms, web_push→push, mobile_push→push, whatsapp→whatsapp |
| `sent_at` | `sent_at` | Shift by delta |
| `purchased_at` | `purchased_at` | Shift by delta |
| `is_opened`, `is_clicked`, `is_unsubscribed`, `is_hard_bounced`, `is_soft_bounced`, `is_complained`, `is_blocked`, `is_purchased` | Same names | Boolean: explicit parsing, no truthiness |

**Not used:** `category`, `platform`, `email_provider`, `stream` (technical metadata)

**`customers` (messaging subset):**
| Source | Target | Transform |
|--------|--------|-----------|
| `client_id` | `source_customer_id` | Prefix `msg:` |
| `first_purchase_date` | `first_purchase_date` | Shift by delta |

**`holidays`:**
| Source | Target | Transform |
|--------|--------|-----------|
| `date` | `date` | Shift by delta |
| `holiday` | `holiday` | None |

**Customer merge strategy:** All three datasets contribute rows to unified `customers.csv`:
1. Coupon customers: `cpn_{customer_id}` — no extra attributes beyond `source_customer_id`
2. Churn customers: `chr_{row_index}` — full churn attributes
3. Messaging customers: `msg_{client_id}` — `first_purchase_date` only
No overlap between prefixes, so no dedup needed. Each row has only columns relevant to its source; other columns NULL.

**Cleaning rules:**
- Drop rows where primary key columns are null; keep other nulls as NULL
- Exact duplicate rows removed within each source file
- Type casting: IDs→VARCHAR, booleans→BOOLEAN (0/1 → false/true, "0"/"1" → false/true), dates→DATE, timestamps→TIMESTAMPTZ, quantities→INTEGER, prices→NUMERIC
- Whitespace trim on all string columns

**Output:** `coupon_campaigns.csv`, `coupon_redemptions.csv`, `customers.csv` (merged), `products.csv`, `orders.csv`, `order_items.csv`, `marketing_campaigns.csv`, `message_sends.csv`, `holidays.csv`.

## 7. ID Remapping & Unified Namespace (Cross-Cutting)

Implemented in `common.py` — single source of truth for all members.

**Namespace constants:**
```python
SOURCE_NAMESPACES = {
    "dunnhumby": "dnb",
    "behavior": "evb",
    "online_retail_ii": "ret",
    "coupon": "cpn",
    "churn": "chr",
    "messaging": "msg",
}
```

**`source_id(raw, dataset)`:** Normalises raw source IDs. Handles pandas upcasting integers to float (1234.0 → "1234"), strips whitespace, rejects NaN/None/blank. Returns empty string for unusable values so callers can drop the row. Prefixes with namespace: `f"{ns}:{text}"`.

**`uid(table, key)`:** Deterministic UUID: `uuid5(UUID_NAMESPACE, f"{TENANT.get()}|{table}|{key}")`. Fixed namespace derived from `https://customerly.local/data_prep/transform`. Re-running produces byte-identical UUIDs → idempotent reloads. Keys derived from `(table, source key)` only, not from `store_id`, so changing tenant UUID does not invalidate child IDs.

**`DEMO_STORE_ID`:** `uuid5(UUID_NAMESPACE, 'stores|customerly-demo')`. All tenant-owned rows stamped with this store_id.

**`set_tenant(store_id)`:** Sets tenant scope via `ContextVar` before mapping primary and foreign keys.

## 8. Date Alignment Strategy (Cross-Cutting)

Per-dataset strategy — never global. The module-level `ANCHOR_DATE` constant was removed to prevent cross-source contamination (a real-date source could silently be shifted by Dunnhumby's anchor).

**`anchor_for(dataset)`:** Returns declared anchor for day-number sources. Raises `KeyError` for unregistered datasets — guessing an anchor would invent dates for data that already has them.

**Registered anchors:**
- `dunnhumby`: `date(2016, 1, 1)`

**`align_day(day_no, anchor)`:** Maps native day number onto dataset's store timeline. `day_no` preserved verbatim in `orders.day_no` for interval math; only human-readable `order_date` is offset. Returns None when day number missing/unusable.

**`window_offset(real_dates, window_start)`:** Days to add to a source's real dates so its earliest date hits `window_start` (`2024-01-01`). Computed once from source's own minimum, applied uniformly — never per row.

**`shift_date(value, offset_days)`:** Applies window offset to a single date/timestamp. Datetime keeps time-of-day; plain date returns date.

**`combine_timestamp(day, trans_time)`:** Combines aligned date with source time-of-day into one timestamp. Dunnhumby `TRANS_TIME` is zero-padded `HHMM` (e.g. `1631` = 16:31). Source local wall time; no timezone identified.

**Per-dataset application:**
- Dunnhumby: synthetic anchor `2016-01-01 + (day_no - 1)`; real time-of-day from `TRANS_TIME`
- Behavior: real `event_time` (UTC), offset +1553 days to shared window
- Retail: real `InvoiceDate`, offset +5144 days to shared window
- Coupon: `delta = 2024-01-01 - max_date`, shift all date columns uniformly
- Messaging: same delta approach as Coupon
- Churn: no real dates; `order_date = 2024-01-01 - DaySinceLastOrder`; `first_purchase_date = 2024-01-01 - Tenure`

## 9. Cleaning Rules (Cross-Cutting)

1. **Null handling:** Drop rows where primary key columns are null; keep other nulls as NULL
2. **Dedup:** Exact duplicate rows removed within each source file
3. **Type casting:**
   - IDs → VARCHAR
   - Booleans → BOOLEAN (0/1 → false/true, "0"/"1" → false/true; explicit parsing, no truthiness)
   - Dates → DATE
   - Timestamps → TIMESTAMPTZ
   - Quantities → INTEGER (fractional quantities fail validation rather than truncate)
   - Prices/amounts → NUMERIC (unconstrained precision; no forced two-decimal rounding)
4. **Whitespace:** Trim leading/trailing whitespace from all string columns using `df.apply(lambda x: x.str.strip() if x.dtype == 'object' else x)`

**Preservation:** Nothing rescaled, rebalanced, or aggregated. Cancellations and negative quantities/prices kept as real behavior. `RETAIL_DISC` stored verbatim (negative reduces value), not sign-flipped. Every drop counted per reason, tagged with source file.

## 10. Derived Values (Flagged, Not Silent)

Per schema-blueprint principle 3 (computed columns are platform-owned), derived values are forced by NOT NULL constraints or absence of source column. Each is printed in run output:

| Column | Why | Derivation |
|--------|-----|------------|
| `order_items.unit_price` (Dunnhumby) | NOT NULL, no unit-price column | `sales_value / quantity` (0.0 when quantity = 0) |
| `products.name` (Dunnhumby, Behavior) | NOT NULL, no name column | From `COMMODITY_DESC` / `category_code` / source id fallback chain |
| `order_items.sales_value` (Retail) | NOT NULL, no discount columns | `quantity * unit_price` |
| `products.unit_price` | No catalog-level price in any source | NULL (Open Decision #11) |
| `orders.region` | No resolved geo source | NULL (Open Decision #4) |
| `customers.first_purchase_date` | Derivation would be aggregation | NULL by design |
| `customers.*` (all except keys, Dunnhumby/Behavior) | Fed only by churn dataset (held out) | NULL by design |

## 11. Files/Columns With No Canonical Target (Gaps, Not Inventions)

Documented absences — nothing dropped quietly:

| Dataset | Source File/Column | Reason |
|---------|-------------------|--------|
| Dunnhumby | `coupon.csv` | Canonical schema has no coupon→product table |
| Dunnhumby | `campaign_table.csv` | Campaign exposure, not redemption; no canonical table for "customer was targeted" |
| Dunnhumby | `hh_demographic.csv` demographics | Excluded per user request in Segments 6/7/8 |
| Dunnhumby | `events`, `marketing_campaigns`, `message_sends`, `holidays` | No source columns in dataset |
| Online Retail II | `Country` | Segment 2 excludes it; no geo source resolved (Open Decision #4) |
| Coupon | `customer_demographics.csv` | All demographics excluded per segments-schema-mapping.md |
| Coupon | `coupon_item_mapping.csv` | No target table in canonical schema |
| Coupon | `test.csv` | No redemption_status (inference set) |
| Messaging | `category`, `platform`, `email_provider`, `stream` | Technical metadata, not needed |
| Messaging | `ab_test`, `warmup_mode`, `hour_limit`, `subject_with_*`, `is_test`, `position` | A/B testing config, not needed |

## 12. Validation & Publication Contract

### 12.1 Validation (`validate.py` + `contract.py`)

`validate_frames()` checks against `backend/schema.sql`:
- Column list + order matches every emitted table
- NOT NULL on all required columns
- UNIQUE constraints: `(store_id, source_customer_id)`, `(store_id, source_product_id)`, `(store_id, source_order_id)`, `stores.id`
- Foreign keys: 12 FKs including `orders.customer_id`, `order_items.order_id`, `order_items.product_id`, both coupon FKs, all 3 `events` FKs
- Calendar invariants (day-number sources only): `order_date == anchor + (day_no - 1)`, `week_no` ∈ 1–104, `day_no` ∈ 1–711
- Source row accounting: raw lines → emitted + recorded drops

`validate_stream()` for large files (`events`, `causal_data`):
- Chunked validation (250,000 rows per chunk)
- Partitioned Python key files for global uniqueness without a database
- UUID keys use 16-byte compact keys; composite keys retain exact JSON values
- Source-line counts reconciled; source exclusions block publication

### 12.2 Publication (`contract.py`)

`publish()`:
1. Validates all frames against schema.sql
2. Stages to hidden directory beside output
3. On failure: retains staging/exception folders, leaves existing final datasets intact
4. On success: atomic directory swap; previous contents retained as hidden `.<dataset>-previous-<uuid>` backup
5. Writes `manifest.json` with: contract_version, store_id, sources (SHA-256), kaggle_version_markers, raw_counts, output_counts, row_reconciliation, id_rule, date_policy, currency_policy, dropped, notes, accepted_source_exclusions, audited_source_exclusions, validation status, `database_import_verified: false`

### 12.3 Output Layout
```
data_prep/processed/<dataset-slug>/
  <table>/<table>.csv
  manifest.json
  source_preservation/<subset>.csv
  exceptions/<table>.csv
```
Noncanonical source-preservation files (coupon-product links, Dunnhumby campaign exposure/redemption days, coupon transaction provenance) are explicitly NOT canonical database tables. Raw files remain immutable.

## 13. Open Decisions Touched by Transformation

| # | Decision | What the transform does today |
|---|----------|------------------------------|
| #3 | `store_id` name collision (tenant FK vs source `STORE_ID`) | `STORE_ID` → `orders.source_store_id` / `causal_data.source_store_id`; tenant stays `store_id UUID` |
| #4 | `orders.region` data source | Left NULL (no geo column in Dunnhumby/Behavior datasets) |
| #5 | `orders.order_timestamp` source | Populated from real timestamps where available (Dunnhumby DAY+TRANS_TIME, Retail InvoiceDate, Behavior event_time); NULL when missing |
| #6 | Channel/plan value sets | `stores.plan = 'pro'` provisionally (one of three values in final-schema.md table 1) |
| #10 | `selling_price` net vs gross | Unverified — coupon `selling_price` mapped to `sales_value` verbatim; no generic second discount subtraction |
| #11 | `products.unit_price` source | Left NULL — no catalog-level price column in any source |
| #12 | Duplicate `customer_features` columns | Three pairs retained (cart_removal/remove_from_cart, unique_products/product_loyalty) — both names referenced by KPI mapping |
| #13 | `customer_features` refresh mechanism | Not resolved by transforms; requirement confirmed (recompute on new platform-generated rows) but mechanism (trigger vs scheduled job) undetermined |
| #14 | `customer_features` grain vs Segment 6 | Three features at customer×product grain stored as per-customer values — aggregate undetermined |
| #15 | `tracked_links` table | Not added — click tracking for platform-sent messages out of scope |
| #16 | `customer_kpis` vs `kpi_snapshots` naming | Schema uses `kpi_snapshots`; Overview.md states `customer_kpis` — undetermined |

Real-data questions (recorded, not resolved):
- `causal_data.display`/`mailer` are promotion codes but Segment 8's `promo_response_rate` formula assumes flags (`display/mailer=1`) — team decision needed on predicate semantics
- 15,245 `causal_data` UNIQUE collisions discard a differing promo code per cell — options: widen key or accept loss
- 4.5 GB `causal_data` volume — confirm platform wants full table vs only order-touching weeks

## 14. Runbook (How to Run)

**Install dependencies:**
```
pip install -r data_prep/transform/requirements.txt
```
Includes `openpyxl` for Online Retail II Excel input.

**Member A (Dunnhumby):**
```bash
python data_prep/transform/run_dunnhumby.py \
    --raw data_prep/raw/dunnhumby-the-complete-journey \
    --out data_prep/processed/dunnhumby-the-complete-journey
```
Options: `--store-id <uuid>` (default deterministic placeholder), `--skip-validate`.

**Member B (Behavior + Retail):**
```bash
python data_prep/transform/run_member_b.py \
    --behavior-raw data_prep/raw/ecommerce-behavior-data-from-multi-category-store \
    --retail-raw data_prep/raw/online-retail-ii-uci \
    --out data_prep/processed
```
Options: `--chunk-size <int>` (default 250000), `--skip-validate`.

**Member C (Coupon + Churn + Messaging):**
Invoked from notebook via `member_c.py` functions: `transform_coupon()`, `transform_churn()`, `transform_messaging()`, `map_messages()`.

**Load order (FK-safe):**
```
stores -> customers -> products -> orders -> order_items -> events
       -> coupon_campaigns -> coupon_redemptions -> causal_data
```

**Regression checks:**
```bash
python -m unittest discover -s data_prep/transform -p "test_*.py"
```

**Database load (example, against database with backend/schema.sql applied):**
```bash
for t in stores customers products orders order_items events; do
  psql "$DATABASE_URL" -c "\copy ${t} FROM 'data_prep/processed/member_b/${t}.csv' WITH (FORMAT csv, HEADER true, NULL '')"
done
```

## 15. Transformation Contract Revision (2026-10-21)

The shared transformation contract v2 supersedes earlier store IDs, per-file date shifts, output paths, and deduplication descriptions. Key revisions:
- Real source dates retained; no 2024 alignment or per-file shift for real-date sources
- Dunnhumby day numbers use `2016-01-01 + DAY - 1` (synthetic display calendar, not evidence of actual 2016 activity)
- Source-local timestamps timezone-naive in `orders`, `order_items.source_timestamp`, marketing campaigns, messages; behavior events retain UTC
- `order_items.source_timestamp` retains individual line timestamps; basket/invoice header timestamp NULL if source lines disagree
- Churn is a snapshot with no verified observation date; `first_purchase_date` stays NULL
- `sales_value` is sole canonical column name; no generic discount subtraction
- Coupon `coupon_discount` maps to `coupon_disc` with original sign
- Source currencies/units not proven comparable; no conversion/rescaling during transformation
- Causal promotion rows use occurrence UUIDs; old unique product/source-store/week constraint removed; downstream uses EXISTS
- Retail Country maps to `orders.region`; missing geography stays NULL
- Messaging campaign identity uses `(campaign_type, id)`
- Coupon order IDs identify source transaction lines, not observed baskets
- `order_items.unit_price` nullable NUMERIC; zero quantity does not imply zero price
- Holiday identity is `(date, holiday)` with UNIQUE(date, holiday)

## 16. Sources Cited

All content above is taken verbatim from the following project files:

- `docs/notes/data_transformation_notes/transformation-contract.md`
- `docs/notes/data_transformation_notes/data-transformation-overview.md`
- `docs/notes/data_transformation_notes/data-transformation-member-c.md`
- `docs/notes/data_transformation_notes/implementation-validation.md`
- `docs/decisions/data-transformation-plan.md` (noted as excluded by its own header: "Pending — Strictly Do not consider this file's context anywhere for now")
- `docs/notes/schema-notes/final-schema.md`
- `docs/notes/customer segmentation-notes/segments-schema-mapping.md`
- `docs/notes/customer segmentation-notes/segments/Predicted Purchase Intent.md`
- `docs/notes/customer segmentation-notes/segments/Future High-Value - CLV.md`
- `docs/notes/customer segmentation-notes/segments/Discount Responsive.md`
- `docs/notes/customer segmentation-notes/segments/Churn-Risk.md`
- `docs/notes/customer segmentation-notes/segments/Channel Preference.md`
- `docs/notes/customer segmentation-notes/segments/Replenishment-Ready.md`
- `docs/notes/customer segmentation-notes/segments/Cross-Sell Opportunity.md`
- `docs/notes/customer segmentation-notes/segments/Seasonal Purchase.md`
- `docs/notes/customer segmentation-notes/segments/Cart Abandoners.md`
- `docs/Overview.md`
- `docs/decisions/final-schema-gaps.md`
- `data_prep/transform/README.md`
- `data_prep/transform/DUNNHUMBY.md`
- `data_prep/transform/BEHAVIOR_AND_RETAIL.md`
- `data_prep/transform/common.py`
- `data_prep/transform/contract.py`
- `data_prep/transform/validate.py`
- `data_prep/transform/dunnhumby.py`
- `data_prep/transform/behavior.py`
- `data_prep/transform/retail.py`
- `data_prep/transform/member_c.py`
- `data_prep/transform/run_dunnhumby.py`
- `data_prep/transform/run_member_b.py`