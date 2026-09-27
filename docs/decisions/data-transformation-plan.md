# Data Transformation Plan — One Ecommerce Store

Status: Pending(Strictly Do not consider this file's context anywhere for now) — implementation in progress
Created: 2026-09-24

## Goal & Core Principle

Transform the Kaggle datasets into **one multi-category ecommerce store's** data — step by step, source by source — where every customer, product, order, and campaign carries real, internally-consistent behavior. **Never fabricate identity**: no cross-dataset customer linking, no invented history. Customer identity within a source is real; identity only exists within its own source (all ids are anonymous per-source; no overlap exists by definition).

## Canonical Store Schema (all phases conform to this)

| Table | Key columns |
|---|---|
| `customers` | `customer_id`, `source`, `raw_id`, `age_band`, `income_band`, `marital_status`, `household_size`, `kid_count`, `is_homeowner`, `region`, `department` |
| `products` | `product_id`, `source`, `raw_id`, `name`, `brand`, `department`, `category`, `size`, `unit_price` |
| `orders` | `order_id`, `customer_id`, `order_date`, `region`, `department`, `source`, header discounts |
| `order_items` | `order_id`, `product_id`, `quantity`, `unit_price`, `retail_disc`, `coupon_disc` |
| `coupon_campaigns` | `campaign_id`, `campaign_type`, `start_date`, `end_date`, `source` |
| `coupon_redemptions` | `redemption_id`, `customer_id`, `coupon_id`, `campaign_id`, `date`, `discount`, `source` |

Every table carries a `source` (and later `region`/`department`) tag → full traceability, no artifacts.

## Store Calendar

- **Store epoch:** `2024-01-01` (recent = sane for a CDP demo).
- Regions open the same day, share the same window (two regions of the grocery chain).
- All source dates mapped through one documented offset function in `common.py`.

---

## Phase 1 — Grocery block (Coupon Redemption + Dunnhumby)

Unlocks segments: Discount Responsive, Replenishment-Ready, Cross-Sell, Seasonal.

### Step 0 — Download & profile

- Fetch both Kaggle datasets into `data_prep/raw/` via `kaggle` CLI.
- Profile each file: row counts, dtypes, nulls, unique values of categoricals.
- **Outputs:** profile notes; resolves open items (value vocabularies, date ranges).

### Step 1 — Canonical frames per source (`transform/dunnhumby.py`, `transform/coupon.py`)

**Dunnhumby (region A, 8 files):**

- `hh_demographic → customers`: `household_key`→`DH-{k}`; map `AGE_DESC→age_band`, `INCOME_DESC→income_band`, `MARITAL_STATUS_CODE→marital_status`, `HOMEOWNER_DESC→is_homeowner`, `HOUSEHOLD_SIZE_DESC→household_size`, `KID_CATEGORY_DESC→kid_count`.
- `product → products`: `PRODUCT_ID`→`DH-{id}`; `DEPARTMENT`+`COMMODITY_DESC`→canonical grocery taxonomy; `BRAND`, `CURR_SIZE_OF_PRODUCT`.
- `transaction_data → orders + order_items`: `BASKET_ID`→`A-{basket}` order, `DAY→order_date`, line-level `QUANTITY`, `SALES_VALUE→unit_price`, `RETAIL_DISC`, `COUPON_DISC`, `COUPON_MATCH_DISC`.
- `campaign_desc/campaign_table → coupon_campaigns`; `coupon_redempt → coupon_redemptions`; `coupon → coupon→product link`; `causal_data →` display/mailer flags (kept for Seasonal/Discount features).

**Coupon Redemption (region B, 7 files):**

- `customer_demographics → customers`: `customer_id`→`CR-{id}`; map `age_range`, `income_bracket`, `marital_status`, `rented→is_homeowner` (inverse), `family_size`, `no_of_children`.
- `item_data → products`: `item_id`→`CR-{id}`; `brand`, `brand_type`, `category`→canonical taxonomy.
- `customer_transaction_data → orders/order_items`: group by `customer_id × date` → synthesized order `B-{n}`; `quantity`, `selling_price`, `other_discount`, `coupon_discount`.
- `campaign_data → coupon_campaigns` (dates shifted so first campaign = store epoch); `coupon_item_mapping → coupon→product`; `train.csv → coupon_redemptions` (labeled `redemption_status`); `test.csv` left unmerged (unlabeled).

**Canonical grocery taxonomy** (hand-authored config in `common.py`): e.g. Dunnhumby `DEPARTMENT` and Coupon `category` values mapped into one tree (`Dairy`, `Bakery`, `Household`, `Beverages`, `Snacks`, …) so both regions' products live in the same catalog — the catalog both Multi-Discount and Replenishment models read.

### Step 2 — Merged store view (`transform/store_merge.py`)

- Union the two region frames → `processed/store/{customers,products,orders,order_items,coupon_campaigns,coupon_redemptions}.csv`.
- No id collisions (namespaced), no customer overlap, all dates inside the shared window.

### Step 3 — Per-segment training datasets (`transform/segments/`)

Build 4 segment datasets from the merged store → `processed/store/segments/`:

- `discount_responsive.csv` — per-customer coupon redemption + discount-share features; label from `redemption_status` & transaction coupon usage.
- `replenishment_ready.csv` — per `(customer, product)` repurchase-cycle features → predict days-until-next-purchase.
- `cross_sell_opportunity.csv` — basket co-occurrence → predict complementary product to recommend.
- `seasonal_purchase.csv` — per-customer week-of-year buying pattern → next-season buyer label.

### Step 4 — Validation & docs

- `transform/validate.py`: sanity checks — no cross-region customers; unique order/product ids; monotonic dates in window; recency distributions sane; **seasonality alignment check** (weekly sales shape of region A vs region B in the shared window flags any "two stores" red flag) → writes a summary report.
- Persist mapping tables, taxonomy, and timeline offsets to `docs/decisions/`.

---

## Phase 2 — Electronics block (Multi-category events)

- `2019-Oct/Nov` event logs → canonical `events` table.
- Extract `event_type=purchase` → `orders`/`order_items` (each purchase event = a real order); `view`/`cart` kept for Purchase Intent + Cart Abandoners features.
- `product_id`→canonical id, `category_code`/`brand`→electronics taxonomy; prices normalized to store currency (documented factor for rubles).
- New region (`region=C`), same departmental `department=electronics`, mapped into the shared window (Oct–Nov maps cleanly).
- Ships Purchase Intent + Cart Abandoners segment data.

## Phase 3 — Giftware block (Online Retail II)

- Invoices → `orders`; `Customer ID`→customers (`Country` kept); `StockCode`/`Description`→products (named!); `InvoiceDate`→shift to store window; GBP→store currency factor.
- Ships CLV segment data (RFM-ready).

## Phase 4 — NOT merged (Churn + Channel messaging)

Deliberate non-merge:

- **Churn** (`data_ecommerce_customer_churn.csv`): separate demo tenant, or hold-out set to validate a churn model trained on the merged store.
- **Channel messaging**: separate tenant for the Channel Preference model — engagement features from `messages-demo.csv`, no fabricated orders.
- Both use the same canonical `customers` shape so the platform accepts them as tenants.

---

## Open items (resolve after download, Step 0)

1. Exact categorical vocabularies (AGE_DESC, marital codes, coupon `category`) → finalize mapping configs.
2. Coupon campaign date-range length vs Dunnhumby's 104 weeks → finalize shared-window bounds + seasonality alignment.
3. Verify `BASKET_ID` uniqueness and `household_key` reuse of `STORE_ID` in Dunnhumby.
4. Price scales: confirm Coupon `selling_price` vs Dunnhumby `SALES_VALUE` are comparable (both small-dollar grocery) before merging without scaling.

## Tooling & structure

```
data_prep/
├── raw/dunnhumby/   raw/coupon_redemption/
├── transform/
│   ├── common.py            # ids, calendar offsets, taxonomy, enums
│   ├── dunnhumby.py  coupon.py  store_merge.py  validate.py
│   └── segments/{discount,replenishment,cross_sell,seasonal}.py
└── processed/{dunnhumby,coupon,store,store/segments}/*.csv
```

Pure pandas + CSVs; no backend changes in this phase.

## Source datasets

| Segment(s) | Kaggle dataset | Files |
|---|---|---|
| Purchase Intent, Cart Abandoners | ecommerce-behavior-data-from-multi-category-store | 2019-Oct.csv, 2019-Nov.csv |
| CLV | online-retail-ii-uci | online_retail_II.csv |
| Discount Responsive | predicting-coupon-redemption | train, test, campaign_data, coupon_item_mapping, customer_demographics, customer_transaction_data, item_data |
| Churn-Risk | e-commerce-customer-churn | data_ecommerce_customer_churn.csv |
| Channel Preference | direct-messaging | campaigns, messages-demo, client_first_purchase_date, holidays |
| Replenishment / Cross-Sell / Seasonal | dunnhumby-the-complete-journey | transaction_data, hh_demographic, product, causal_data, coupon, coupon_redempt, campaign_table, campaign_desc |