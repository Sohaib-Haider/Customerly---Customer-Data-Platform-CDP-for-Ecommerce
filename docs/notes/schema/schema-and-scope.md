# CDP — Schema, Scope & Data Architecture Notes

Status: Draft (to review before finalizing)
Related: `docs/decisions/data-transformation-plan.md`, `docs/Overview.md`, [`docs/notes/customer segmentation/segments-overview.md`](docs/notes/customer%20segmentation/segments-overview.md)

## 1. The Product, in one breath

A CDP for ecommerce stores. A store connects its customer data (CSV or read-only Postgres), we clean + map it into our canonical form, then we give back: **9 predicted customer segments**, **KPIs**, a **Customer 360 profile** per customer, and **multi-channel campaigns** (Email / WhatsApp / SMS) via a drag-and-drop workflow builder.

## 2. Our scope = the 6 Kaggle datasets (nothing beyond them)

We can't build "customer data platform for every ecommerce in the world." So we cap scope: **the product must cater to exactly what these 6 datasets can express.** Every segment, every KPI, every stat we ship = one that is derivable from the data universe these datasets describe. Anything outside that universe is out of scope.

> Rule: the product handles **everything these datasets provide.** Nothing more.

The 9 segments (from the 6 datasets). Full segment playbook → [`segments-overview.md`](docs/notes/customer%20segmentation/segments-overview.md); per-segment dataset files & attributes → [`docs/notes/segments/`](docs/notes/segments/):

| # | Segment | Source dataset | Segment note |
|---|---|---|---|
| 1 | Predicted Purchase Intent | Multi-category behavior events | [`Predicted Purchase Intent.md`](docs/notes/segments/Predicted%20Purchase%20Intent.md) |
| 2 | Future High-Value / CLV | Online Retail II | [`Future High-Value - CLV.md`](docs/notes/segments/Future%20High-Value%20-%20CLV.md) |
| 3 | Discount Responsive | Predicting Coupon Redemption | [`Discount Responsive.md`](docs/notes/segments/Discount%20Responsive.md) |
| 4 | Churn-Risk | E-commerce Customer Churn | [`Churn-Risk.md`](docs/notes/segments/Churn-Risk.md) |
| 5 | Channel Preference | Multichannel direct messaging | [`Channel Preference.md`](docs/notes/segments/Channel%20Preference.md) |
| 6 | Replenishment-Ready | Dunnhumby Complete Journey | [`Replenishment-Ready.md`](docs/notes/segments/Replenishment-Ready.md) |
| 7 | Cross-Sell Opportunity | Dunnhumby Complete Journey | [`Cross-Sell Opportunity.md`](docs/notes/segments/Cross-Sell%20Opportunity.md) |
| 8 | Seasonal Purchase | Dunnhumby Complete Journey | [`Seasonal Purchase.md`](docs/notes/segments/Seasonal%20Purchase.md) |
| 9 | Cart Abandoners | Multi-category behavior events | [`Cart Abandoners.md`](docs/notes/segments/Cart%20Abandoners.md) |

## 3. The two layers (the mental model)

These two layers are the core of the whole database/data design. They are tied, but they are not the same thing.

### Layer 1 — "One ecommerce store's data" (the transformed dataset)

- The canonical shape any connected store's data is normalized into.
- It is the target of the LLM column mapping and the `required_fields` schema definitions.
- The transformed + merged Kaggle data is **one concrete instance** of it — a full dataset that reads: "we are one ecommerce store, here is all our customers' data."
- Built offline by `data_prep/` (transform → processed → training). Pure pandas + CSVs. No backend involvement.

Canonical Layer 1 tables:

| Table | Key columns |
|---|---|
| `customers` | `customer_id`, `source`, `raw_id`, `age_band`, `income_band`, `marital_status`, `household_size`, `kid_count`, `is_homeowner`, `region`, `department` |
| `products` | `product_id`, `source`, `raw_id`, `name`, `brand`, `department`, `category`, `size`, `unit_price` |
| `orders` | `order_id`, `customer_id`, `order_date`, `region`, `department`, `source`, header discounts |
| `order_items` | `order_id`, `product_id`, `quantity`, `unit_price`, `retail_disc`, `coupon_disc` |
| `coupon_campaigns` | `campaign_id`, `campaign_type`, `start_date`, `end_date`, `source` |
| `coupon_redemptions` | `redemption_id`, `customer_id`, `coupon_id`, `campaign_id`, `date`, `discount`, `source` |
| `events` | (Phase 2 — session-level view/cart/purchase, drives Purchase Intent + Cart Abandoners) |

### Layer 2 — The platform's own schema (the product we actually build)

Layer 2 is **everything our product stores.** It is one shared database, and it holds three kinds of tables:

1. **Store tables** — one row per ecommerce store that signs up to use our tool.
2. **Customer-data tables** — the store's own customer data after mapping and cleaning. They are the same tables as Layer 1 (customers, orders, products, events…), except every row is tagged with the store that owns it.
3. **Result tables** — the outputs our product *creates* by running on a store's data: which customers belong to which segment (`customer_segments`), each customer's KPI values (`customer_kpis`), store-level KPIs (`store_kpis`), and campaign/workflow/send records.

