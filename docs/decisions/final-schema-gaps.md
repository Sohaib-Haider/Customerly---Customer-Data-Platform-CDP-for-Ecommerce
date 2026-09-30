# Final Schema Gap Analysis

**Status:** Draft — for review
**Last Updated:** 2026-09-28
**Source documents:** `schema-blueprint.md`, `Overview.md`, `final-schema.md`

---

## Purpose

This file documents tables that are missing from `final-schema.md` when measured against the design intent in `schema-blueprint.md` and the product requirements in `Overview.md`. The final schema covers the analytics and segmentation data model well, but it is missing tables needed for platform operations — role-based access, ML model versioning, segment configuration, KPI metadata, and the workflow builder.

---

## Missing Tables

### 1. `roles`

`store_users` has a `role` column with text like "owner" or "admin", but no table defines what each role can actually do. Can an admin delete campaigns? Can a member view revenue? Without this, the app can't enforce who can do what.

Django's built-in `auth.Group` and `auth.Permission` models are app-level, not per-store. For a multi-tenant product where each store has its own custom roles, a `roles` table with a `store_id` FK is needed — or Django's `Group` model must be extended with a `store_id` column.

| Attribute | Type | Explanation |
|---|---|---|
| `store_id` | UUID | Which store this role belongs to |
| `name` | VARCHAR | Role name (e.g. "owner", "admin", "member") |
| `permissions` | JSONB | What this role can do (e.g. `["view_revenue", "delete_campaigns"]`) |

---

### 2. `ml_models`

The platform trains ML models for segments (Churn-Risk, Purchase Intent, etc.). But there's no table to store which model version is currently used for each segment. If you retrain a model, there's no record of what changed or which version is live. It's like having no version history for your ML code.

| Attribute | Type | Explanation |
|---|---|---|
| `segment_id` | UUID | Which segment this model is for |
| `version` | VARCHAR | Model version (e.g. "v1.0", "v2.0") |
| `model_path` | VARCHAR | Where the model file is stored |
| `trained_at` | TIMESTAMPTZ | When this model was trained |
| `metrics` | JSONB | Performance scores (e.g. `{"accuracy": 0.92}`) |
| `is_active` | BOOLEAN | Whether this is the currently used model |

---

### 3. `segment_definitions`

Overview.md says segments are "config-driven, not hardcoded" — each segment has a `required_fields` schema that determines if a store's data qualifies for it. But there's no table that stores these definitions. Right now the segment configs would have to be written in code, which contradicts the "config-driven" requirement.

| Attribute | Type | Explanation |
|---|---|---|
| `name` | VARCHAR | Segment name (e.g. "Churn-Risk") |
| `segment_type` | VARCHAR | "ml" or "rule" |
| `required_fields` | JSONB | What fields a store must have to use this segment |
| `description` | TEXT | What this segment does |

---

### 4. `kpi_definitions`

There are 115 KPI cards, but no table defines what each KPI is — its formula, category, refresh schedule, etc. Right now this info only exists in the documentation file. If someone wants to add or change a KPI, there's no database record to update.

| Attribute | Type | Explanation |
|---|---|---|
| `name` | VARCHAR | KPI identifier (e.g. "gross_revenue") |
| `category` | VARCHAR | Which category (e.g. "revenue", "customer") |
| `formula` | TEXT | How to calculate this KPI |
| `refresh_schedule` | VARCHAR | How often to recalculate (e.g. "daily", "weekly") |
| `description` | TEXT | What this KPI measures |

---

### 5. `workflows`

Overview.md says users build campaigns with a "drag-and-drop workflow builder." But there's no table to store these workflows. A workflow is like: "When a customer abandons cart, wait 1 hour, then send WhatsApp message."

Following n8n's proven pattern, the entire workflow definition (nodes + connections) is stored as JSONB in a single row — no separate `workflow_nodes` table needed.

