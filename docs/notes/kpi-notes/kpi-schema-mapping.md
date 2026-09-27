# KPI Schema Mapping

**Status:** Draft — under active discussion  
**Last Updated:** 2026-09-27

---

## Purpose

This document defines every KPI card available on the platform. It is the single source of truth for:

- What each KPI measures
- Which category it belongs to
- Who sees it (platform team vs. store owner)

This file does **not** yet define formulas, SQL, or target table schemas — those come after we finalize the KPI list.

---

## File Map

```
kpi-schema-mapping.md
├── Purpose
├── File Map (this section)
├── KPI Types
│   ├── Platform KPIs (internal — CDP team only)
│   └── Store KPIs (customer-facing — store owner dashboard)
├── KPI Categories
│   ├── Platform Growth (5 cards)
│   ├── Revenue (17 cards)
│   ├── Customer (20 cards)
│   └── Order (10 cards)
├── Future Categories (not yet added)
│   ├── Product
│   ├── Marketing/Channel
│   ├── Coupon
│   ├── Event/Behavior
│   ├── Segment ML Scores
│   ├── Causal/Promotion
│   ├── Holiday
│   └── Channel Preference
└── Changelog
```

---

## KPI Types

There are two types of KPIs on this platform:

| Type | Audience | Purpose |
|------|----------|---------|
| **Platform KPIs** | CDP team (internal) | Monitor platform growth, store adoption, and user engagement |
| **Store KPIs** | Store owner (customer-facing) | Business performance, marketing, customer insights |

Store owners **never** see Platform KPIs. Platform KPIs are internal metrics for the CDP product team.

---

## KPI Categories

### Category 1: Platform Growth

Platform-level metrics for the CDP team. These track how the platform itself is growing — how many stores are signing up, what plans they're on, and how many users they're adding.

| # | KPI Card | Description | Required Attributes |
|---|----------|-------------|---------------------|
| 1 | Total Stores | Total number of stores that have signed up on the platform | `stores.id` |
| 2 | Stores by Plan | Distribution of stores across subscription tiers (free / pro / enterprise) | `stores.plan` |
| 3 | New Stores (period) | Number of new stores that signed up within a given time period | `stores.created_at` |
| 4 | Total Store Users | Total number of users (owners + staff) across all stores | `store_users.id` |
| 5 | Users by Role | Distribution of users by role (owner / admin / member) | `store_users.role` |

**Audience:** CDP team only (internal)

---

### Category 2: Revenue

Revenue metrics for the store owner. These track how much money the store is making, how it's trending, and where it's coming from.

| # | KPI Card | Description | Required Attributes |
|---|----------|-------------|---------------------|
| 1 | Gross Revenue | Total sales value before any discounts | `order_items.sales_value` |
| 2 | Net Revenue | Actual revenue received after all discounts are applied | `order_items.sales_value`, `order_items.retail_disc`, `order_items.coupon_disc`, `order_items.coupon_match_disc`, `order_items.other_discount` |
| 3 | Revenue by Period | Revenue broken down by day, week, or month | `orders.order_date`, `order_items.sales_value` |
| 4 | Revenue by Channel | Revenue attributed to each marketing channel (email, push, SMS) | `message_sends.channel`, `message_sends.is_purchased`, `order_items.sales_value` |
| 5 | Revenue by Category | Revenue broken down by product category | `products.category`, `order_items.sales_value` |
| 6 | Revenue by Segment | Revenue attributed to each customer segment | `customer_segments.segment_name`, `order_items.sales_value` |
| 7 | Average Order Value | Average amount spent per order | `order_items.sales_value`, `orders.source_order_id` |
| 8 | Revenue per Customer | Average revenue generated per customer | `order_items.sales_value`, `orders.customer_id` |
| 9 | Revenue Growth Rate | Percentage growth compared to the previous period | `order_items.sales_value`, `orders.order_date` |
| 10 | Total Discount Amount | Sum of all discounts applied across all orders | `order_items.retail_disc`, `order_items.coupon_disc`, `order_items.coupon_match_disc`, `order_items.other_discount` |
| 11 | Discount Rate | Percentage of gross revenue given away as discounts | `order_items.retail_disc`, `order_items.coupon_disc`, `order_items.coupon_match_disc`, `order_items.other_discount`, `order_items.sales_value` |
| 12 | Revenue per Item Sold | Average revenue generated per individual item sold | `order_items.sales_value`, `order_items.quantity` |
| 13 | Revenue by Brand | Revenue broken down by product brand | `products.brand`, `order_items.sales_value` |
| 14 | Revenue by Department | Revenue broken down by store department | `products.department`, `order_items.sales_value` |
| 15 | Revenue by Region | Revenue broken down by geographic region | `orders.region`, `order_items.sales_value` |
| 16 | Revenue by Week | Revenue broken down by week number | `orders.week_no`, `order_items.sales_value` |
| 17 | Historical CLV (Actual) | Actual lifetime value calculated from historical purchase data | `order_items.sales_value`, `orders.customer_id` |

