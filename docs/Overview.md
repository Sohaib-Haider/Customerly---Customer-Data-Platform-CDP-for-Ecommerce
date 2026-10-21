## Overview
CDP focused only on ecommerce. Lets an ecommerce store connect their customer data, get ML/rule-based customer segmentation, KPIs, a Customer 360 view, and run multi-channel campaigns (email/SMS/WhatsApp) via a drag-and-drop workflow builder.

## File Map:

```
intellimerchant/
├── data_prep/                     # offline, one-time work before the app runs
│   ├── raw/                       # original 6 Kaggle datasets
│   ├── transform/                 # merging + transformation scripts/notebooks
│   ├── processed/                 # final clean per-segment training datasets
│   ├── training/                  # scripts that train each ML model
│   └── schemas/                   # required_fields schema definitions per segment
│
├── backend/                       # Django + DRF app
│   ├── config/                    # project-level settings, urls, wsgi/asgi
│   │   ├── settings/
│   │   │   ├── base.py            # shared settings (all environments)
│   │   │   ├── dev.py             # dev overrides (DEBUG=True, local DB)
│   │   │   └── prod.py            # prod overrides (DEBUG=False, real DB, security)
│   │   ├── urls.py                # root URL routing
│   │   ├── wsgi.py                # WSGI entrypoint (deployment)
│   │   └── asgi.py                # ASGI entrypoint (async/websockets)
│   │
│   ├── apps/                      # business logic, split by domain
│   │   ├── users/                 # store signup/login/profile
│   │   ├── ingestion/             # CSV upload + Postgres-connect handling
│   │   ├── mapping/                # LLM column-mapping logic
│   │   ├── segments/               # segment schemas + segmentation (ML + rule-based)
│   │   ├── analytics/              # KPIs + customer 360 view
│   │   └── campaigns/              # workflow builder, channel sending
│   │
│   ├── core/                      # shared permissions, pagination, exceptions
│   ├── ml_models/                 # trained model artifacts (copied from data_prep/training)
│   ├── requirements/
│   │   ├── base.txt               # shared packages
│   │   ├── dev.txt                # dev-only packages (debug tools, testing)
│   │   └── prod.txt               # prod-only packages (gunicorn, etc.)
│   ├── manage.py                  # Django CLI entrypoint
│   └── .env                       # environment variables/secrets
│
├── frontend/                      # empty for now, framework TBD
│
├── docs/
│   ├── submission/
│   ├── notes/
│   ├── decisions/
│   └── diagrams/
│
└── docker-compose.yml             # container orchestration (backend + db, etc.)
```

## Data Foundation
- 6 raw Kaggle ecommerce datasets, pairwise merged/transformed into 9 clean per-segment training datasets, all normalized to look like one ecommerce store's data
- ML models trained per segment that requires one (some segments are rule-based, no ML)
- Each segment has a fixed `required_fields` schema (config-driven, not hardcoded) — a segment only becomes available to a store if their ingested data satisfies its schema

## Segments 9

Predicted Purchase Intent
Future High-Value / CLV
Discount Responsive
Churn-Risk
Channel Preference
Replenishment-Ready
Cross-Sell Opportunity
Seasonal Purchase
Cart Abandoners

## Ingestion
Two options: CSV upload, or connect their own Postgres DB (read-only source). Both paths converge into the same in-memory pandas pipeline — no per-source branching logic.

## Column Mapping
Claude API (LLM) maps the store's arbitrary column names to the platform's required schema fields (e.g. "petrol average" → "fuel_mileage").

## Cleaning & Storage
Data is cleaned/transformed in-memory (pandas), then persisted once into the platform's own internal multi-tenant Postgres DB. The platform does NOT write back to the store's connected database — their DB is read-only input.

## Segmentation & KPIs
After mapping, check which segments the store's fields satisfy → run those segments (ML or rule-based) → save results to `customer_segments` table. KPIs computed the same way (from raw + ML outputs) → `customer_kpis` table. Customer 360 view = a joined view over customer + segments + KPIs, not separately stored raw data.

## Campaigns
User picks a segment (or custom filter) → builds a workflow with drag-and-drop nodes: send-message (with variables like {{customer_name}}), wait/delay, channel-select (WhatsApp / SMS / email only).

Segmentation only tells you *who* to target (`customer_segments` → `customer_id`). To reach them, the `customers` table carries two nullable contact columns: `email` for email campaigns and `phone_number` for SMS/WhatsApp. Both are optional because many e-commerce datasets have no contact details, so audience resolution skips any customer missing a usable address for the chosen channel rather than failing. Consent/opt-in tracking and holdout control groups are not yet modelled.

## Platform Users
Separate `users` app for the ecommerce store's own account: signup/login/store profile. Distinct from the ingested end-customer data.

## Tech Stack
Backend: Django + DRF (Python chosen for ML/data ecosystem — pandas/sklearn are C-backed, not a performance bottleneck for this use case)
DB: PostgreSQL
LLM: Gemini flash free API
Frontend: TBD, will consume backend via REST APIs

## Folder Structure
Include the backend folder structure (apps split by domain: customers, segments, campaigns, analytics, users; config/, core/, ml_models/), and leave frontend/ as an empty placeholder folder for now.

Write it in clean developer-README style — headers, short bullets, code blocks for structure/stack — no fluff, no marketing tone.