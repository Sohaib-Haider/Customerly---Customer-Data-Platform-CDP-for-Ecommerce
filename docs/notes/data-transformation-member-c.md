# Data Transformation — Member C

**Scope:** Coupon Redemption + Customer Churn + Direct Messaging (Segments 3, 4, 5)
**Last Updated:** 2026-10-19

---

## Principles

1. **Single store** — all datasets loaded under one `store_id` for end-to-end platform testing
2. **No ML feature computation** — only raw behavioral data transformed into canonical shape
3. **Preserve behavior** — no aggregation, no pattern changes; dates shifted but intervals preserved
4. **Kaggle import** — datasets pulled directly via Kaggle API, no local raw files
5. **Reproducible** — fixed reference date `2024-01-01` for date alignment

---

## Datasets

| # | Kaggle Dataset | Slug | Feeds Segment |
|---|---|---|---|
| 1 | Predicting Coupon Redemption | `vasudeva009/predicting-coupon-redemption` | Discount Responsive (3) |
| 2 | E-commerce Customer Churn | `samuelsemaya/e-commerce-customer-churn` | Churn-Risk (4) |
| 3 | E-commerce multichannel direct messaging | `mkechinov/direct-messaging` | Channel Preference (5) |

---

## ID Remapping

All source IDs prefixed with dataset code to avoid collisions in unified namespace:

| Dataset | Prefix | Applies To |
|---|---|---|
| Coupon Redemption | `COUPON_` | customer_id, item_id, campaign_id, coupon_id, transaction row |
| Customer Churn | `CHURN_` | customer_id (row index) |
| Direct Messaging | `MSG_` | client_id, campaign_id, message_id |

---

## Date Alignment

**Reference date:** `2024-01-01` (fixed for reproducibility)

**Approach:** For each dataset, compute `delta = reference_date - max_date_in_dataset` once, then shift **all** date columns by the same delta. This preserves intervals between events exactly. Computing delta separately per column would change durations (e.g., campaign length) if columns have different max dates.

**Churn dataset exception:** No real dates exist. `DaySinceLastOrder` is used to synthesize `order_date` as `reference_date - DaySinceLastOrder`.

---

## Dataset 1: Predicting Coupon Redemption

### Source Files

| File | Columns |
|---|---|
| train.csv | id, campaign_id, coupon_id, customer_id, redemption_status |
| campaign_data.csv | campaign_id, campaign_type, start_date, end_date |
| coupon_item_mapping.csv | coupon_id, item_id |
| customer_demographics.csv | customer_id, age_range, marital_status, rented, family_size, no_of_children, income_bracket |
| customer_transaction_data.csv | date, customer_id, item_id, quantity, selling_price, other_discount, coupon_discount |
| item_data.csv | item_id, brand, brand_type, category |

### Target Tables & Mappings

#### coupon_campaigns

| Source | Target | Transform |
|---|---|---|
| campaign_id | source_campaign_id | prefix `COUPON_` |
| campaign_type | campaign_type | none |
| start_date | start_date | shift by delta |
| end_date | end_date | shift by delta |

#### coupon_redemptions

| Source | Target | Transform |
|---|---|---|
| coupon_id | coupon_id | prefix `COUPON_` |
| campaign_id | campaign_id | prefix `COUPON_` |
| customer_id | customer_id | prefix `COUPON_` |
| redemption_status | redemption_status | boolean: 1→true, 0→false |

#### customers (coupon subset)

| Source | Target | Transform |
|---|---|---|
| customer_id | source_customer_id | prefix `COUPON_` |

#### products

| Source | Target | Transform |
|---|---|---|
| item_id | source_product_id | prefix `COUPON_` |
| brand | brand | none |
| category | category | none |

**Note:** `brand_type` not needed per segments-schema-mapping.md.

#### orders (one per transaction row)

| Source | Target | Transform |
|---|---|---|
| row index | source_order_id | `COUPON_TXN_{row_index}` |
| date | order_date | shift by delta |
| customer_id | customer_id | prefix `COUPON_` |

#### order_items

| Source | Target | Transform |
|---|---|---|
| quantity | quantity | none |
| selling_price | sales_value | treated as NET (confirmed from source analysis) |
| other_discount | other_discount | none |
| coupon_discount | coupon_discount | none |

### Not Used

- `customer_demographics.csv` — all demographics excluded per segments-schema-mapping.md
- `coupon_item_mapping.csv` — no target table (coupon-product mapping not in canonical schema)
- `test.csv` — no redemption_status (inference set, not needed)

---

## Dataset 2: E-commerce Customer Churn

### Source File

| File | Columns |
|---|---|
| data_ecommerce_customer_churn.csv | Tenure, WarehouseToHome, NumberOfDeviceRegistered, PreferedOrderCat, SatisfactionScore, MaritalStatus, NumberOfAddress, Complain, DaySinceLastOrder, CashbackAmount, Churn |

### Target Table: customers (churn subset)

| Source | Target | Transform |
|---|---|---|
| row index | source_customer_id | `CHURN_{row_index}` |
| Tenure | tenure | none |
| WarehouseToHome | warehouse_to_home | none |
| NumberOfDeviceRegistered | number_of_devices_registered | none |
| PreferedOrderCat | preferred_order_category | none |
| SatisfactionScore | satisfaction_score | none |
| Complain | complain | none |
| DaySinceLastOrder | days_since_last_order | none |
| CashbackAmount | cashback_amount | none |
| Churn | churn | boolean: 1→true, 0→false |