**Audience:** Store owner (customer-facing)

---

### Category 3: Customer

Customer metrics for the store owner. These track who the customers are, how engaged they are, and how much value they bring over time.

| # | KPI Card | Description | Required Attributes |
|---|----------|-------------|---------------------|
| 1 | Total Customers | Total number of unique customers | `customers.source_customer_id` |
| 2 | New Customers | Customers who made their first purchase in the period | `customers.first_purchase_date` |
| 3 | Active Customers (30d) | Customers who ordered in the last 30 days | `customers.days_since_last_order` |
| 4 | Customer Retention Rate | % of customers who kept buying vs. previous period | `customers.first_purchase_date`, `orders.order_date` |
| 5 | Customer Churn Rate | % of customers who stopped buying | `customers.churn` |
| 6 | Customer Lifetime Value (CLV) | ML-predicted future value of a customer | `customer_features.recency`, `frequency`, `monetary`, `avg_order_value`, `customer_tenure`, `total_items_bought`, `unique_products_bought`, `max_single_order_value`, `min_single_order_value`, `purchase_regularity` |
| 7 | Repeat Purchase Rate | % of customers who bought more than once | `customer_features.frequency` |
| 8 | Avg Days Between Purchases | Average gap between consecutive orders | `customer_features.purchase_regularity` |
| 9 | Customer Satisfaction (CSAT) | Average satisfaction score (1-5) | `customers.satisfaction_score` |
| 10 | Complaint Rate | % of customers who raised a complaint | `customers.complain` |
| 11 | Average Tenure | Average days as a customer | `customers.tenure` |
| 12 | Avg Days Since Last Order | Average recency across all customers | `customers.days_since_last_order` |
| 13 | Average Cashback Amount | Average cashback earned per customer | `customers.cashback_amount` |
| 14 | Churn Rate by Category | Churn rate broken down by preferred category | `customers.churn`, `customers.preferred_order_category` |
| 15 | One-time Buyer Rate | % of customers who only bought once | `customer_features.frequency` |
| 16 | High-Value Customer Rate | % of customers above a monetary threshold | `customer_features.monetary` |
| 17 | Customer Growth Rate | Net customer growth vs. previous period | `customers.first_purchase_date` |
| 18 | Avg Warehouse-to-Home Distance | Average delivery distance | `customers.warehouse_to_home` |
| 19 | Avg Devices Registered | Average devices per customer | `customers.number_of_devices_registered` |
| 20 | Time to First Purchase | Days from signup to first order | `customers.first_purchase_date` |

**Audience:** Store owner (customer-facing)

---

### Category 4: Order

Order metrics for the store owner. These track order volumes, conversion funnels, and fulfillment patterns.

| # | KPI Card | Description | Required Attributes |
|---|----------|-------------|---------------------|
| 1 | Total Orders | Total number of unique orders placed | `orders.source_order_id` |
| 2 | Orders per Customer | Average number of orders per customer | `orders.source_order_id`, `orders.customer_id` |
| 3 | View to Cart Rate | % of product views that result in add-to-cart | `customer_features.view_count`, `customer_features.cart_count` |
| 4 | Cart to Purchase Rate | % of add-to-cart events that result in purchase | `customer_features.purchase_count`, `customer_features.cart_count` |
| 5 | Cart Abandonment Rate | % of carts that never convert to purchase | `customer_features.cart_count`, `customer_features.purchase_count`, `customer_features.remove_from_cart_count` |
| 6 | Avg Items per Order | Average number of line items per order | `order_items.quantity`, `orders.source_order_id` |
| 7 | Orders by Region | Order count broken down by geographic region | `orders.region` |
| 8 | Order Growth Rate | Order growth % vs. previous period | `orders.order_date` |
| 9 | Order Peak Hour | Hour of day with the most orders | `orders.order_timestamp` |
| 10 | Session to Purchase Conversion | % of sessions that result in a purchase | `customer_features.purchase_count`, `customer_features.session_count` |

**Audience:** Store owner (customer-facing)

---

## Future Categories

The following categories are planned but not yet added to this file. We will discuss and finalize each one before adding it:

- Product KPIs
- Marketing/Channel KPIs
- Coupon KPIs
- Event/Behavior KPIs
- Segment ML Scores
- Causal/Promotion KPIs
- Holiday KPIs
- Channel Preference KPIs

---

## Changelog

| Date | Change |
|------|--------|
| 2026-09-27 | Initial draft — added Platform Growth and Revenue categories |
| 2026-09-27 | Added Customer category (20 cards) |
| 2026-09-27 | Added Order category (10 cards) |
