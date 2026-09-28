# CDP — Final Canonical Database Schema

**Status:** Proposed final schema — awaiting sign-off on 16 Open Decisions before SQL generation
**Last Updated:** 2026-09-28
**Source documents (authoritative):** schema-blueprint.md, segments-schema-mapping.md, segments-overview.md, the 9 files in `segments/`, kpi-schema-mapping.md, `docs/Overview.md`

> **Scope of authority used in this document**
> - `kpi-schema-mapping.md` is treated as a **fixed requirements contract**. A column referenced by any KPI card is never removed, renamed or re-typed on judgement alone, even where the schema has a better alternative. Conflicts are recorded in Open Decisions instead.
> - `docs/decisions/data-transformation-plan.md` is **excluded**: it is marked *"Pending (Strictly Do not consider this file's context anywhere for now)"*. No column, provenance marker, type or constraint in this document is based on it.
> - Where the valid documentation contradicts itself, both readings are cited and the item is marked **Decision Required** rather than resolved by guessing.

---

## Overview

This document defines the canonical PostgreSQL schema for the multi-tenant e-commerce Customer Data Platform (CDP). It translates the design intent from the schema blueprint into concrete table definitions with columns, types, constraints, indexes, and field provenance.

### Design Principles (carried forward from blueprint)

1. **Multi-tenancy:** Shared tables, `store_id` on every tenant-owned row.
2. **No redundant columns:** Each column serves at least one segment, KPI, or platform operation.
3. **Computed columns are platform-owned:** Calculated fields are never uploaded by stores.
4. **Traceability:** Source columns and original IDs track where data came from.
5. **One canonical schema:** No per-store customization or extensions.
6. **Financials at order item level:** All monetary values and discounts live on `order_items`.

### Field Provenance Legend

