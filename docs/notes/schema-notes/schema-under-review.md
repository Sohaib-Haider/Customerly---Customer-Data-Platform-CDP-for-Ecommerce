# CDP — Finalized Schema (Before Events)

Status: This file is under review, do not have comparison of this file with existing files for now
There may have solid issued tat needs to be resolved
Last Updated: 2026-09-27

---

## GROUP 1: PLATFORM CORE

### `stores`
Tenant accounts — the ecommerce stores that sign up to use the platform.

| Column | Type | Description |
|---|---|---|
| id | UUID PK | Internal unique identifier |
| name | VARCHAR(255) | Store name |
| owner_name | VARCHAR(255) | Name of the store owner |
| email | VARCHAR(255) UNIQUE | Login email |
| password_hash | VARCHAR(255) | Hashed password |
| phone | VARCHAR(50) | Contact phone number |
| whatsapp | VARCHAR(50) | WhatsApp number |
| plan | VARCHAR(50) | Subscription plan: free / pro / enterprise |
| created_at | TIMESTAMP | When the store account was created |

### `store_users`
Team members who can login to a store (owner + staff).

| Column | Type | Description |
|---|---|---|
| id | UUID PK | Internal unique identifier |
| store_id | UUID FK → stores.id | Which store this user belongs to |
| name | VARCHAR(255) | User's full name |
| email | VARCHAR(255) | Login email |
| password_hash | VARCHAR(255) | Hashed password |
| role | VARCHAR(50) | Role: owner / admin / member |
| created_at | TIMESTAMP | When the user was added |

---

## GROUP 2: DATA INGESTION

### `data_uploads`
Tracks each file upload by a store.

| Column | Type | Description |
|---|---|---|
| id | UUID PK | Internal unique identifier |
| store_id | UUID FK → stores.id | Which store uploaded the file |
| file_name | VARCHAR(255) | Original filename |
| file_type | VARCHAR(50) | File type: csv / postgres |
| file_size | INTEGER | File size in bytes |
| row_count | INTEGER | Number of rows in the file |
| status | VARCHAR(50) | Upload status: pending / mapping / mapping_done / imported / error |
| uploaded_by | UUID FK → store_users.id | Which user uploaded the file |
| uploaded_at | TIMESTAMP | When the file was uploaded |

### `column_mappings`
Stores the LLM mapping results — how source columns map to our canonical schema.

| Column | Type | Description |
|---|---|---|
| id | UUID PK | Internal unique identifier |
| store_id | UUID FK → stores.id | Which store this mapping belongs to |
| upload_id | UUID FK → data_uploads.id | Which upload this mapping is for |
| source_column | VARCHAR(255) | Original column name from the store's data |
| target_table | VARCHAR(255) | Our canonical table name |
| target_column | VARCHAR(255) | Our canonical column name |
| confidence | FLOAT | LLM mapping confidence score (0-1) |

---

## GROUP 3: CANONICAL CUSTOMER DATA

### Table 1: `customers`
One row per customer. Only attributes that serve our 9 segments and KPI cards.

| Column | Type | Description |
|---|---|---|
| id | UUID PK | Internal unique identifier |
| store_id | UUID FK → stores.id | Which store owns this customer (multi-tenant key) |
| source | VARCHAR(50) | Which dataset/upload this customer came from |
| source_customer_id | VARCHAR(255) | Original customer ID from the store's data |
| tenure | INTEGER | How long they've been a customer (days) — Churn-Risk feature |
| preferred_order_category | VARCHAR(100) | Their favorite product category — Churn-Risk feature |
| satisfaction_score | INTEGER | Satisfaction rating 1-5 — Churn-Risk feature + KPI card |
| complain | BOOLEAN | Has the customer complained — Churn-Risk feature + KPI card |
| days_since_last_order | INTEGER | Days since the customer's last order — Churn-Risk feature + KPI card |
| cashback_amount | DECIMAL(10,2) | Cashback earned — Churn-Risk feature |
| churn | BOOLEAN | Churned (yes/no) — Churn-Risk label |
| first_purchase_date | DATE | When the customer first purchased — Channel Preference + KPI card |

### Table 2: `products`
One row per product. Only attributes that serve our 9 segments and KPI cards.

