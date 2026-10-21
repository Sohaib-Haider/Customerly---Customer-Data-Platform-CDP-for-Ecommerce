# Data Transformation — Overall Approach

**Scope:** Members A, B and C — six source datasets covering nine segments  
**Last Updated:** 2026-10-21

## Purpose

The platform is a multi-tenant e-commerce Customer Data Platform (CDP). Stores upload CSV data, which is mapped and normalized into a shared canonical PostgreSQL schema.

The transformation work prepares realistic, clean, unified data for testing that platform. This document records the common approach specified in the three member briefs; it is not a claim that every member's implementation or output has been verified.

## ML Training and Platform Testing Are Separate

| Track | Input | Purpose |
|---|---|---|
| ML training | Raw datasets for each segment | Train and evaluate segment models separately from the platform database |
| Platform testing | Transformed data in canonical schema format | Exercise ingestion, column mapping, feature computation and dashboards |

ML models are trained on the raw per-segment datasets, **not on the transformed platform-testing data**. Those raw training datasets are not loaded into the platform database as training data.

The transformed data is intended for this end-to-end testing flow:

```text
Source datasets
    → clean, map columns, remap IDs and align dates
    → canonical CSVs
    → platform upload and column mapping
    → unified PostgreSQL database
    → platform feature computation
    → dashboards and customer views
```

**ML segment prediction is excluded from this transformation-testing scope.** The transforms do not train models, run inference, assign predicted segments or calculate ML scores. Feature computation happens later in the platform, not in the offline transformation scripts.

The transformed copies do not need to preserve ML training integrity, because training uses the separate raw datasets. They must still preserve source behavioral patterns as far as the canonical representation permits; this separation does not authorize inventing customer relationships or changing purchase and engagement patterns.

## Member Responsibilities

| Member | Source datasets | Segments covered | Main canonical destinations |
|---|---|---|---|
| A | Dunnhumby: The Complete Journey | 6. Replenishment-Ready; 7. Cross-Sell Opportunity; 8. Seasonal Purchase | `customers`, `products`, `orders`, `order_items`, `causal_data`; supported coupon/campaign records where mapped |
| B | eCommerce behavior data from multi category store; Online Retail II | 1. Predicted Purchase Intent; 2. Future High-Value / CLV; 9. Cart Abandoners | Behavioral data → `events`; retail transactions → `customers`, `products`, `orders`, `order_items` |
| C | Predicting Coupon Redemption; E-commerce Customer Churn; E-commerce multichannel direct messaging | 3. Discount Responsive; 4. Churn-Risk; 5. Channel Preference | Coupon data → `customers`, `products`, `orders`, `order_items`, `coupon_campaigns`, `coupon_redemptions`; churn data → `customers`; messaging data → `customers`, `marketing_campaigns`, `message_sends`, `holidays` |

These segment names identify the source data's intended use. They do not mean the transformation itself produces segment predictions.

## Common Transformation Workflow

1. **Read the source data and reference mappings.** Identify the source fields, row grain and canonical destinations.
2. **Clean and cast values.** Handle missing identifiers, whitespace, duplicate records and data types. Document removed rows and any information that cannot be represented.
3. **Map columns to the canonical schema.** Shape customers, products, transaction lines, events and messaging records into the relevant tables. Resolve foreign keys consistently.
4. **Remap IDs into a unified namespace.** Distinguish IDs from different source datasets so equal raw IDs cannot accidentally refer to the same customer, product or campaign.
5. **Align dates using a documented strategy.** Apply a consistent offset within each real-date dataset, or a declared anchor for a day-number dataset. Preserve event ordering, time gaps and campaign durations within each source.
6. **Produce canonical CSVs ready for ingestion.** Validate required columns, identifiers, relationships and row counts before loading. Record assumptions and schema limitations alongside the output.

Do not calculate customer-level aggregates or ML features during these steps. Splitting a source transaction file into order headers and line items is structural normalization; the underlying transaction lines must remain available.

## One Unified Store for Testing

The testing setup can place the transformed datasets under one shared demo `store_id`, while the application itself remains multi-tenant. Member C's notes explicitly require one store, and Member A's transform accepts a tenant ID. Combining all members' outputs under one store requires using the same tenant ID at integration time; the common briefs alone do not prove that this has already happened.

Putting datasets under one store does **not** merge unrelated people. A customer from one dataset remains distinct from a customer in another unless there is a supported identity match. Each customer may therefore have only a subset of the fields needed by the nine segments. Missing source attributes remain unavailable rather than being copied from unrelated customers.

Original source-store identifiers, where present, must remain distinguishable from the platform tenant ID. For example, Dunnhumby's original store identifier is represented separately as `source_store_id`.

## Preserving Behavior and Recording Limitations

Preserve transaction amounts, quantities, discounts, returns, event sequences and engagement outcomes wherever the target schema supports them. Do not rescale, rebalance or fabricate behavioral records to make the unified data appear more complete.

Date alignment preserves intervals within a source; it does not establish real chronological relationships between independent datasets. Derived dates, placeholder values and synthetic order identifiers must be documented.

Canonical schema constraints can cause information loss. Missing destination tables, deduplication rules or one-order-per-row assumptions may affect what the platform can calculate. Record those limitations explicitly rather than describing every transformed dataset as fully lossless. Validation passing confirms the checks that were run; it does not by itself prove that all source behavior was preserved.

## References

- [Final canonical schema](../schema-notes/final-schema.md)
- [Segment-to-schema mappings](../customer%20segmentation-notes/segments-schema-mapping.md)
- [Segment source descriptions](../customer%20segmentation-notes/segments/)
- [Member A transformation details](../../../data_prep/transform/DUNNHUMBY.md)
- [Transformation implementation conventions](../../../data_prep/transform/README.md)
- [Member C transformation notes](data-transformation-member-c.md)

The member briefs supplied for this overview define the shared scope. Individual transformation notes and code provide implementation details and evidence of completed runs.