**Multi-tenant means:** one database, tables shared by every store, and each row carries the owning store's id (`store_id`). A store only ever sees its own rows — we never make a separate table per store.

Guiding principle for both layers: **one canonical schema, not one schema per dataset.** The 6 datasets only tell us which columns/entities the tables must be able to hold. A tenant's tables fill only the parts their data satisfies.

## 4. Layer 2 (draft entities, derived from the 6 datasets)

> **DRAFT — NOT the final schema.** The Layer 2 architecture below is a skeleton: it lists entity names and grouping only. It is missing several tables (ingestion/connections, column mapping, channel configs, workflow edges, outbound sends, store-level KPIs) and almost every attribute/column. The exact schema with all tables, columns, types, and keys will be defined as a full data dictionary before the ER diagram.

```
stores (tenants = the ecommerce store accounts; from the users app)
  id, owner, name, plan, created_at

  > customer data cluster (Layer 1 tables, tenant-scoped by store_id):
  # customer-data tables = the store's own customer data after mapping & cleaning.
  # They are the SAME tables/shape as Layer 1 (customers, orders, products, events...),
  # except every row is tagged with the store that owns it (store_id).
  # Required-attributes rule: a store's upload may contain many columns (their schema).
  # The platform looks ONLY for the attributes each feature needs, maps those,
  # ignores everything else, and stores only the required ones (store_id + the required columns).
  customers, products, orders, order_items,
  coupon_campaigns, coupon_redemptions, events, sessions

  > segmentation & analytics:
  segments            (definition + required_fields per segment — config-driven)
  customer_segments   (customer_id, segment_id, score, computed_at)
  customer_kpis       (customer_id, kpi, value, computed_at)

  > customer 360 view = a JOINED VIEW over customers + segments + KPIs
    (nothing separately stored)

  > campaigns:
  campaigns, workflows, workflow_nodes (send-message / wait / channel-select)
  message_sends       (outbound log: customer, channel, status, engagement flags)
```

Hard rule for the ER diagram: **store accounts (users) and end-customers (ingested) are separate entities.** A store *owns* its customers; they are never the same table.

## 5. Dataset → Layer-2 entity derivation (scope map)

Per-dataset field-level detail → the segment note files listed in §2.

| Dataset | Data it expresses | Layer-2 entities it drives |
|---|---|---|
| Multi-category events | session browsing (view/cart/purchase), Oct–Nov 2019 | `events`, `sessions`, purchase-derived `orders`, product/category info |
| Coupon Redemption | coupons, campaigns, redemption labels, transactions, demographics | `coupon_campaigns`, `coupon_redemptions`, `customers.demographics`, `orders` |
| Dunnhumby | baskets, products, coupon redemptions, promos (display/mailer), demographics | `orders`/`order_items`, `products`, `promotions`, `customers.demographics` |
| Online Retail II | invoice orders, RFM material, Country | `orders`, `customers` (geo) |
| Churn | pre-computed behavior features + label | `customer_features`, `customer_segments` (or tenant/test data) |
| Channel messaging | campaign sends + engagement outcomes | `message_sends`, channels, engagement flags |

## 6. Identity — the line we will never cross

- Every dataset is customer data with a customer id (`user_id`, `Customer ID`, `customer_id`, `client_id`, `household_key`) — see each segment note's attribute list (links in §2).
- Those ids are **anonymous and only meaningful inside their own source.** No dataset contains PII (email/phone/name) that could tie a person across sources. Overlap between datasets does not exist.
- Therefore: **we never merge two sources as "the same customer," and we never reuse raw ids** across sources.
- Unification is **structural**: one customers master, canonical ids (namespaced per source, e.g. `DH-…`, `CR-…`), every customer appears once, each keeps only its own real history. No fabricated people, no invented histories.
- Result: some segments only ever apply to a subset of customers — and that's correct, because it mirrors the platform rule: *a segment is available only if the store's data satisfies its required fields.*

## 7. Calendar (store timeline)

- Store epoch: `2024-01-01`.
- Regions/departments share one calendar window (e.g. grocery chain with two regions; later electronics + giftware departments).
- All source dates flow through one documented offset function. Relative dates (Dunnhumby `DAY`) are converted; real dates shifted into the window.
- Seasonal/recency features stay valid only if the timeline is monotonic and coherent — hence the seasonality alignment check between regions.

## 8. What the platform is NOT doing

- Not writing back to a connected store's database (their DB is read-only input).
- Not storing the Customer 360 as raw data — it's a joined view.
- Not trusting a store's segment claims — segments are recomputed from mapped/cleaned data.
- Not merging Churn + Channel messaging as fake behavior — they're handled as separate tenants / model testbeds (no orders/products exist to merge logically).

## 9. Where the schema/data artifacts live

- `data_prep/schemas/` — `required_fields` per segment (the config the LLM mapping + segment availability check reads).
- `data_prep/transform/` → `processed/` — Layer 1 instance(s) of the store.
- `data_prep/training/` → `backend/ml_models/` — trained model artifacts.
- `backend/` — Layer 2 implementation (apps: users, ingestion, mapping, segments, analytics, campaigns; core/, config/).
- `docs/diagrams/` — future ER diagram of Layer 2.