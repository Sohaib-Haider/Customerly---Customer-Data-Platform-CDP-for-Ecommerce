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
│   └── Revenue (17 cards)
├── Future Categories (not yet added)
│   ├── Customer
│   ├── Order
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

| # | KPI Card | Description |
|---|----------|-------------|
| 1 | Total Stores | Total number of stores that have signed up on the platform |
| 2 | Stores by Plan | Distribution of stores across subscription tiers (free / pro / enterprise) |
| 3 | New Stores (period) | Number of new stores that signed up within a given time period |
| 4 | Total Store Users | Total number of users (owners + staff) across all stores |
| 5 | Users by Role | Distribution of users by role (owner / admin / member) |

**Audience:** CDP team only (internal)

---

### Category 2: Revenue

Revenue metrics for the store owner. These track how much money the store is making, how it's trending, and where it's coming from.

| # | KPI Card | Description |
|---|----------|-------------|
| 1 | Gross Revenue | Total sales value before any discounts |
| 2 | Net Revenue | Actual revenue received after all discounts are applied |
| 3 | Revenue by Period | Revenue broken down by day, week, or month |
| 4 | Revenue by Channel | Revenue attributed to each marketing channel (email, push, SMS) |
| 5 | Revenue by Category | Revenue broken down by product category |
| 6 | Revenue by Segment | Revenue attributed to each customer segment |
| 7 | Average Order Value | Average amount spent per order |
| 8 | Revenue per Customer | Average revenue generated per customer |
| 9 | Revenue Growth Rate | Percentage growth compared to the previous period |
| 10 | Total Discount Amount | Sum of all discounts applied across all orders |
| 11 | Discount Rate | Percentage of gross revenue given away as discounts |
| 12 | Revenue per Item Sold | Average revenue generated per individual item sold |
| 13 | Revenue by Brand | Revenue broken down by product brand |
| 14 | Revenue by Department | Revenue broken down by store department |
| 15 | Revenue by Region | Revenue broken down by geographic region |
| 16 | Revenue by Week | Revenue broken down by week number |
| 17 | Historical CLV (Actual) | Actual lifetime value calculated from historical purchase data |

**Audience:** Store owner (customer-facing)

---

## Future Categories

The following categories are planned but not yet added to this file. We will discuss and finalize each one before adding it:

- Customer KPIs
- Order KPIs
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
