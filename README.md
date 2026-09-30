# Customer Data Platform (CDP) for Ecommerce

**A Customer Data Platform (CDP) built exclusively for ecommerce — turn raw customer data into predictive segments and automated multi-channel campaigns.**

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![Django](https://img.shields.io/badge/Django-REST_Framework-092E20?logo=django)
![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-336791?logo=postgresql)
![Status](https://img.shields.io/badge/Status-In_Development-yellow)
![FYP](https://img.shields.io/badge/FAST_NUCES-Final_Year_Project-red)

---
## File Map:

```
CDP/
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

## Overview

Most CDPs are built for enterprises juggling hundreds of integrations. **Our platform strips that down to what an ecommerce store actually needs** — connect your data, get predictive customer segments, and run targeted campaigns, without the enterprise bloat.

## Core Capabilities

| Feature | What it does |
|---|---|
| Flexible Ingestion | Upload a CSV or connect your Postgres DB directly |
| AI Column Mapping | LLM auto-maps your messy column names to the platform's schema |
| 9 Predictive Segments | ML + rule-based models identify purchase intent, churn risk, CLV, and more |
| Customer 360 View | Every customer's full profile — segments, KPIs, behavior, in one place |
| Omni-Channel Campaigns | Drag-and-drop workflow builder — Email, WhatsApp, SMS |

## The 9 Segments

1. Predicted Purchase Intent
2. Future High-Value / Predicted CLV
3. Discount Responsive
4. Predicted Churn-Risk
5. Predicted Channel Preference
6. Predicted Replenishment-Ready
7. Predicted Cross-Sell Opportunity
8. Predicted Seasonal Purchase
9. Predicted Cart Abandoners

*Full details on datasets, marketing logic, and sources → [`docs/notes/segments.md`](docs/notes/segments.md)*

## How It Works
```
Store connects data (CSV)
↓
LLM maps columns → platform schema
↓
Clean + transform → stored in internal DB
↓
Eligible segments run (ML / rule-based)
↓
KPIs + Customer 360 generated
↓
Launch targeted campaigns by segment
```

---