| Attribute | Type | Explanation |
|---|---|---|
| `store_id` | UUID | Which store owns this workflow |
| `name` | VARCHAR | Workflow name (e.g. "Abandoned Cart Recovery") |
| `status` | VARCHAR | "draft", "active", or "paused" |
| `nodes` | JSONB | Array of node objects: `[{"id": "node_1", "type": "send_message", "config": {"message": "Hello"}, "position": 1}, ...]` |
| `connections` | JSONB | Node connection mapping: `{"node_1": {"next": "node_2"}, ...}` |
| `campaign_id` | UUID | FK → marketing_campaigns — links workflow-created campaigns to existing campaign table |
| `created_at` | TIMESTAMPTZ | When this workflow was created |
| `updated_at` | TIMESTAMPTZ | When this workflow was last modified |

---

### 6. `workflow_executions`

Once a workflow runs, you need to track which customer is at which step. Without this, you can't know who received which message or who's still waiting.

| Attribute | Type | Explanation |
|---|---|---|
| `workflow_id` | UUID | FK → workflows — which workflow is running |
| `customer_id` | UUID | FK → customers — which customer is in this workflow |
| `segment_id` | UUID | FK → customer_segments — which segment triggered this execution |
| `current_node_index` | INTEGER | Index into the nodes JSONB array — which step the customer is currently at |
| `status` | VARCHAR | "waiting", "completed", or "exited" |
| `started_at` | TIMESTAMPTZ | When the customer entered this workflow |
| `completed_at` | TIMESTAMPTZ | When the customer finished this workflow |

---

### 7. Missing FK: `workflow_executions` → `message_sends`

When a workflow's send_message node fires, a record is created in `message_sends`. But there is no foreign key linking that message back to the `workflow_executions` row that caused it. Without this link, you cannot attribute message engagement (opens, clicks, purchases) to a specific workflow execution.

**Fix:** Add `workflow_execution_id` UUID FK → `workflow_executions` on `message_sends`. Nullable — historical ingested messages will have this as NULL.

---

### 8. Missing trigger: `customer_features` refresh on new data

When the platform generates new data (orders, messages, events) after a workflow runs, `customer_features` must be recomputed so ML segment scores and KPI cards reflect the latest activity. The schema has `computed_at` for staleness detection, but no mechanism to trigger incremental refresh when new rows arrive in `orders`, `order_items`, `message_sends`, or `events`.

**Fix:** Add a trigger or scheduled job that detects new platform-generated rows and flags affected customers for feature recomputation. Without this, `customer_features` and `kpi_snapshots` will only reflect historical uploaded data, not live platform activity.

---

## What's Already Covered

The following tables exist in `final-schema.md` and are sufficient for their purpose:

| Blueprint Group | Tables |
|---|---|
| Group 1: Platform Core | `stores`, `store_users` |
| Group 2: Data Ingestion | `ingestion_uploads`, `ingestion_column_mappings` |
| Group 3: Canonical Customer Data | `customers`, `products`, `orders`, `order_items` |
| Group 4: Features & Messaging | `events`, `customer_features`, `marketing_campaigns`, `message_sends`, `holidays`, `causal_data` |
| Group 5: Segmentation | `segments`, `customer_segments` |
| Group 6: Analytics | `kpi_snapshots` |
| Group 7: Campaign Management | `coupon_campaigns`, `coupon_redemptions` |

---

## Recommendation

Add the 6 missing tables to `final-schema.md` before freezing the schema. These tables are needed for:

- **Platform operations** — `roles` for access control
- **ML pipeline** — `ml_models` for model versioning
- **Config-driven architecture** — `segment_definitions` and `kpi_definitions` so segments and KPIs are data, not code
- **Campaign execution** — `workflows` (with nodes/connections as JSONB) and `workflow_executions` for the drag-and-drop workflow builder

Additionally, 2 relationship/trigger gaps must be addressed:

- **Workflow → Message attribution** — add `workflow_execution_id` FK on `message_sends` to link platform-sent messages back to their workflow execution
- **Feature refresh on live data** — add a trigger or scheduled job to recompute `customer_features` when new platform-generated rows arrive in `orders`, `order_items`, `message_sends`, or `events`

Without these tables and relationships, the platform cannot fulfill the product requirements described in `Overview.md`.