| Marker | Meaning |
|--------|---------|
| **[S]** | Uploaded / source data (from store's raw files) |
| **[P]** | Platform-generated (system IDs, timestamps, status) |
| **[C]** | Computed / derived (platform calculates from other fields) |

---

## Table of Contents

1. [stores](#1-stores)
2. [store_users](#2-store_users)
3. [ingestion_uploads](#3-ingestion_uploads)
4. [ingestion_column_mappings](#4-ingestion_column_mappings)
5. [customers](#5-customers)
6. [products](#6-products)
7. [orders](#7-orders)
8. [order_items](#8-order_items)
9. [events](#9-events)
10. [customer_features](#10-customer_features)
11. [segments](#11-segments)
12. [customer_segments](#12-customer_segments)
13. [marketing_campaigns](#13-marketing_campaigns)
14. [message_sends](#14-message_sends)
15. [coupon_campaigns](#15-coupon_campaigns)
16. [coupon_redemptions](#16-coupon_redemptions)
17. [holidays](#17-holidays)
18. [causal_data](#18-causal_data)
19. [kpi_snapshots](#19-kpi_snapshots)
20. [KPI Coverage](#kpi-coverage)
21. [Open Decisions](#open-decisions)

---

## 1. stores

**Purpose:** Tenant store accounts. Every other tenant-owned table references this table for isolation.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated store ID (PK) |
| `name` | `VARCHAR(255)` | NOT NULL | [S] | Store display name |
| `plan` | `VARCHAR(50)` | NOT NULL | [S] | Subscription tier: `free`, `pro`, `enterprise` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Account creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Last update timestamp |

**Primary Key:** `id`

**Unique Constraints:** None

**Indexes:**
- `idx_stores_plan` on `(plan)` — for "Stores by Plan" KPI
- `idx_stores_created_at` on `(created_at)` — for "New Stores (period)" KPI

---

## 2. store_users

**Purpose:** Team members belonging to a store. Handles authentication and role-based access.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated user ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `email` | `VARCHAR(255)` | NOT NULL | [S] | User email (login) |
| `role` | `VARCHAR(50)` | NOT NULL | [S] | `owner`, `admin`, `member` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Account creation timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, email)` — one account per email per store

**Indexes:**
- `idx_store_users_store_id` on `(store_id)`
- `idx_store_users_role` on `(role)` — for "Users by Role" KPI

---

## 3. ingestion_uploads

**Purpose:** Tracks every file upload for audit, debugging, and traceability.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Upload ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `uploaded_by` | `UUID` | NOT NULL | [P] | FK → store_users.id |
| `file_name` | `VARCHAR(500)` | NOT NULL | [S] | Original filename |
| `file_type` | `VARCHAR(50)` | NOT NULL | [S] | `csv`, `json`, `parquet`, etc. |
| `row_count` | `INTEGER` | NULL | [P] | Number of rows parsed |
| `status` | `VARCHAR(50)` | NOT NULL | [P] | `pending`, `mapping`, `ingesting`, `completed`, `failed` |
| `error_message` | `TEXT` | NULL | [P] | Error details if failed |
| `uploaded_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Upload timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE
- `uploaded_by` → `store_users.id` ON DELETE RESTRICT

**Indexes:**
- `idx_ingestion_uploads_store_id` on `(store_id)`
- `idx_ingestion_uploads_status` on `(status)`

---

## 4. ingestion_column_mappings

**Purpose:** LLM-powered column mapping. Records how source columns map to canonical schema columns for each upload.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Mapping ID (PK) |
| `upload_id` | `UUID` | NOT NULL | [P] | FK → ingestion_uploads.id |
| `source_column` | `VARCHAR(255)` | NOT NULL | [S] | Column name in the uploaded file |
| `target_table` | `VARCHAR(100)` | NOT NULL | [P] | Canonical table name |
| `target_column` | `VARCHAR(100)` | NOT NULL | [P] | Canonical column name |
| `confidence` | `NUMERIC(3,2)` | NULL | [P] | LLM confidence score (0.00–1.00) |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Mapping creation timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `upload_id` → `ingestion_uploads.id` ON DELETE CASCADE

**Unique Constraints:**
- `(upload_id, source_column)` — one mapping per source column per upload

**Indexes:**
- `idx_ingestion_column_mappings_upload_id` on `(upload_id)`

---

## 5. customers

**Purpose:** Normalized customer records. The central entity for segmentation, CLV, and customer KPIs.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated customer ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `source_customer_id` | `VARCHAR(255)` | NOT NULL | [S] | Original customer ID from store's system |
| `first_purchase_date` | `DATE` | NULL | [S] | Date of first order (for new customer KPIs) |
| `tenure` | `INTEGER` | NULL | [S] | Days as a customer (from churn dataset) |
| `warehouse_to_home` | `NUMERIC(10,2)` | NULL | [S] | Delivery distance in km |
| `number_of_devices_registered` | `INTEGER` | NULL | [S] | Number of registered devices |
| `satisfaction_score` | `SMALLINT` | NULL | [S] | CSAT score (1–5) |
| `complain` | `BOOLEAN` | NULL | [S] | Whether customer raised a complaint |
| `days_since_last_order` | `INTEGER` | NULL | [S] | Days since most recent order |
| `cashback_amount` | `NUMERIC(12,2)` | NULL | [S] | Total cashback earned |
| `preferred_order_category` | `VARCHAR(255)` | NULL | [S] | Most-ordered product category |
| `churn` | `BOOLEAN` | NULL | [S] | **LABEL** — ground-truth churn flag from source dataset |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Last update timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, source_customer_id)` — one customer per source ID per store

**Indexes:**
- `idx_customers_store_id` on `(store_id)`
- `idx_customers_first_purchase_date` on `(first_purchase_date)` — for "New Customers" KPI
- `idx_customers_days_since_last_order` on `(days_since_last_order)` — for "Active Customers (30d)" KPI
- `idx_customers_satisfaction_score` on `(satisfaction_score)` — for CSAT KPI
- `idx_customers_complain` on `(complain)` — for "Complaint Rate" KPI

> **Note on churn target leakage:** `churn` is a documented source column (segments-schema-mapping.md, Segment 4: `Churn | customers | churn | LABEL`) and is required by 2 KPI cards (Customer Churn Rate, Churn Rate by Category), so it is stored here. However, it is the **prediction target** for the Churn-Risk segment and must **never** be supplied as a model input feature. The same leakage warning applies to `days_since_last_order` (segments-schema-mapping.md: "high target leakage risk (churn likely defined by this feature)").
>
> **Requirement:** the ML feature pipeline must use an explicit allowlist of model inputs for the Churn-Risk segment. `churn` must be excluded from that allowlist, and `days_since_last_order` should be excluded unless leakage is empirically ruled out. See Open Decisions #1.

---

## 6. products

**Purpose:** Normalized product catalog. Referenced by order_items, events, and causal_data.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated product ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `source_product_id` | `VARCHAR(255)` | NOT NULL | [S] | Original product ID from store's system |
| `name` | `VARCHAR(500)` | NOT NULL | [S] | Product name / description |
| `brand` | `VARCHAR(255)` | NULL | [S] | Product brand |
| `category` | `VARCHAR(255)` | NULL | [S] | Product category |
| `department` | `VARCHAR(255)` | NULL | [S] | Store department (Dunnhumby) |
| `commodity_desc` | `VARCHAR(255)` | NULL | [S] | Commodity description (Dunnhumby) |
| `unit_price` | `NUMERIC(12,2)` | NULL | [S] | Current unit price |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Last update timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, source_product_id)` — one product per source ID per store

**Indexes:**
- `idx_products_store_id` on `(store_id)`
- `idx_products_brand` on `(brand)` — for "Revenue by Brand" KPI
- `idx_products_category` on `(category)` — for "Revenue by Category" KPI
- `idx_products_department` on `(department)` — for "Revenue by Department" KPI

> **`unit_price` source — Decision Required (#11):** the "Average Product Price" KPI card references `products.unit_price`, but no segment mapping assigns a source column to it. In every documented dataset, price is a transaction-level attribute (`order_items.unit_price` / `order_items.sales_value`), not a catalog-level one. Whether `products.unit_price` is a stored catalog price or should be derived from `order_items` is undetermined. Column retained because the KPI requires it.
>
> **`category` vs `department`:** both are retained because they are populated from different documented sources — `category` from the coupon dataset's `item_data.category` (Segment 3) and `department` / `commodity_desc` from the Dunnhumby `product` file (Segments 6–8). They are not duplicates.

---

## 7. orders

**Purpose:** Order header records. Groups order_items and links to customers.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated order ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id (tenant isolation) |
| `source_order_id` | `VARCHAR(255)` | NOT NULL | [S] | Original order ID from store's system |
| `customer_id` | `UUID` | NOT NULL | [P] | FK → customers.id |
| `order_date` | `DATE` | NOT NULL | [S] | Order date |
| `order_timestamp` | `TIMESTAMPTZ` | NULL | [S] | Full timestamp with time-of-day |
| `week_no` | `SMALLINT` | NULL | [S] | Native week number (Dunnhumby: 1–104) |
| `day_no` | `SMALLINT` | NULL | [S] | Native study day (Dunnhumby: 1–711) |
| `source_store_id` | `INT` | NULL | [S] | Source `STORE_ID` (Dunnhumby) — **name Decision Required, see #3** |
| `region` | `VARCHAR(255)` | NULL | **Decision Required** | Geographic region — required by 2 KPI cards but has no documented source column. See #4 |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Record creation timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE
- `customer_id` → `customers.id` ON DELETE RESTRICT

**Unique Constraints:**
- `(store_id, source_order_id)` — one order per source ID per store

**Indexes:**
- `idx_orders_store_id` on `(store_id)`
- `idx_orders_customer_id` on `(customer_id)` — for customer-level aggregations
- `idx_orders_order_date` on `(order_date)` — for revenue by period, growth rate
- `idx_orders_order_timestamp` on `(order_timestamp)` — for "Order Peak Hour" KPI
- `idx_orders_week_no` on `(week_no)` — for "Revenue by Week" KPI
- `idx_orders_region` on `(region)` — for "Orders by Region" KPI

> **`store_id` name collision — Decision Required (#3):** segments-schema-mapping.md Segments 6 and 8 map the source column `STORE_ID` to a target column literally named `store_id` on `orders`, and the same name on `causal_data`. That collides with the tenant `store_id` required by schema-blueprint.md principle 1. The same conflict exists in `causal_data`.
>
> The column is shown here as `source_store_id`, following the `source_*` convention already used for `source_customer_id`, `source_order_id` and `source_product_id` (schema-blueprint.md principle 4). **This rename is a proposal, not an applied decision** — the documented target name is `store_id`.
>
> **`region` — Decision Required (#4):** 2 KPI cards require `orders.region` (Revenue by Region, Orders by Region), but no segment mapping assigns any source column to it. The only geographic column in any source is `Country` (Online Retail II), which segments-schema-mapping.md Segment 2 explicitly excludes: *"`Country` — Not needed for this segment (no segment/KPI uses geo)"*. That exclusion contradicts the KPI mapping. Not guessed here.
>
> **`order_timestamp` — Decision Required (#5):** the "Order Peak Hour" card requires it, but no segment mapping assigns a source column to it (only `order_date` is mapped). Per-source availability of a time component differs. Nullable is the only safe constraint.
>
> **`source_order_id` nullability — Decision Required (#8):** documented source mappings exist for Online Retail II (`Invoice`) and Dunnhumby (`BASKET_ID`) only. The coupon dataset maps `date → order_date` with no order identifier, and the multi-category event dataset maps to `events` only. Whether `source_order_id` can be `NOT NULL` for all ingested datasets is therefore undetermined.

---

## 8. order_items

**Purpose:** Line items per order. **All financial values and discounts live here** per the blueprint design principle.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated line item ID (PK) |
| `order_id` | `UUID` | NOT NULL | [P] | FK → orders.id |
| `product_id` | `UUID` | NOT NULL | [P] | FK → products.id |
| `quantity` | `INTEGER` | NOT NULL | [S] | Number of units |
| `unit_price` | `NUMERIC(12,2)` | NOT NULL | [S] | Price per unit at time of sale |
| `sales_value` | `NUMERIC(12,2)` | NOT NULL | [S] | Net sales value (after discounts for Dunnhumby; quantity × unit_price for Online Retail II) |
| `retail_disc` | `NUMERIC(12,2)` | NOT NULL DEFAULT 0 | [S] | Retail discount amount |
| `coupon_disc` | `NUMERIC(12,2)` | NOT NULL DEFAULT 0 | [S] | Coupon discount amount |
| `coupon_match_disc` | `NUMERIC(12,2)` | NOT NULL DEFAULT 0 | [S] | Coupon match discount amount |
| `other_discount` | `NUMERIC(12,2)` | NOT NULL DEFAULT 0 | [S] | Other discount amount |

**Primary Key:** `id`

**Foreign Keys:**
- `order_id` → `orders.id` ON DELETE CASCADE
- `product_id` → `products.id` ON DELETE RESTRICT

**Indexes:**
- `idx_order_items_order_id` on `(order_id)` — for order-level aggregations
- `idx_order_items_product_id` on `(product_id)` — for product-level aggregations

> **Naming — `sales_value` vs `net_sales_value`:** segments-schema-mapping.md is itself inconsistent. Segment 3 maps the coupon source column `selling_price` to a target named `net_sales_value`, and the `discount_dependency_ratio` formula in the same document reads `order_items.net_sales_value`. Segment 6 of the same document maps the Dunnhumby source column `SALES_VALUE` to `order_items.sales_value`, and all 17 Revenue KPI cards in kpi-schema-mapping.md reference `order_items.sales_value`.
>
> `sales_value` is used here because it is the name used by the KPI requirements document and by segments-schema-mapping.md Segment 6. **Decision Required (#9):** segments-schema-mapping.md Segment 3 and one feature formula still use `net_sales_value` and need to be aligned to whichever name is chosen. Not resolved unilaterally.
>
> **Pricing note (documented, unverified):** segments-schema-mapping.md Segment 2 states that for Online Retail II there are no discount columns, so the net amount is `quantity * unit_price`. Segment 3 states an explicit verification is still outstanding for the coupon dataset's `selling_price`: *"Confirm whether `selling_price` is net (post-discount) or gross… If gross, use `selling_price + other_discount + coupon_discount` as `net_sales_value` in all formulas."* This is **Decision Required (#10)** and requires inspecting the actual file. It does not change the column list either way.

---

## 9. events

**Purpose:** Behavioral events (view, cart, purchase, remove_from_cart). Raw material for purchase intent and cart abandonment segments.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated event ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `customer_id` | `UUID` | NOT NULL | [P] | FK → customers.id |
| `session_id` | `VARCHAR(255)` | NOT NULL | [S] | Groups events into sessions |
| `event_type` | `VARCHAR(50)` | NOT NULL | [S] | `view`, `cart`, `purchase`, `remove_from_cart` |
| `event_timestamp` | `TIMESTAMPTZ` | NOT NULL | [S] | When the event occurred |
| `product_id` | `UUID` | NULL | [P] | FK → products.id (nullable: may not match catalog) |
| `category_id` | `VARCHAR(255)` | NULL | [S] | Category ID from source |
| `category_code` | `VARCHAR(255)` | NULL | [S] | Category grouping from source |
| `brand` | `VARCHAR(255)` | NULL | [S] | Brand from source |
| `price` | `NUMERIC(12,2)` | NULL | [S] | Price at time of event |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE
- `customer_id` → `customers.id` ON DELETE CASCADE
- `product_id` → `products.id` ON DELETE SET NULL

**Indexes:**
- `idx_events_store_id` on `(store_id)`
- `idx_events_customer_id` on `(customer_id)` — for customer-level feature computation
- `idx_events_event_type` on `(event_type)` — for filtering by event type
- `idx_events_event_timestamp` on `(event_timestamp)` — for recency calculations
- `idx_events_session_id` on `(session_id)` — for session-level aggregations
- `idx_events_product_id` on `(product_id)` — for product-level analysis

---

## 10. customer_features

**Purpose:** Computed ML features per customer. Populated by scheduled platform jobs. One row per customer per store. These features feed all 9 ML segments.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `customer_id` | `UUID` | NOT NULL | [P] | FK → customers.id (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `recency` | `INTEGER` | NULL | [C] | Days since last order |
| `frequency` | `INTEGER` | NULL | [C] | Count of distinct orders |
| `monetary` | `NUMERIC(12,2)` | NULL | [C] | Total spend (sum of sales_value) |
| `avg_order_value` | `NUMERIC(12,2)` | NULL | [C] | monetary / frequency |
| `customer_tenure` | `INTEGER` | NULL | [C] | Days since first order |
| `total_items_bought` | `INTEGER` | NULL | [C] | Sum of quantity across all orders |
| `unique_products_bought` | `INTEGER` | NULL | [C] | `COUNT(DISTINCT product_id)` per customer — **duplicate of `product_loyalty`, see #12** |
| `max_single_order_value` | `NUMERIC(12,2)` | NULL | [C] | Maximum order value |
| `min_single_order_value` | `NUMERIC(12,2)` | NULL | [C] | Minimum order value |
| `purchase_regularity` | `NUMERIC(10,2)` | NULL | [C] | Std dev of days between consecutive orders |
| `view_count` | `INTEGER` | NULL | [C] | Total view events |
| `cart_count` | `INTEGER` | NULL | [C] | Total cart events |
| `purchase_count` | `INTEGER` | NULL | [C] | Total purchase events |
| `view_to_cart_rate` | `NUMERIC(5,4)` | NULL | [C] | cart_count / view_count |
| `cart_to_purchase_rate` | `NUMERIC(5,4)` | NULL | [C] | purchase_count / cart_count |
| `last_view_days` | `INTEGER` | NULL | [C] | Days since last view event |
| `session_count` | `INTEGER` | NULL | [C] | Count of distinct sessions |
| `avg_viewed_price` | `NUMERIC(12,2)` | NULL | [C] | Average price of viewed products |
| `cart_removal_count` | `INTEGER` | NULL | [C] | `COUNT(event_type='remove_from_cart')` — **duplicate of `remove_from_cart_count`, see #12** |
| `cart_removal_rate` | `NUMERIC(5,4)` | NULL | [C] | `cart_removal_count / cart_count` — **duplicate of `remove_from_cart_rate`, see #12** |
| `cart_abandonment_rate` | `NUMERIC(5,4)` | NULL | [C] | (cart_count - purchase_count) / cart_count |
| `last_cart_days` | `INTEGER` | NULL | [C] | Days since last cart event |
| `avg_cart_value` | `NUMERIC(12,2)` | NULL | [C] | Average price of carted products |
| `remove_from_cart_count` | `INTEGER` | NULL | [C] | `COUNT(event_type='remove_from_cart')` per customer — **duplicate of `cart_removal_count`, see #12** |
| `remove_from_cart_rate` | `NUMERIC(5,4)` | NULL | [C] | `remove_from_cart_count / cart_count` — **duplicate of `cart_removal_rate`, see #12** |
| `total_discount_received` | `NUMERIC(12,2)` | NULL | [C] | Sum of all discounts |
| `discount_dependency_ratio` | `NUMERIC(5,4)` | NULL | [C] | total_discount_received / monetary |
| `coupon_redemption_rate` | `NUMERIC(5,4)` | NULL | [C] | Redeemed coupons / total coupons received |
| `avg_discount_per_order` | `NUMERIC(12,2)` | NULL | [C] | Average discount per order |
| `discount_order_frequency` | `NUMERIC(5,4)` | NULL | [C] | Orders with discount / total orders |
| `email_open_rate` | `NUMERIC(5,4)` | NULL | [C] | Email opens / emails sent |
| `email_click_rate` | `NUMERIC(5,4)` | NULL | [C] | Email clicks / emails sent |
| `push_open_rate` | `NUMERIC(5,4)` | NULL | [C] | Push opens / push sent |
| `sms_open_rate` | `NUMERIC(5,4)` | NULL | [C] | SMS opens / SMS sent |
| `unsubscribe_rate` | `NUMERIC(5,4)` | NULL | [C] | Unsubscribes / total messages |
| `bounce_rate` | `NUMERIC(5,4)` | NULL | [C] | (hard + soft bounces) / total messages |
| `purchase_rate` | `NUMERIC(5,4)` | NULL | [C] | Purchases / total messages |
| `days_since_last_purchase` | `INTEGER` | NULL | [C] | Days since last purchase of same product |
| `purchase_frequency` | `NUMERIC(10,2)` | NULL | [C] | Orders per product (customer+product level) |
| `avg_days_between_purchases` | `NUMERIC(10,2)` | NULL | [C] | Avg days between purchases of same product |
| `product_loyalty` | `INTEGER` | NULL | [C] | `COUNT(DISTINCT product_id)` per customer — **duplicate of `unique_products_bought`, see #12** |
| `basket_size` | `NUMERIC(10,2)` | NULL | [C] | Avg distinct products per order |
| `category_diversity` | `INTEGER` | NULL | [C] | Count of distinct departments purchased |
| `cross_category_purchase` | `NUMERIC(5,4)` | NULL | [C] | category_diversity / unique_products_bought |
| `primary_dept` | `VARCHAR(255)` | NULL | [C] | Most-purchased department |
| `complementary_affinity` | `INTEGER` | NULL | [C] | Products outside primary department |
| `brand_concentration` | `NUMERIC(5,4)` | NULL | [C] | unique brands / unique products |
| `purchase_by_week` | `JSONB` | NULL | [C] | Weekly purchase counts (JSON: {"1": 5, "2": 3, ...}) |
| `seasonal_concentration` | `NUMERIC(10,2)` | NULL | [C] | Max weekly purchases / avg weekly purchases |
| `seasonal_category` | `VARCHAR(255)` | NULL | [C] | Department purchased in peak weeks |
| `seasonal_purchase_ratio` | `NUMERIC(5,4)` | NULL | [C] | Purchases in peak weeks / total purchases |
| `promo_response_rate` | `NUMERIC(5,4)` | NULL | [C] | Purchases with display/mailer / total purchases |
| `computed_at` | `TIMESTAMPTZ` | NOT NULL | [P] | When features were last computed |

**Primary Key:** `customer_id`

**Foreign Keys:**
- `customer_id` → `customers.id` ON DELETE CASCADE
- `store_id` → `stores.id` ON DELETE CASCADE

**Indexes:**
- `idx_customer_features_store_id` on `(store_id)`
- `idx_customer_features_computed_at` on `(computed_at)` — for incremental refresh

> **Design note:** This is a wide table (one row per customer). All features are computed by the platform via scheduled jobs. The `purchase_by_week` column uses JSONB for flexibility. kpi-schema-mapping.md requires these metrics to *"calculate dynamically or refresh on a rolling 7-day schedule"*; the refresh mechanism (full vs. incremental) is not specified in the valid documentation — see Open Decisions #13.
>
> **Grain conflict — Decision Required (#14):** this table has one row per customer, but segments-schema-mapping.md Segment 6 defines three features at a **customer × product** grain, not per customer:
> - `days_since_last_purchase` — *"SQL per customer+product"*
> - `purchase_frequency` — *"SQL per customer+product"*
> - `avg_days_between_purchases` — *"SQL window function per customer+product"*
>
> Yet kpi-schema-mapping.md references them as single per-customer values (`customer_features.days_since_last_purchase`, `customer_features.purchase_frequency`). A single-row-per-customer table can only hold an aggregate of these (e.g. minimum across products, or average across products). Which aggregate is intended is undetermined, and the alternative — a separate customer×product table — is a design change that is not made here.
>
> **Duplicate feature columns — Decision Required (#12):** three pairs of columns in this table have identical documented formulas under two different names, and **both names are referenced by kpi-schema-mapping.md**, so neither can be removed without breaking a documented KPI requirement:
>
> | Duplicate pair | Formula (segments-schema-mapping.md) | Referenced by |
> |---|---|---|
> | `cart_removal_count` / `remove_from_cart_count` | `COUNT(*) WHERE event_type='remove_from_cart'` | Segment 1 + "Purchase Intent Score" card 1 uses `cart_removal_rate`; "Cart Abandonment Rate" card 5 uses `remove_from_cart_count` |
> | `cart_removal_rate` / `remove_from_cart_rate` | same numerator ÷ `cart_count` | as above |
> | `unique_products_bought` / `product_loyalty` | `COUNT(DISTINCT product_id)` per customer | CLV card 6 uses `unique_products_bought`; "Replenishment Readiness" card 6 uses `product_loyalty` |
>
> This conflicts with schema-blueprint.md principle 2 ("no redundant columns"). All 6 duplicate columns are retained so that no KPI card loses a column it references; consolidating them is a Decision Required because the KPI mapping is treated as a fixed requirements contract.

---

## 11. segments

**Purpose:** Segment definitions. Stores the 9 ML segments available on the platform.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Segment ID (PK) |
| `name` | `VARCHAR(100)` | NOT NULL | [P] | Segment name (e.g., "Predicted Purchase Intent") |
| `description` | `TEXT` | NULL | [P] | Human-readable description |
| `segment_type` | `VARCHAR(50)` | NOT NULL | [P] | `ml` or `rule` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Creation timestamp |

**Primary Key:** `id`

**Unique Constraints:**
- `name` — segment names are unique

**Indexes:** None beyond PK

> **Note:** Segments are platform-defined, not store-specific. All stores share the same 9 segments.

---

## 12. customer_segments

**Purpose:** Customer-to-segment assignments with ML scores. Powers segment distribution KPIs and targeted marketing.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Assignment ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `customer_id` | `UUID` | NOT NULL | [P] | FK → customers.id |
| `segment_id` | `UUID` | NOT NULL | [P] | FK → segments.id |
| `score` | `NUMERIC(5,4)` | NULL | [C] | ML probability score (0.0000–1.0000) |
| `assigned_at` | `TIMESTAMPTZ` | NOT NULL | [P] | When the assignment was computed |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE
- `customer_id` → `customers.id` ON DELETE CASCADE
- `segment_id` → `segments.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, customer_id, segment_id)` — one assignment per customer per segment per store

**Indexes:**
- `idx_customer_segments_store_id` on `(store_id)`
- `idx_customer_segments_customer_id` on `(customer_id)`
- `idx_customer_segments_segment_id` on `(segment_id)` — for "Segment Size" KPI
- `idx_customer_segments_score` on `(score)` — for "Average Segment Score" KPI

> **Note:** `store_id` is included because this is a tenant-owned table (schema-blueprint.md principle 1: "`store_id` on every tenant-owned row"). It is queried directly by the Segment Size, Segment Distribution, and Customers in Multiple Segments KPI cards without joining through `customers`, so tenancy cannot be inferred reliably from a parent join alone.

---

## 13. marketing_campaigns

**Purpose:** Marketing campaign definitions. Referenced by message_sends for attribution.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated campaign ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `source_campaign_id` | `VARCHAR(255)` | NOT NULL | [S] | Original campaign ID from store's system |
| `campaign_type` | `VARCHAR(100)` | NOT NULL | [S] | Type of campaign |
| `channel` | `VARCHAR(50)` | NOT NULL | [S] | `email`, `whatsapp`, `sms`, `push` |
| `topic` | `VARCHAR(255)` | NULL | [S] | Campaign topic |
| `started_at` | `TIMESTAMPTZ` | NULL | [S] | Campaign start |
| `finished_at` | `TIMESTAMPTZ` | NULL | [S] | Campaign end |
| `total_count` | `INTEGER` | NULL | [S] | Total messages in campaign |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Record creation timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, source_campaign_id)` — one campaign per source ID per store

**Indexes:**
- `idx_marketing_campaigns_store_id` on `(store_id)`
- `idx_marketing_campaigns_channel` on `(channel)` — for "Campaigns by Channel" KPI
- `idx_marketing_campaigns_campaign_type` on `(campaign_type)` — for "Campaigns by Type" KPI

---

## 14. message_sends

**Purpose:** Individual message delivery and engagement tracking. Powers all marketing/channel KPIs.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated message ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `source_message_id` | `VARCHAR(255)` | NOT NULL | [S] | Original message ID from store's system |
| `campaign_id` | `UUID` | NOT NULL | [P] | FK → marketing_campaigns.id |
| `customer_id` | `UUID` | NOT NULL | [P] | FK → customers.id |
| `message_type` | `VARCHAR(50)` | NOT NULL | [S] | Type of message |
| `channel` | `VARCHAR(50)` | NOT NULL | [S] | `email`, `whatsapp`, `sms`, `push` |
| `channel_norm` | `VARCHAR(50)` | NOT NULL | [C] | Normalized channel: `email`, `sms`, `push` |
| `sent_at` | `TIMESTAMPTZ` | NOT NULL | [S] | When message was sent |
| `is_opened` | `BOOLEAN` | NOT NULL DEFAULT false | [S] | Whether message was opened |
| `is_clicked` | `BOOLEAN` | NOT NULL DEFAULT false | [S] | Whether message was clicked |
| `is_unsubscribed` | `BOOLEAN` | NOT NULL DEFAULT false | [S] | Whether user unsubscribed |
| `is_hard_bounced` | `BOOLEAN` | NOT NULL DEFAULT false | [S] | Hard bounce |
| `is_soft_bounced` | `BOOLEAN` | NOT NULL DEFAULT false | [S] | Soft bounce |
| `is_complained` | `BOOLEAN` | NOT NULL DEFAULT false | [S] | Marked as spam |
| `is_blocked` | `BOOLEAN` | NOT NULL DEFAULT false | [S] | Message blocked |
| `is_purchased` | `BOOLEAN` | NOT NULL DEFAULT false | [S] | Purchase attributed to this message |
| `purchased_at` | `TIMESTAMPTZ` | NULL | [S] | When the attributed purchase occurred |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Record creation timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE
- `campaign_id` → `marketing_campaigns.id` ON DELETE CASCADE
- `customer_id` → `customers.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, source_message_id)` — one message per source ID per store

**Indexes:**
- `idx_message_sends_store_id` on `(store_id)`
- `idx_message_sends_campaign_id` on `(campaign_id)`
- `idx_message_sends_customer_id` on `(customer_id)` — for customer-level engagement features
- `idx_message_sends_channel` on `(channel)` — for channel-level KPIs
- `idx_message_sends_channel_norm` on `(channel_norm)` — for normalized channel features
- `idx_message_sends_sent_at` on `(sent_at)` — for time-based queries
- `idx_message_sends_is_opened` on `(is_opened)` — for open rate KPIs
- `idx_message_sends_is_clicked` on `(is_clicked)` — for CTR KPIs
- `idx_message_sends_is_purchased` on `(is_purchased)` — for conversion KPIs

> **Channel normalization:** `web_push` and `mobile_push` are normalized to `push` in `channel_norm`, per the mapping table in segments-schema-mapping.md Segment 5.
>
> **Gap — Decision Required (#6):** that mapping table defines only `email`, `sms`, `web_push` and `mobile_push`. It has **no row for `whatsapp`**, yet WhatsApp is a first-class channel elsewhere in the valid documentation (kpi-schema-mapping.md lists it in 4 Channel Preference cards and segments-overview.md names it repeatedly). `channel_norm` and `marketing_campaigns.channel` therefore need a defined value set that includes `whatsapp`. Not guessed here.
>
> **Attribution — Decision Required (#2):** `is_purchased` and `purchased_at` carry the source's own purchase attribution for historical data. However, no attribution **window** is defined anywhere in the valid documentation, and kpi-schema-mapping.md lists "attribution window" as a required input for the Revenue Per Recipient card. See Open Decisions #2.

---

## 15. coupon_campaigns

**Purpose:** Coupon campaign definitions. Referenced by coupon_redemptions.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated coupon campaign ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `source_campaign_id` | `VARCHAR(255)` | NOT NULL | [S] | Original campaign ID from store's system |
| `campaign_type` | `VARCHAR(100)` | NOT NULL | [S] | Coupon campaign type |
| `start_date` | `DATE` | NOT NULL | [S] | Campaign start date |
| `end_date` | `DATE` | NOT NULL | [S] | Campaign end date |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Record creation timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, source_campaign_id)` — one coupon campaign per source ID per store

**Indexes:**
- `idx_coupon_campaigns_store_id` on `(store_id)`
- `idx_coupon_campaigns_campaign_type` on `(campaign_type)` — for "Coupon Campaigns by Type" KPI

---

## 16. coupon_redemptions

**Purpose:** Individual coupon redemption records. Powers coupon KPIs and discount responsiveness segment.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated redemption ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id |
| `coupon_id` | `VARCHAR(255)` | NOT NULL | [S] | Coupon identifier |
| `campaign_id` | `UUID` | NOT NULL | [P] | FK → coupon_campaigns.id |
| `customer_id` | `UUID` | NOT NULL | [P] | FK → customers.id |
| `redemption_status` | `BOOLEAN` | NOT NULL | [S] | Whether coupon was redeemed (true/false) |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | [P] | Record creation timestamp |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE
- `campaign_id` → `coupon_campaigns.id` ON DELETE CASCADE
- `customer_id` → `customers.id` ON DELETE CASCADE

**Indexes:**
- `idx_coupon_redemptions_store_id` on `(store_id)`
- `idx_coupon_redemptions_campaign_id` on `(campaign_id)` — for "Coupon Redemptions by Campaign" KPI
- `idx_coupon_redemptions_customer_id` on `(customer_id)` — for "Unique Customers Using Coupons" KPI
- `idx_coupon_redemptions_redemption_status` on `(redemption_status)` — for "Coupon Redemption Rate" KPI
- `idx_coupon_redemptions_coupon_id` on `(coupon_id)` — for "Total Coupon Redemptions" KPI

> **Note:** No redemption timestamp column is defined. The source file (`train.csv`) contains only `id`, `campaign_id`, `coupon_id`, `customer_id` and `redemption_status` — it carries no redemption date. No KPI card requires one.

---

## 17. holidays

**Purpose:** Holiday calendar for seasonal analysis and send-time optimization.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Holiday ID (PK) |
| `date` | `DATE` | NOT NULL | [S] | Holiday date |
| `holiday` | `VARCHAR(255)` | NOT NULL | [S] | Holiday name |

**Primary Key:** `id`

**Unique Constraints:**
- `(date)` — one holiday per date

**Indexes:**
- `idx_holidays_date` on `(date)` — for date-based lookups

> **Note:** Source is `holidays.csv` from the direct-messaging dataset, which contains exactly two columns: `date` and `holiday` (see `segments/Channel Preference.md`). No country/locale column exists in the source, so none is defined here.
>
> This is a shared reference table, not tenant-specific, so it carries no `store_id`.
>
> **Observation (no action required):** the Holiday KPI category (3 cards) was dropped in kpi-schema-mapping.md ("not feasible with current data"), and `days_until_holiday` was explicitly ruled out for the Seasonal Purchase segment. No current KPI card reads this table. It is retained because schema-blueprint.md Group 4 lists "holiday calendars" and segments-schema-mapping.md Segment 5 lists `holidays` as a new table needed.

---

## 18. causal_data

**Purpose:** Product × store × week promotion flags from Dunnhumby. Used for seasonal purchase segment's promo response features.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Platform-generated ID (PK) |
| `store_id` | `UUID` | NOT NULL | [P] | FK → stores.id (tenant isolation) |
| `product_id` | `UUID` | NOT NULL | [P] | FK → products.id |
| `source_store_id` | `INT` | NOT NULL | [S] | Source `STORE_ID` — **name Decision Required, see #3** |
| `week_no` | `SMALLINT` | NOT NULL | [S] | Native week number (1–104) |
| `display` | `VARCHAR(50)` | NULL | [S] | Display promotion flag |
| `mailer` | `VARCHAR(50)` | NULL | [S] | Mailer promotion flag |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE
- `product_id` → `products.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, source_store_id, product_id, week_no)` — one record per product per source store per week

**Indexes:**
- `idx_causal_data_store_id` on `(store_id)`
- `idx_causal_data_product_id` on `(product_id)`
- `idx_causal_data_source_store_id` on `(source_store_id)`
- `idx_causal_data_week_no` on `(week_no)`

> **Why this table exists:** the Causal/Promotion KPI category (4 cards) was dropped in kpi-schema-mapping.md, so no KPI card reads this table directly. It is retained because segments-schema-mapping.md Segment 8 defines it as a new table needed, and because `customer_features.promo_response_rate` — which is required by the **"Seasonal Purchase Propensity"** card in kpi-schema-mapping.md — is computed by joining `orders` → `causal_data` on (`product_id`, `source_store_id`, `week_no`). Without this table that KPI card cannot be served.
>
> **Join key:** segments-schema-mapping.md Segment 8 states all three key columns are native source values and join with "no re-mapping".

---

## 19. kpi_snapshots

**Purpose:** Pre-computed KPI values for fast dashboard loading. Populated by scheduled jobs.

| Column | Type | Nullable | Provenance | Description |
|--------|------|----------|------------|-------------|
| `id` | `UUID` | NOT NULL | [P] | Snapshot ID (PK) |
| `store_id` | `UUID` | NULL | [P] | FK → stores.id. **NULL only for Platform Growth KPIs** (platform-level totals) |
| `kpi_name` | `VARCHAR(100)` | NOT NULL | [P] | KPI identifier (e.g., "gross_revenue", "net_revenue") |
| `kpi_category` | `VARCHAR(50)` | NOT NULL | [P] | Category: `revenue`, `customer`, `order`, `product`, `marketing`, `coupon`, `event`, `segment`, `channel_preference`, `platform_growth` |
| `period_type` | `VARCHAR(20)` | NOT NULL | [P] | `daily`, `weekly`, `monthly`, `total` |
| `period_start` | `DATE` | NULL | [P] | Start of period (NULL for `total`) |
| `period_end` | `DATE` | NULL | [P] | End of period (NULL for `total`) |
| `dimension` | `VARCHAR(100)` | NULL | [P] | Breakdown dimension (e.g., `category`, `brand`, `channel`, `segment`) |
| `dimension_value` | `VARCHAR(255)` | NULL | [P] | Value of the dimension (e.g., `Electronics`, `email`) |
| `value` | `NUMERIC(18,4)` | NOT NULL | [C] | Computed KPI value |
| `computed_at` | `TIMESTAMPTZ` | NOT NULL | [P] | When the snapshot was computed |

**Primary Key:** `id`

**Foreign Keys:**
- `store_id` → `stores.id` ON DELETE CASCADE

**Unique Constraints:**
- `(store_id, kpi_name, period_type, period_start, dimension, dimension_value)` — one value per KPI per period per dimension

**Indexes:**
- `idx_kpi_snapshots_store_id` on `(store_id)`
- `idx_kpi_snapshots_kpi_name` on `(kpi_name)`
- `idx_kpi_snapshots_kpi_category` on `(kpi_category)`
- `idx_kpi_snapshots_computed_at` on `(computed_at)` — for incremental refresh

> **Note:** Platform-level KPIs (Platform Growth category) have `store_id = NULL`. Store-level KPIs always have a non-NULL `store_id`. This is why `store_id` is nullable.
>
> **Implementation caveat:** in PostgreSQL a plain `UNIQUE` constraint does not treat `NULL` as equal, so platform-level rows (`store_id IS NULL`) will not be deduplicated by the constraint above. Requires either a partial unique index (`WHERE store_id IS NULL`) or `NULLS NOT DISTINCT` (PostgreSQL 15+). Recorded in Open Decisions #7.

---

## Entity Relationship Summary

```
stores (1) ──< store_users
stores (1) ──< ingestion_uploads (1) ──< ingestion_column_mappings
stores (1) ──< customers (1) ──< orders (1) ──< order_items
stores (1) ──< products (1) ──< order_items
stores (1) ──< products (1) ──< events
stores (1) ──< customers (1) ──< events
stores (1) ──< customers (1) ──< customer_features
stores (1) ──< customers (1) ──< customer_segments
stores (1) ──< segments (1) ──< customer_segments
stores (1) ──< marketing_campaigns (1) ──< message_sends
stores (1) ──< customers (1) ──< message_sends
stores (1) ──< coupon_campaigns (1) ──< coupon_redemptions
stores (1) ──< customers (1) ──< coupon_redemptions
stores (1) ──< products (1) ──< causal_data
stores (1) ──< kpi_snapshots
holidays (shared reference, not tenant-specific)
```

---

## KPI Coverage

This section verifies that the proposed schema contains all data required by the 115 KPI cards defined in kpi-schema-mapping.md.

### Platform Growth (5 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Total Stores | `stores.id` | COVERED |
| Stores by Plan | `stores.plan` | COVERED |
| New Stores (period) | `stores.created_at` | COVERED |
| Total Store Users | `store_users.id` | COVERED |
| Users by Role | `store_users.role` | COVERED |

### Revenue (17 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Gross Revenue | `order_items.sales_value` | COVERED |
| Net Revenue | `order_items.sales_value`, `retail_disc`, `coupon_disc`, `coupon_match_disc`, `other_discount` | COVERED |
| Revenue by Period | `orders.order_date`, `order_items.sales_value` | COVERED |
| Revenue by Channel | `message_sends.channel`, `is_purchased`, `order_items.sales_value` | COVERED |
| Revenue by Category | `products.category`, `order_items.sales_value` | COVERED |
| Revenue by Segment | `customer_segments.segment_id`, `order_items.sales_value` | COVERED |
| Average Order Value | `order_items.sales_value`, `orders.source_order_id` | COVERED |
| Revenue per Customer | `order_items.sales_value`, `orders.customer_id` | COVERED |
| Revenue Growth Rate | `order_items.sales_value`, `orders.order_date` | COVERED |
| Total Discount Amount | `order_items.retail_disc`, `coupon_disc`, `coupon_match_disc`, `other_discount` | COVERED |
| Discount Rate | Same + `sales_value` | COVERED |
| Revenue per Item Sold | `order_items.sales_value`, `quantity` | COVERED |
| Revenue by Brand | `products.brand`, `order_items.sales_value` | COVERED |
| Revenue by Department | `products.department`, `order_items.sales_value` | COVERED |
| Revenue by Region | `orders.region`, `order_items.sales_value` | COVERED |
| Revenue by Week | `orders.week_no`, `order_items.sales_value` | COVERED |
| Historical CLV (Actual) | `order_items.sales_value`, `orders.customer_id` | COVERED |

### Customer (20 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Total Customers | `customers.source_customer_id` | COVERED |
| New Customers | `customers.first_purchase_date` | COVERED |
| Active Customers (30d) | `customers.days_since_last_order` | COVERED |
| Customer Retention Rate | `customers.first_purchase_date`, `orders.order_date` | COVERED |
| Customer Churn Rate | `customers.churn` | COVERED |
| Customer Lifetime Value (CLV) | `customer_features.recency`, `frequency`, `monetary`, etc. | COVERED |
| Repeat Purchase Rate | `customer_features.frequency` | COVERED |
| Avg Days Between Purchases | `customer_features.purchase_regularity` | COVERED |
| Customer Satisfaction (CSAT) | `customers.satisfaction_score` | COVERED |
| Complaint Rate | `customers.complain` | COVERED |
| Average Tenure | `customers.tenure` | COVERED |
| Avg Days Since Last Order | `customers.days_since_last_order` | COVERED |
| Average Cashback Amount | `customers.cashback_amount` | COVERED |
| Churn Rate by Category | `customers.churn`, `preferred_order_category` | COVERED |
| One-time Buyer Rate | `customer_features.frequency` | COVERED |
| High-Value Customer Rate | `customer_features.monetary` | COVERED |
| Customer Growth Rate | `customers.first_purchase_date` | COVERED |
| Avg Warehouse-to-Home Distance | `customers.warehouse_to_home` | COVERED |
| Avg Devices Registered | `customers.number_of_devices_registered` | COVERED |
| Time to First Purchase | `customers.first_purchase_date` | COVERED |

### Order (10 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Total Orders | `orders.source_order_id` | COVERED |
| Orders per Customer | `orders.source_order_id`, `orders.customer_id` | COVERED |
| View to Cart Rate | `customer_features.view_count`, `cart_count` | COVERED |
| Cart to Purchase Rate | `customer_features.purchase_count`, `cart_count` | COVERED |
| Cart Abandonment Rate | `customer_features.cart_count`, `purchase_count`, `remove_from_cart_count` | COVERED |
| Avg Items per Order | `order_items.quantity`, `orders.source_order_id` | COVERED |
| Orders by Region | `orders.region` | COVERED |
| Order Growth Rate | `orders.order_date` | COVERED |
| Order Peak Hour | `orders.order_timestamp` | COVERED |
| Session to Purchase Conversion | `customer_features.purchase_count`, `session_count` | COVERED |

### Product (11 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Top Products by Revenue | `order_items.product_id`, `sales_value` | COVERED |
| Product Revenue | `order_items.product_id`, `sales_value` | COVERED |
| Cross-sell Rate | `order_items.product_id`, `products.category` | COVERED |
| Replenishment Rate | `customer_features.days_since_last_purchase`, `purchase_frequency` | COVERED |
| Seasonal Concentration | `customer_features.seasonal_concentration` | COVERED |
| Total Products | `products.source_product_id` | COVERED |
| Products by Category | `products.category` | COVERED |
| Products by Brand | `products.brand` | COVERED |
| Average Product Price | `products.unit_price` | COVERED |
| Top Products by Quantity | `order_items.product_id`, `quantity` | COVERED |
| Product Penetration | `order_items.product_id`, `orders.customer_id` | COVERED |

### Marketing/Channel (21 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Email Open Rate | `message_sends.is_opened`, `channel` | COVERED |
| Push Open Rate | `message_sends.is_opened`, `channel` | COVERED |
| SMS Open Rate | `message_sends.is_opened`, `channel` | COVERED |
| Bounce Rate | `message_sends.is_hard_bounced`, `is_soft_bounced` | COVERED |
| Unsubscribe Rate | `message_sends.is_unsubscribed` | COVERED |
| Campaign Conversion Rate | `message_sends.is_purchased` | COVERED |
| Coupon Redemption Rate | `coupon_redemptions.redemption_status` | COVERED |
| Discount Dependency Ratio | `order_items.retail_disc`, `coupon_disc`, `coupon_match_disc`, `other_discount`, `sales_value` | COVERED |
| Total Messages Sent | `message_sends.source_message_id` | COVERED |
| Messages by Channel | `message_sends.channel` | COVERED |
| Messages by Type | `message_sends.message_type` | COVERED |
| Message Complaint Rate | `message_sends.is_complained` | COVERED |
| Message Block Rate | `message_sends.is_blocked` | COVERED |
| Click-Through Rate (CTR) | `message_sends.is_clicked` | COVERED |
| Message Frequency per Customer | `message_sends.customer_id` | COVERED |
| Total Marketing Campaigns | `marketing_campaigns.source_campaign_id` | COVERED |
| Campaigns by Channel | `marketing_campaigns.channel` | COVERED |
| Campaigns by Type | `marketing_campaigns.campaign_type` | COVERED |
| Channel Performance Summary | `message_sends.*`, `order_items.sales_value` | COVERED |
| Revenue Per Recipient (RPR) | `message_sends.*`, `order_items.sales_value`, attribution window | **DECISION REQUIRED** — see Open Decisions #2 |
| Top Performing Automated Flows | `marketing_campaigns.*`, `message_sends.is_purchased`, `order_items.sales_value` | COVERED |

### Coupon (6 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Total Coupon Campaigns | `coupon_campaigns.source_campaign_id` | COVERED |
| Coupon Campaigns by Type | `coupon_campaigns.campaign_type` | COVERED |
| Total Coupon Redemptions | `coupon_redemptions.coupon_id` | COVERED |
| Coupon Redemptions by Campaign | `coupon_redemptions.campaign_id` | COVERED |
| Unique Customers Using Coupons | `coupon_redemptions.customer_id` | COVERED |
| Coupon Campaign Duration | `coupon_campaigns.start_date`, `end_date` | COVERED |

### Event/Behavior (8 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Total Events | `events.event_type` | COVERED |
| Total Sessions | `events.session_id` | COVERED |
| Events per Session | `events.session_id`, `event_type` | COVERED |
| Unique Products Viewed | `events.product_id`, `event_type` | COVERED |
| Unique Categories Viewed | `events.category_id`, `event_type` | COVERED |
| Average Viewed Price | `events.price`, `event_type` | COVERED |
| Category View Distribution | `events.category_code` | COVERED |
| Brand View Distribution | `events.brand` | COVERED |

### Segment ML Scores (13 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Purchase Intent Score | `customer_features.view_count`, `cart_count`, `view_to_cart_rate`, etc. | COVERED |
| Future High-Value Score | `customer_features.recency`, `frequency`, `monetary`, etc. | COVERED |
| Discount Responsiveness Score | `customer_features.total_discount_received`, `discount_dependency_ratio`, etc. | COVERED |
| Churn Risk Score | `customer_features.tenure`, `warehouse_to_home`, etc. | COVERED |
| Channel Preference | `customer_features.email_open_rate`, `email_click_rate`, etc. | COVERED |
| Replenishment Readiness | `customer_features.days_since_last_purchase`, `purchase_frequency`, etc. | COVERED |
| Cross-sell Affinity Score | `customer_features.basket_size`, `category_diversity`, etc. | COVERED |
| Seasonal Purchase Propensity | `customer_features.purchase_by_week`, `seasonal_concentration`, etc. | COVERED |
| Cart Abandonment Risk | `customer_features.cart_count`, `purchase_count`, `cart_abandonment_rate`, etc. | COVERED |
| Segment Size | `customer_segments.segment_id` | COVERED |
| Segment Distribution | `customer_segments` | COVERED |
| Average Segment Score | `customer_segments.score` | COVERED |
| Customers in Multiple Segments | `customer_segments` | COVERED |

### Channel Preference (4 cards) — COVERED

| KPI | Required Data | Status |
|-----|--------------|--------|
| Preferred Channel | `message_sends.channel`, `is_opened` | COVERED |
| Channel Open Rate by Customer | `message_sends.is_opened`, `channel` | COVERED |
| Channel Click Rate by Customer | `message_sends.is_clicked`, `channel` | COVERED |
| Channel Purchase Rate by Customer | `message_sends.is_purchased`, `channel` | COVERED |

### Summary

| Category | Cards | Covered | Decision Required |
|----------|-------|---------|-------------------|
| Platform Growth | 5 | 5 | 0 |
| Revenue | 17 | 17 | 0 |
| Customer | 20 | 20 | 0 |
| Order | 10 | 10 | 0 |
| Product | 11 | 11 | 0 |
| Marketing/Channel | 21 | 20 | 1 |
| Coupon | 6 | 6 | 0 |
| Event/Behavior | 8 | 8 | 0 |
| Segment ML Scores | 13 | 13 | 0 |
| Channel Preference | 4 | 4 | 0 |
| **Total** | **115** | **114** | **1** |

**All 115 KPI cards have a corresponding column in this schema.** The 1 outstanding item is the Revenue Per Recipient attribution window, which is a *parameter* the documentation never defines rather than a missing column — see Open Decisions #2.

**Columns that are covered but whose upstream source is undetermined** (column exists, ingestion source is not documented anywhere): `orders.region` (#4), `orders.order_timestamp` (#5), `products.unit_price` (#11). These are marked in their table sections and in Open Decisions.

**Feature-level conflicts that do not block a KPI card** but need resolution: the duplicate feature pairs (#12) and the customer × product grain conflict (#14) in `customer_features`.

---

## Open Decisions

Everything in this section is **undetermined by the valid project documentation** (schema-blueprint.md, segments-schema-mapping.md, the 9 segment files, kpi-schema-mapping.md, Overview.md). No column has been added, removed or renamed to resolve any of these.

Each item states the conflicting evidence so the decision can be made without re-reading the source notes.

### 1. Churn model input allowlist (leakage enforcement)

**Resolved in schema:** `customers.churn` is stored, because segments-schema-mapping.md Segment 4 documents `Churn | customers | churn | LABEL` and 2 KPI cards require it.

**Still undetermined:** how the platform *enforces* that `churn` (and possibly `days_since_last_order`) is excluded from model inputs. segments-schema-mapping.md only says:

> "`DaySinceLastOrder` retained for analysis — high target leakage risk (churn likely defined by this feature). Analyze leakage before training; may need exclusion from production model."

Overview.md states segments are "config-driven, not hardcoded" and gated by a `required_fields` schema. Whether that config can also express an *exclusion* list is a design question.

**Decision Required:** how to enforce the exclusion — a training-pipeline allowlist, a schema-level flag, or manual discipline.

---

### 2. Revenue Per Recipient attribution window

**Undetermined.** The RPR card lists `attribution window` as a required input, but no valid document defines a value or a unit. Historical attribution is available for free via `message_sends.is_purchased` + `purchased_at`; nothing is documented for live-generated messages.

**Decision Required:** attribution window value, and whether it is a global constant, per-campaign, or per-channel. If per-campaign, `marketing_campaigns` needs a new column.

---

### 3. `store_id` name collision (tenant FK vs source `STORE_ID`)

**Conflicting evidence:**

| Document | Says |
|---|---|
| schema-blueprint.md principle 1 | `store_id` = the tenant key, "on every tenant-owned row" |
| segments-schema-mapping.md Segment 6 | `STORE_ID | orders | store_id` |
| segments-schema-mapping.md Segment 8 | `store_id (INT)` on `causal_data`; joins `orders` → `causal_data` on `store_id = STORE_ID` |

The same identifier name is used for two different things. `causal_data` is the worst case, because it needs **both**: a tenant FK and the source store key, and the documented name for the source key is also `store_id`.

**Applied in this document (as a proposal, not a decision):** tenant column stays `store_id UUID`; the source key is shown as `source_store_id INT`, following the existing `source_customer_id` / `source_order_id` / `source_product_id` convention (principle 4).

**Decision Required:** confirm the rename to `source_store_id`, or define an alternative. Until then `causal_data` carries an extra column that the documentation does not authorise.

---

### 4. `orders.region` data source

**Conflicting evidence:**

| Document | Says |
|---|---|
| kpi-schema-mapping.md | Revenue by Region and Orders by Region both require `orders.region` |
| segments-schema-mapping.md Segment 2 | "`Country` — Not needed for this segment (no segment/KPI uses geo)" |

No segment mapping assigns any source column to `orders.region`. The only geographic field in any documented dataset is `Country` (Online Retail II), and that document explicitly rejects it. The column is retained (2 KPI cards need it) with provenance marked **Decision Required**.

**Decision Required:** the source of region data, or whether `Country` should be promoted to `orders.region` — which would reverse the Segment 2 exclusion.

---

### 5. `orders.order_timestamp` source

**Partially undetermined.** The "Order Peak Hour" card requires `orders.order_timestamp`, but no segment mapping assigns a source column to it — only `order_date` is ever mapped. Per the segment files:

| Source column | Type documented |
|---|---|
| `InvoiceDate` (Online Retail II) | "Invoice date **and time**" |
| `event_time` (multi-category events) | "Time when event happened (in UTC)" |
| `DAY` + `TRANS_TIME` (Dunnhumby) | day number + time of day |
| `date` (coupon dataset) | "Date of Transaction" — **date only** |

So the column is `NULL`-able by design, but whether ingestion should synthesise a timestamp for the date-only source is undetermined. It is deliberately not defaulted to midnight, which would fabricate precision.

**Decision Required:** behaviour when no time component exists — leave NULL, or exclude the KPI for that store.

---

### 6. Channel value sets (`whatsapp` missing)

**Conflicting evidence:**

| Document | Says |
|---|---|
| segments-schema-mapping.md Segment 5 | Normalisation table defines only `email`, `sms`, `web_push`, `mobile_push` |
| kpi-schema-mapping.md | Channel Preference cards cover Email, WhatsApp, SMS, Push |
| segments-overview.md | Channels repeatedly listed as "Email + Push + WhatsApp/SMS" |
| Overview.md | Campaign channel-select is "WhatsApp / SMS / email only" — no push |

`whatsapp` appears in 4 KPI cards but has no row in the normalisation table. Push appears as a normalised value but is not in Overview.md's channel selector.

**Decision Required:** the permitted value set for `marketing_campaigns.channel` and `message_sends.channel` / `channel_norm`, and the `whatsapp` normalisation rule.

---

### 7. `kpi_snapshots` uniqueness with nullable `store_id`

**Technical, not documented.** `store_id` must be NULL for the 5 Platform Growth cards, but PostgreSQL's default `UNIQUE` treats NULLs as distinct, so platform-level rows are not deduplicated by the declared constraint.

**Decision Required:** partial unique index (`WHERE store_id IS NULL`) vs `UNIQUE NULLS NOT DISTINCT` (PostgreSQL 15+), and the minimum PostgreSQL version for the project.

---

### 8. `orders.source_order_id` nullability

**Undetermined.** Documented source mappings exist for only 2 of 6 datasets:

| Dataset | Mapped to `source_order_id`? |
|---|---|
| Online Retail II | Yes — `Invoice` (Segment 2) |
| Dunnhumby | Yes — `BASKET_ID` (Segment 6) |
| Predicting Coupon Redemption | **No** — Segment 3 maps `date → order_date` only, no order identifier |
| Multi-category events | **No** — Segments 1 and 9 map to `events` only, no `orders` mapping exists |

Whether those datasets produce rows in `orders` at all, and if so what `source_order_id` holds, is not documented in the valid notes.

**Decision Required:** whether `source_order_id` can remain `NOT NULL`, or must become nullable.

---

### 9. `order_items.sales_value` vs `net_sales_value`

**Internal inconsistency in the valid documentation.** segments-schema-mapping.md uses both names:

- Segment 3: `selling_price | order_items | net_sales_value`
- Segment 3 feature formula: `discount_dependency_ratio` reads `order_items.net_sales_value`
- Segment 6: `SALES_VALUE | order_items | sales_value`
- kpi-schema-mapping.md: all 17 Revenue cards read `order_items.sales_value`

`sales_value` is used here (majority usage, and the KPI requirements document's name). Segment 3 and one feature formula still use `net_sales_value`.

**Decision Required:** which name is canonical; the other document needs aligning.

---

### 10. `selling_price` net vs. gross (requires dataset inspection)

**Explicitly flagged as unverified in the valid documentation.** segments-schema-mapping.md Segment 3:

> "**Verification needed:** Confirm whether `selling_price` is net (post-discount) or gross. If gross, `discount_dependency_ratio` will underestimate dependency. … If `selling_price` is gross, use `selling_price + other_discount + coupon_discount` as `net_sales_value` in all formulas."

Affects `order_items.sales_value` values, `total_discount_received`, `discount_dependency_ratio`, `avg_discount_per_order`, Discount Rate and Total Discount Amount.

**Verification target:** `customer_transaction_data.csv` in the Predicting Coupon Redemption dataset — columns `selling_price`, `other_discount`, `coupon_discount`. Suggested test: check whether `selling_price` is constant per `item_id` (constant ⇒ gross/list price; varying ⇒ already net). Does **not** change the column list either way.

---

### 11. `products.unit_price` source

**Undetermined.** The "Average Product Price" card references `products.unit_price`, but no segment mapping assigns a source column to it. In all documented datasets price is transaction-level, not catalog-level. Column retained because the KPI requires it.

**Decision Required:** is it a stored catalog price, or derived from `order_items`?

---

### 12. Duplicate `customer_features` columns

**Conflicts with schema-blueprint.md principle 2** ("No redundant columns"). Three column pairs have identical documented formulas under two names, and **both names are referenced by kpi-schema-mapping.md**, so neither can be dropped without breaking a documented requirement:

| Duplicate pair | Documented formula | Name referenced by |
|---|---|---|
| `cart_removal_count` / `remove_from_cart_count` | `COUNT(*) WHERE event_type='remove_from_cart'` | Purchase Intent Score card 1; Cart Abandonment Rate card 5 |
| `cart_removal_rate` / `remove_from_cart_rate` | numerator ÷ `cart_count` | as above |
| `unique_products_bought` / `product_loyalty` | `COUNT(DISTINCT product_id)` per customer | CLV card 6; Replenishment Readiness card 6 |

All duplicates are retained and annotated in the `customer_features` section.

**Decision Required:** which name wins, and whether the KPI mapping is allowed to be amended to drop the other.

---

### 13. `customer_features` refresh mechanism

**Undetermined.** kpi-schema-mapping.md requires metrics to *"calculate dynamically or refresh on a rolling 7-day schedule"* and requires immediate display on first upload, but does not state whether recomputation is full or incremental. `computed_at` is defined for staleness detection either way.

**Decision Required:** full recompute vs. incremental. Noting only that `purchase_regularity`, `purchase_by_week` and `seasonal_concentration` all depend on full history.

---

### 14. `customer_features` grain vs. Segment 6 features

**Structural conflict.** `customer_features` is one row per customer, but segments-schema-mapping.md Segment 6 defines three features at customer × product grain:

| Feature | Documented grain |
|---|---|
| `days_since_last_purchase` | "SQL per customer+product" |
| `purchase_frequency` | "SQL per customer+product" |
| `avg_days_between_purchases` | "SQL window function per customer+product" |

kpi-schema-mapping.md references them as single per-customer values. A one-row-per-customer table can only hold an aggregate, and which aggregate is intended is undetermined.

**Decision Required:** which aggregate (e.g. minimum across products, or average), or whether a separate customer × product table is required. No new table added here.

---

### 15. `tracked_links` table requested by the KPI mapping

**Omission to flag.** kpi-schema-mapping.md contains an explicit developer instruction:

> "To track Email, SMS, and WhatsApp CTRs without an SDK, update your database schema to include a `tracked_links` table mapping unique short-codes to destination URLs. Then, develop a simple backend endpoint (e.g., `/click/:code`) that logs the click timestamp and executes an HTTP 302 redirect."

No such table is defined in this schema. CTR is currently served from the source-provided `message_sends.is_clicked`, which is sufficient for ingested historical data but not for messages the platform sends itself.

**Decision Required:** add `tracked_links` (+ a click-event table) to the schema, or record that click tracking for platform-sent messages is out of scope. Not added, because adding tables is a design decision.

---

### 16. `customer_kpis` vs `kpi_snapshots` naming

**Minor inconsistency.** Overview.md states KPI results are saved to a table named `customer_kpis`. schema-blueprint.md Group 6 describes "Pre-computed KPI tables" without naming one. This document uses `kpi_snapshots`.

**Decision Required:** confirm the table name and align Overview.md.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-09-28 | Initial proposed final schema — 19 tables, 115 KPI coverage analysis, 10 open decisions |
| 2026-09-28 | **Review pass.** Corrected against valid documentation only (`data-transformation-plan.md` excluded). Removed 2 invented columns (`holidays.country`, `coupon_redemptions.redeemed_at`); added 2 documented source columns (`customers.churn`, `message_sends.purchased_at`) plus 1 index; added `store_id` to `customer_segments` for tenant isolation; fixed `kpi_snapshots.store_id` nullable contradiction; resolved `sales_value` naming to the KPI-document name; annotated 3 duplicate `customer_features` pairs rather than deleting KPI-referenced columns; split the `store_id` tenant/source collision into a distinct `source_store_id` column in `orders` and `causal_data`; recorded `products.unit_price` as source-undetermined. KPI coverage now 114/115 with 1 parameter-only gap. Open Decisions expanded 10 → 16 and rewritten with cited conflicting evidence. |