**Synthesized dates:**
- `order_date` = `reference_date - DaySinceLastOrder` (for date alignment consistency)
- `first_purchase_date` = `reference_date - Tenure`

### Not Used

- `MaritalStatus`, `NumberOfAddress` — excluded per segments-schema-mapping.md

---

## Dataset 3: Direct Messaging

### Source Files

| File | Columns |
|---|---|
| campaigns.csv | id, campaign_type, channel, topic, started_at, finished_at, total_count, ... |
| messages-demo.csv | id, message_id, campaign_id, message_type, client_id, channel, ..., sent_at, is_opened, is_clicked, is_unsubscribed, is_hard_bounced, is_soft_bounced, is_complained, is_blocked, is_purchased, ... |
| client_first_purchase_date.csv | client_id, first_purchase_date |
| holidays.csv | date, holiday |

### Target Tables & Mappings

#### marketing_campaigns

| Source | Target | Transform |
|---|---|---|
| id | source_campaign_id | prefix `MSG_` |
| campaign_type | campaign_type | none |
| channel | channel | none |
| topic | topic | none |
| started_at | started_at | shift by delta |
| finished_at | finished_at | shift by delta |
| total_count | total_count | none |

**Not used:** ab_test, warmup_mode, hour_limit, subject_length, subject_with_*, is_test, position — A/B config not needed.

#### message_sends

| Source | Target | Transform |
|---|---|---|
| message_id | source_message_id | prefix `MSG_` |
| campaign_id | campaign_id | prefix `MSG_` |
| client_id | customer_id | prefix `MSG_` |
| message_type | message_type | none |
| channel | channel | none |
| sent_at | sent_at | shift by delta |
| is_opened | is_opened | boolean |
| is_clicked | is_clicked | boolean |
| is_unsubscribed | is_unsubscribed | boolean |
| is_hard_bounced | is_hard_bounced | boolean |
| is_soft_bounced | is_soft_bounced | boolean |
| is_complained | is_complained | boolean |
| is_blocked | is_blocked | boolean |
| is_purchased | is_purchased | boolean |

**Channel normalization** (stored in `channel_norm`):

| Raw Channel | Normalized |
|---|---|
| email | email |
| sms | sms |
| web_push | push |
| mobile_push | push |
| whatsapp | whatsapp |

**Not used:** category, platform, email_provider, stream — technical metadata.

#### customers (messaging subset)

| Source | Target | Transform |
|---|---|---|
| client_id | source_customer_id | prefix `MSG_` |
| first_purchase_date | first_purchase_date | shift by delta |

#### holidays

| Source | Target | Transform |
|---|---|---|
| date | date | shift by delta |
| holiday | holiday | none |

---

## Customer Merge Strategy

All three datasets contribute rows to a unified `customers.csv`:

1. Coupon customers: `COUPON_{customer_id}` — no extra attributes beyond source_customer_id
2. Churn customers: `CHURN_{row_index}` — full churn attributes (tenure, warehouse_to_home, etc.)
3. Messaging customers: `MSG_{client_id}` — first_purchase_date only

No overlap between prefixes, so no dedup needed. Each row has only the columns relevant to its source dataset; other columns remain NULL.

---

## Cleaning Rules

1. **Null handling:** drop rows where primary key columns are null; keep other nulls as NULL
2. **Dedup:** exact duplicate rows removed within each source file
3. **Type casting:**
   - IDs → VARCHAR
   - booleans → BOOLEAN (0/1 → false/true, "0"/"1" → false/true)
   - dates → DATE
   - timestamps → TIMESTAMPTZ
   - quantities → INTEGER
   - prices/amounts → NUMERIC
4. **Whitespace:** trim leading/trailing whitespace from all string columns using `df.apply(lambda x: x.str.strip() if x.dtype == 'object' else x)` before writing CSVs

---

## Output

All CSVs written to `data_prep/processed/`:

| File | Source Dataset | Canonical Table |
|---|---|---|
| `coupon_campaigns.csv` | Coupon | coupon_campaigns |
| `coupon_redemptions.csv` | Coupon | coupon_redemptions |
| `customers.csv` | Coupon + Churn + Messaging | customers (merged) |
| `products.csv` | Coupon | products |
| `orders.csv` | Coupon | orders |
| `order_items.csv` | Coupon | order_items |
| `marketing_campaigns.csv` | Messaging | marketing_campaigns |
| `message_sends.csv` | Messaging | message_sends |
| `holidays.csv` | Messaging | holidays |

---

## Verification Checklist

After transformation, verify:
- [ ] All 9 output CSVs exist and are non-empty
- [ ] No ID collisions across datasets (prefixes work)
- [ ] All dates within reasonable range of reference date `2024-01-01`
- [ ] No NULL values in primary key columns
- [ ] Channel values in `message_sends` are from: email, sms, web_push, mobile_push, whatsapp
- [ ] `selling_price` mapped as net (not gross)
- [ ] Customer count = coupon customers + churn customers + messaging customers
- [ ] One order per transaction row in coupon data