| Column | Type | Description |
|---|---|---|
| id | UUID PK | Internal unique identifier |
| store_id | UUID FK → stores.id | Which store owns this product (multi-tenant key) |
| source | VARCHAR(50) | Which dataset/upload this product came from |
| source_product_id | VARCHAR(255) | Original product ID from the store's data |
| name | VARCHAR(255) | Product name — Cross-Sell, KPIs |
| brand | VARCHAR(255) | Product brand — Purchase Intent, Cross-Sell |
| category | VARCHAR(100) | Product category — Cross-Sell, KPIs |
| department | VARCHAR(50) | Department (e.g. Dairy, Bakery) — Cross-Sell, Seasonal |
| commodity_desc | VARCHAR(255) | Product commodity description — Cross-Sell |
| unit_price | DECIMAL(10,2) | Product unit price — KPIs, CLV |

### Table 3: `orders`
One row per order (the receipt). Order-level info only, no financials.

| Column | Type | Description |
|---|---|---|
| id | UUID PK | Internal unique identifier |
| store_id | UUID FK → stores.id | Which store owns this order (multi-tenant key) |
| source | VARCHAR(50) | Which dataset/upload this order came from |
| source_order_id | VARCHAR(255) | Original order ID from the store's data |
| customer_id | UUID FK → customers.id | Which customer placed this order |
| order_date | DATE | Date of the order — CLV, Replenishment, Seasonal, KPIs |
| order_timestamp | TIMESTAMP | Exact timestamp of the order — Purchase Intent, Cart Abandoners |
| region | VARCHAR(50) | Store region — Geo KPIs |
| department | VARCHAR(50) | Department — Cross-Sell, Seasonal |

### Table 4: `order_items`
One row per product within an order (each line on the receipt). All financials live here.

| Column | Type | Description |
|---|---|---|
| id | UUID PK | Internal unique identifier |
| store_id | UUID FK → stores.id | Which store owns this order item (multi-tenant key) |
| source | VARCHAR(50) | Which dataset/upload this order item came from |
| order_id | UUID FK → orders.id | Which order this item belongs to |
| product_id | UUID FK → products.id | Which product this item is |
| quantity | INTEGER | Quantity of this product — CLV, KPIs |
| unit_price | DECIMAL(10,2) | Price per unit of this product — KPIs |
| selling_price | DECIMAL(10,2) | Selling price — CLV, KPIs |
| sales_value | DECIMAL(10,2) | Total value (quantity x unit_price) — Replenishment, Cross-Sell |
| retail_disc | DECIMAL(10,2) | Retail discount amount — Discount Responsive, KPIs |
| coupon_disc | DECIMAL(10,2) | Coupon discount amount — Discount Responsive, KPIs |
| coupon_match_disc | DECIMAL(10,2) | Coupon match discount amount — Discount Responsive, KPIs |
| other_discount | DECIMAL(10,2) | Other discount amount — Discount Responsive, KPIs |
| total_discount | DECIMAL(10,2) | **Computed:** Sum of all discounts — KPIs |
| net_amount | DECIMAL(10,2) | **Computed:** sales_value minus total_discount — CLV, KPIs |

**Computed Column Formulas:**

- `total_discount` = `retail_disc` + `coupon_disc` + `coupon_match_disc` + `other_discount`
- `net_amount` = `sales_value` - `total_discount`

---

## REMAINING TABLES (Not Yet Defined)

### Group 3 (4 tables remaining):
- `events`
- `sessions`
- `coupon_campaigns`
- `coupon_redemptions`

### Group 4: Features & Messaging (4 tables):
- `customer_features`
- `marketing_campaigns`
- `message_sends`
- `holidays`

### Group 5: Segmentation (2 tables):
- `segments`
- `customer_segments`

### Group 6: Analytics (2 tables):
- `customer_kpis`
- `store_kpis`

### Group 7: Campaign Management (3 tables):
- `campaigns`
- `workflows`
- `workflow_nodes`

---

## DESIGN PRINCIPLES

1. **Multi-tenancy:** Shared database, shared tables, `store_id` on every tenant-owned row
2. **No redundant columns:** Each attribute serves at least one segment or KPI card
3. **Computed columns:** Calculated by the platform, not from customer uploaded data
4. **Traceability:** `source` and `source_*_id` columns track where data came from
5. **One canonical schema:** Data from any store maps to the same tables
