# CDP — Schema Design Review

**Status:** Initial draft — for understanding only  
**Last Updated:** 2026-09-27

---

## START HERE — For Agents & Developers

This file is a **design starting point**, not a final schema. Before designing any tables, you MUST read these files in order:

1. **Segment files** (raw data context — understand what data exists):
   - [segments-overview.md](../customer%20segmentation-notes/segments-overview.md)
   - [segments-schema-mapping.md](../customer%20segmentation-notes/segments-schema-mapping.md)
   - All 9 segment files in `../customer%20segmentation-notes/segments/`

2. **KPI definitions** (what metrics need to be served):
   - [kpi-schema-mapping.md](../kpi-notes/kpi-schema-mapping.md)

3. **This file** (schema design intent):
   - schema-blueprint.md

**Do not design tables without first understanding:**
- What raw data is available (segment files)
- What KPIs need to be computed (KPI mapping)
- How data maps to the canonical schema (segments-schema-mapping)

**Note:** Data transformation decisions are pending and will be determined after the schema is finalized. This file provides the initial grouping and design principles only.

---

## Purpose

This document provides the initial design intent for the CDP platform schema. It is meant to give future agents and developers a starting point for understanding how the schema should be organized before they begin designing tables.

The schema is a **multi-tenant, canonical data model** where:
- E-commerce stores upload their data
- The platform normalizes it into a shared schema
- ML-powered customer segments and KPI dashboards are served back to store owners

---

## File Map

> **Note:** This file map is an initial draft based on current understanding. It may be incomplete or subject to change after reviewing the files listed in START HERE.

```
schema-under-review.md
├── START HERE
├── Purpose
├── File Map (this section)
├── Design Overview (initial draft)
│   ├── Group 1: Platform Core
│   ├── Group 2: Data Ingestion
│   ├── Group 3: Canonical Customer Data
│   ├── Group 4: Features & Messaging
│   ├── Group 5: Segmentation
│   ├── Group 6: Analytics
│   └── Group 7: Campaign Management
├── Design Principles
└── Changelog
```

---

## Design Overview

> **Note:** This design overview is an initial draft. It may be incomplete or subject to change after reviewing the files listed in START HERE. The groups below represent the current understanding of how the schema should be organized. Final group definitions, table structures, and relationships will be determined after reviewing the source data and KPI requirements.

### Group 1: Platform Core

Tenant accounts and team members. Handles authentication, authorization, and multi-tenant isolation. Every other group references this group to determine which store owns the data.

### Group 2: Data Ingestion

File upload tracking and LLM-powered column mapping. Tracks what was uploaded, by whom, and how source columns map to the canonical schema. Provides traceability back to original source data.

### Group 3: Canonical Customer Data

The heart of the schema. Stores normalized customer, product, order, and order item data. All financials live at the order item level. This group serves as the single source of truth for all business data.

### Group 4: Features & Messaging

Computed ML features, marketing campaigns, message delivery tracking, and holiday calendars. Powers segmentation, channel preference, and marketing analytics.

### Group 5: Segmentation

Segment definitions and customer-to-segment assignments. Stores which customers belong to which segments and their ML scores. Enables targeted marketing and personalized experiences.

### Group 6: Analytics

Pre-computed KPI tables. Populated by scheduled jobs to make dashboard queries fast. Serves both platform-level and store-level metrics.

### Group 7: Campaign Management

Campaigns, workflows, and workflow nodes. Manages marketing campaign execution, automation, and orchestration across multiple channels.

---

## Design Principles

These principles govern all schema decisions:

1. **Multi-tenancy:** Shared database, shared tables, `store_id` on every tenant-owned row. No separate databases per store.

2. **No redundant columns:** Each column must serve at least one segment or KPI card. If it doesn't, it doesn't exist.

3. **Computed columns are platform-owned:** Calculated fields (totals, net amounts, rates) are computed by the platform, never uploaded by the store.

4. **Traceability:** Source columns and original IDs track where data came from, enabling audit and debugging.

5. **One canonical schema:** Data from any store maps to the same tables. No per-store customization or extensions.

---

## What's NOT in this file

This file intentionally does **not** contain:
- Table names
- Column definitions
- SQL
- Data types
- Constraints
- Data transformation decisions (pending schema finalization)

These will be defined in a separate schema definition document after the design is finalized based on the source data and KPI requirements.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-09-27 | Initial draft — created for understanding and schema design starting point |
