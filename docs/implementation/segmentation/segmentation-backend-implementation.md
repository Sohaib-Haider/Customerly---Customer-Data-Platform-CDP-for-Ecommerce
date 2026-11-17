# Segmentation backend: implementation and extension guide

**Reviewed:** 16 November 2026. This document describes the code currently in the repository. It distinguishes implemented behavior from future platform work.

**Integration update, 17 November:** the [ingestion manager](../ingestion/ingestion-backend-implementation.md) now saves approved CSV data and durably schedules affected customers for these predictors. Customer 360 consumption is also implemented. References below to pending orchestration/consumers predate that integration. CLV now removes the declared source prefix from invoice/product identifiers, retaining its GBP and history checks.

**Audience:** developers and agents maintaining the two current predictors or adding the remaining seven. Read the extension checklist before implementing another predictor; creating a directory alone does not register it.

This document contains no connection credentials, signing secrets, or access tokens. All examples use placeholders.

## Contents

1. [What is implemented](#1-what-is-implemented)
2. [Architecture and boundaries](#2-architecture-and-boundaries)
3. [Files and responsibilities](#3-files-and-responsibilities)
4. [Execution paths and triggers](#4-execution-paths-and-triggers)
5. [Supabase and environment configuration](#5-supabase-and-environment-configuration)
6. [Database tables and ownership](#6-database-tables-and-ownership)
7. [Artifacts, dependencies, and registration](#7-artifacts-dependencies-and-registration)
8. [Eligibility](#8-eligibility)
9. [Churn implementation](#9-churn-implementation)
10. [CLV implementation](#10-clv-implementation)
11. [Results and persistence](#11-results-and-persistence)
12. [Membership rules](#12-membership-rules)
13. [Transactions, repeat runs, and concurrency](#13-transactions-repeat-runs-and-concurrency)
14. [Authentication and tenant isolation](#14-authentication-and-tenant-isolation)
15. [HTTP API](#15-http-api)
16. [Management commands and setup](#16-management-commands-and-setup)
17. [Errors and troubleshooting](#17-errors-and-troubleshooting)
18. [Tests and verification](#18-tests-and-verification)
19. [Adding the next segment](#19-adding-the-next-segment)
20. [Changing a model version](#20-changing-a-model-version)
21. [Remaining integration work](#21-remaining-integration-work)
22. [References](#22-references)

## 1. What is implemented

There is one Django app, `backend/apps/segments`, registered through `apps.segments.apps.SegmentsConfig`. It contains separate predictor packages for `churn` and `clv`.

The current implementation can:

- Read existing customer data from the configured Supabase PostgreSQL database.
- Check customer-level data availability against a model's requirements.
- Prepare inputs in the order and representation expected by the saved models.
- Run the existing final `.pkl` artifacts without retraining them.
- Return either a prediction or an unavailable result with reasons.
- Save the current result for each store/customer/segment.
- Optionally update segment memberships using an explicitly supplied threshold.
- Expose authenticated execution, catalog, result-list, and customer-result APIs.
- Run the same database-backed operation through management commands.

Only two of the nine planned segmentation types are implemented. The current catalog initializer registers those two; it does not create seven empty predictors.

Uploading data does not train a separate model for the store. Prediction uses the existing platform artifacts. No baseline is substituted for CLV at runtime, and there is no experimental opt-in requirement. The historical evaluation metadata inside its final artifact is retained as recorded.

## 2. Architecture and boundaries

### One app, separate modules

The nine segmentation types belong to one business domain. They share customer identity, store isolation, model registration, eligibility machinery, prediction persistence, and API conventions. Each predictor still needs its own feature preparation and output interpretation.

One Django app does not imply nine independently deployed services. These packages run inside the same backend process. A new model normally gets a package under `predictors/`, not another Django app or another `INSTALLED_APPS` entry.

The intended wider platform flow is:

```text
Upload / source connection
  -> ingestion coordinator                    [not implemented yet]
  -> mapping suggestions and explicit approval
  -> configured data cleaning
  -> canonical database saving                [upload integration pending]
  -> affected-customer selection and trigger   [not implemented yet]
  -> segments database execution              [implemented]
  -> current predictions / optional memberships
  -> analytics / Customer 360 / campaigns      [consumer integration pending]
```

Within segmentation:

```text
API authentication + request validation, or trusted management command
  -> execute_customer
  -> store-scoped source reads
  -> run_segment
  -> predictor-specific preparation + eligibility + model inference
  -> verify the active registry matches the local artifact
  -> persist result and optionally update membership
```

### Responsibilities of adjacent apps

| Component | Responsibility |
| --- | --- |
| `mapping` | Resolve uploaded column names to canonical fields using exact matches, aliases, and LLM suggestions; require approval before applying mappings |
| `preprocessing` | General cleaning before storage: duplicates, missing-value handling, formatting, typing, and other configured steps |
| `ingestion` | Intended owner of upload orchestration, validated database writes, changed-customer tracking, and post-save prediction triggering; still a placeholder |
| `segments` | Read canonical data, prepare model inputs, check eligibility, predict, save results, and optionally maintain memberships |
| `analytics` | Currently implements 19 FYP1 historical KPI cards and their API/CLI; Customer 360 and consumption of saved ML predictions remain separate integration work |
| `users` | Future store account/login and authorization integration; not implemented by the segmentation service-token mechanism |
| `campaigns` | Future consumer of audience memberships and customer contact details |

Mapping identifies what an input column means. Segmentation checks whether the resulting canonical data is usable for a particular customer and model. Those are different operations; the predictor does not call the LLM again.

### Cleaning versus model preparation

Numeric normalization and category encoding were removed from the separate preprocessing app's functions, defaults, and execution steps. Model-specific preparation is inside `segments`.

Other preprocessing behavior was retained. Automatic duplicate removal, constant-column removal, type conversion, outlier capping, or batch-based missing-value filling can still affect business data. An ingestion implementation must configure these operations for the source and model contracts; removing normalization alone does not make every other default appropriate for all data.

Churn's saved scaling is applied to in-memory prediction inputs. CLV uses its saved fitted pipeline. Neither path writes the transformed inputs back over customer attributes or purchase amounts. No second cleaned dataset is saved by the predictor. The writes described below are derived predictions, membership changes, model metadata, and migration bookkeeping.

## 3. Files and responsibilities

```text
backend/
├── config/
│   ├── settings/{base,dev,prod,test}.py
│   └── urls.py
├── ml_models/
│   ├── churn_model.pkl
│   ├── churn_preprocessing.json
│   └── clv_model.pkl
└── apps/segments/
    ├── apps.py
    ├── authentication.py
    ├── models.py
    ├── serializers.py
    ├── views.py
    ├── urls.py
    ├── management/commands/
    │   ├── initialize_segments.py
    │   ├── create_segment_token.py
    │   └── run_segments.py
    ├── migrations/
    │   ├── 0001_initial.py
    │   └── 0002_prediction_security.py
    ├── predictors/
    │   ├── churn/{contract,features,preparation,predictor,recover_preprocessing}.py
    │   └── clv/{contract,features,predictor}.py
    ├── services/
    │   ├── catalog.py
    │   ├── database.py
    │   ├── eligibility.py
    │   ├── execution.py
    │   ├── fitted.py
    │   ├── model_loader.py
    │   ├── results.py
    │   ├── runner.py
    │   └── validation.py
    ├── tests/
    │   ├── test_database_api.py
    │   ├── test_eligibility.py
    │   ├── test_fitted.py
    │   └── test_predictors.py
    └── README.md
```

Package `__init__.py` files are omitted from this tree for readability.

| File | Responsibility |
| --- | --- |
| `contract.py` in each predictor | Ordered model inputs, model constants, and eligibility policy |
| `features.py` in each predictor | Validate and construct one customer's model inputs |
| `predictor.py` in each predictor | Validate the artifact, prepare inputs, invoke inference, validate outputs, and build a common result |
| Churn `preparation.py` | Load and validate the saved numeric statistics bound to its classifier |
| Churn `recover_preprocessing.py` | Offline maintenance command to reconstruct and verify missing training preparation |
| `services/runner.py` | Database-independent dispatch to the selected predictor |
| `services/database.py` | Verified canonical field mappings and parameterized source reads |
| `services/execution.py` | Database-backed transaction, registry check, persistence, membership updates, and stale-cutoff protection |
| `services/catalog.py` | Register definitions/artifacts and verify the active artifact identity |
| `services/model_loader.py` | Lazy trusted-artifact loading, file identity, caching, and environment checks |
| `services/eligibility.py` | Reusable configured value/history checks |
| `services/fitted.py` | Apply saved medians/means/scales without learning from the request |
| `services/validation.py` | Shared numeric-value predicate |
| `services/results.py` | Common result objects and expected operational errors |
| `models.py` | Unmanaged canonical table views and the managed current-result table |
| `authentication.py` | Expiring, store-scoped service tokens |
| `serializers.py` | Bounded requests and result pagination validation |
| `views.py` / `urls.py` | Authenticated HTTP boundary; no uploaded data or model paths accepted here |

## 4. Execution paths and triggers

### Pure Python path

`run_segment(segment, *, values=None, purchases=None, observation_date=None, currency=None)` accepts logical Python inputs. It does not query the database or save results. It is useful for predictor tests and reuse independent of Django's database adapter.

For churn, supply `values`. For CLV, supply `PurchaseLine` records, an observation date, and `currency="GBP"`. An unsupported segment returns `unknown_segment`.

### Database-backed path

`execute_customer(key, store_id, customer_id, *, observation_date=None, currency=None, membership_threshold=None, update_memberships=False, persist=True)` reads canonical data and invokes the pure path.

The HTTP boundary obtains `store_id` from its authenticated principal. A trusted internal caller must obtain the store from its own authenticated ingestion/account context. Passing an arbitrary client-provided store ID directly to this service is not authorization.

### Exactly when inference runs

Inference currently runs only when:

- A client calls `POST /api/segments/<key>/run/`; or
- An operator runs `manage.py run_segments`; or
- Trusted backend code explicitly calls the execution function or pure runner.

It does not run at server import/startup, on a GET request, on a database insert, automatically after an upload, or on a schedule. There is no new-rows-only mechanism, dirty-customer queue, background worker, or automatic change detection yet.

Requests provide explicit customer IDs. Previously scored customers can be rerun, and their current saved result is replaced. A future ingestion coordinator should identify affected customer IDs after committing source writes and trigger execution for those IDs.

## 5. Supabase and environment configuration

The backend uses Django's PostgreSQL driver through the Supabase Session pooler. It does not use Supabase's HTTP Data API to read prediction inputs.

`config/settings/base.py`:

- Loads `backend/.env` with `override=False`; existing process environment values take precedence.
- Parses `DATABASE_URL` with `dj-database-url` and rejects non-PostgreSQL URLs.
- Defaults `sslmode` to `require` unless explicitly present in the URL options.
- Defaults connection timeout to 10 seconds.
- Uses `conn_max_age=0`.
- Uses Django's disabled database backend when no URL is supplied; startup and `/health/` can work, but ORM operations cannot.

Use the template in [backend/.env.example](../../../backend/.env.example). A sanitized example is:

```dotenv
DATABASE_URL=postgresql://DATABASE_USER:URL_ENCODED_DATABASE_PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
DJANGO_SECRET_KEY=PRIVATE_RANDOM_APPLICATION_SECRET
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
```

The database password and Django signing secret serve different purposes. Supabase dashboard credentials, anon keys, and service-role API keys are not required for this direct PostgreSQL path. Never commit `.env`, paste its contents into implementation notes, or log the full URL.

The inspected application schema is `public`. Existing model `db_table` names and SQL queries depend on the configured database search path resolving these tables. A custom schema would require a deliberate mapping/search-path change, not a request parameter.

The previously configured connection was verified with encrypted client-to-pooler transport and read-only inspection. `pg_stat_ssl` at the PostgreSQL server can describe the pooler-to-server leg; the client-side `pgconn.ssl_in_use` check was used to verify the application's own encrypted connection.

Management commands, WSGI, and ASGI default to development settings unless `DJANGO_SETTINGS_MODULE` is exported. Production uses `config.settings.prod`, requires private secret/explicit hosts/database URL, disables debug, and enables its HTTPS/cookie settings. Production hosting and proxy configuration are separate deployment tasks. `/health/` is process liveness only, not database/model readiness.

## 6. Database tables and ownership

### Existing canonical tables

| Table | Segmentation use | Write behavior |
| --- | --- | --- |
| `stores` | Verify store existence and scope | Read only |
| `customers` | Customer identity and six churn attributes | Read only |
| `orders` | CLV invoice/customer/date relationships | Read only |
| `order_items` | CLV quantity, original price, amount, and line timestamp | Read only |
| `products` | CLV original product code and store ownership | Read only |
| `segments` | Platform segment definitions and descriptive requirements | Initialization creates missing definitions |
| `ml_models` | Active version, path, digest, metrics, and training-time metadata | Explicit initialization registers/activates artifacts |
| `customer_segments` | Current audience memberships | Written only when membership updates are explicitly requested |
| `customer_features` | Existing canonical feature table | Not read or populated by this implementation; CLV builds its own exact inputs in memory |
| `kpi_snapshots` | Existing KPI storage | Not used to store these per-customer predictions |

`Store`, `Customer`, `SegmentDefinition`, and `CustomerMembership` are unmanaged Django models. They map only the fields this app needs; they are not full ownership claims over every column in those canonical tables. Source orders/items/products are read through parameterized SQL rather than additional ORM model classes.

`managed=False` controls migration ownership; it does not technically prevent ORM writes. Source-data preservation comes from the implemented read-only source code paths and their tests. Keep that boundary when adding adapters.

The 23-table canonical upload schema remains in [backend/schema.sql](../../../backend/schema.sql). The segmentation-owned result table is an additional runtime table created by migrations. A fresh database needs both the canonical schema and these migrations; applying only the CSV loader does not install prediction storage.

### Managed `segment_predictions` table

`SegmentPrediction` owns this derived current-result table:

| Field | Meaning |
| --- | --- |
| `id` | UUID for the current result row |
| `store_id`, `customer_id`, `segment_id` | Store/customer/definition references |
| `segment_key` | Runtime key, currently `churn` or `clv` |
| `status` | `predicted` or `unavailable` |
| `result` | JSON result, metadata, eligibility/reasons, and optional membership-policy snapshot |
| `observation_date` | CLV source-local cutoff; null for churn |
| `computed_at` | Timestamp of the latest persisted computation |

Uniqueness is `(store, customer, segment)`. This is current state, not an append-only history of every run. A failed/unavailable rerun can replace a previously successful current result. Retaining both last-successful and last-attempt state would require an explicit extension.

There is a status check constraint and an index on `(store, segment_key, status)`, plus foreign-key indexes. Source/customer ownership is checked in the application; the three individual foreign keys alone do not prove that all referenced rows belong to the same store.

### Migrations

- `0001_initial`: records unmanaged model state and creates `segment_predictions`. Unmanaged canonical models are no-op table operations; they are not recreated.
- `0002_prediction_security`: on PostgreSQL, enables row-level security and replaces only the new table's foreign keys with deferrable `ON DELETE CASCADE` references. Deleting a parent removes its derived result. It does not alter existing canonical columns or their existing constraints.
- Django also maintains its normal migration-history table.

No public-client row policies are created for prediction storage. Access goes through the trusted backend connection and store-scoped API. An owner-capable PostgreSQL role can bypass RLS; therefore app-level tenant checks are essential. A future restricted database role needs an explicit privilege/RLS plan. RLS/cascade behavior is verified against PostgreSQL, not inferred from SQLite tests.

Do not edit already-applied migrations to evolve the schema; create a new migration.

## 7. Artifacts, dependencies, and registration

### Files and loading

| Runtime key | Trusted artifact | Additional preparation |
| --- | --- | --- |
| `churn` | `backend/ml_models/churn_model.pkl` | `backend/ml_models/churn_preprocessing.json` |
| `clv` | `backend/ml_models/clv_model.pkl` | Fitted preparation already bundled in the artifact |

`model_loader.ARTIFACTS` is an explicit allowlist. The loader accepts a segment key, never an uploaded pickle path. Files are loaded lazily with `joblib`; importing a predictor does not load an artifact. Only trusted project artifacts should be placed here.

Loaded models are cached by file path, modification time, and size, with an LRU capacity of four. The loader returns the object and its full SHA-256. File replacement normally invalidates the cache. It does not implement a general remote model registry or automatically follow arbitrary database `model_path` changes.

Scikit-learn's version-mismatch warning becomes an availability error. CLV and the saved churn preparation compare these recorded versions exactly:

| Dependency | Required recorded version |
| --- | --- |
| Python | 3.13.3 |
| NumPy | 2.5.4 |
| pandas | 3.0.6 |
| scikit-learn | 1.9.1 |
| joblib | 1.6.0 |

The four Python package versions are pinned in `backend/requirements/base.txt`; Python itself is checked at runtime. Upgrading a serialized-model environment requires compatibility validation, not just relaxing the check.

### Catalog initialization

`initialize_segments` explicitly registers:

| Key | Exact catalog name |
| --- | --- |
| `churn` | `Predicted Churn-Risk` |
| `clv` | `Future High-Value / CLV` |

Missing definition IDs are deterministic UUID5 values of `customerly:segment:<key>` under `uuid.NAMESPACE_URL`. Existing definitions are found by unique name. Their description/type/required-fields metadata is preserved rather than overwritten.

The initializer validates local artifacts/preparation before activation, uses a PostgreSQL transaction advisory lock, marks earlier versions of that segment inactive, and upserts the current version. Version labels are `sha256-` followed by the first 24 digest characters. Full digests are stored in `ml_models.metrics` and checked during database execution.

The registry's model path is the relative project path. CLV `trained_at` comes from its saved metadata. Churn's original classifier lacked a training timestamp; its file modification time is stored as an explicitly labelled estimate in registry metrics. Do not present that estimate as a verified training timestamp.

Database-backed successful inference requires exactly one active registry row whose version and full digest match the local artifact. Otherwise the result becomes `model_registry_mismatch`. The pure runner has no database registry check.

The enforcing function is `services.catalog.registry_matches`; `get_definition` resolves definitions by their exact configured display name. Renaming a catalog row without coordinating `DEFINITIONS` can make it appear uninitialized.

`segments.required_fields` is descriptive catalog metadata exposed by the API. It is not currently an executable policy language: runtime eligibility comes from each module's `contract.py`. Editing the JSON alone does not change inference requirements. Initialization also does not overwrite an existing definition's JSON on a later code update.

## 8. Eligibility

`check_eligibility(values, policy, history_counts=...)` is independent of database naming, LLM mapping, and model loading.

- `ValueRequirement(key, allow_missing=False, validator=None)` describes one scalar input.
- `HistoryRequirement(key, minimum_records=1)` describes an already validated history count.
- `EligibilityPolicy(values=..., histories=...)` combines requirements. Empty policies and duplicate keys within one requirement group are rejected.
- `EligibilityResult.eligible` is true only when there are no issues. `as_dict()` returns `eligible`, `reasons`, and `allowed_missing`.

Value checks distinguish absent keys from present-but-missing values. `None`, blank text, `NaN`, `pd.NA`, and `NaT` count as missing. Zero and false are valid present values. Collections, infinite numbers, and complex numbers are not valid scalar real inputs. Extra inputs are ignored.

Missing values are allowed only when the requirement explicitly says so. An absent key still fails, even when missing values are supported. The engine does not fill anything itself. Custom predicates validate model-specific types/ranges; generic availability does not infer units or guarantee a model's applicability to a new store. Validator programming errors propagate instead of being disguised as missing customer data.

History counts must be nonnegative integers, not Booleans. The caller must first select the correct customer, dates, and valid records. A raw row count is not automatically a valid purchase count.

Eligibility reason codes are `missing_input`, `missing_value`, `invalid_value`, `missing_history`, `invalid_history`, and `insufficient_history`. Reasons identify requirements without embedding customer values. A customer may qualify for one predictor and be unavailable for the other.

## 9. Churn implementation

### Database mapping and order

| Ordered estimator label | Canonical source field |
| --- | --- |
| `Tenure` | `customers.tenure` |
| `WarehouseToHome` | `customers.warehouse_to_home` |
| `NumberOfDeviceRegistered` | `customers.number_of_devices_registered` |
| `SatisfactionScore` | `customers.satisfaction_score` |
| `Complain` | `customers.complain` |
| `CashbackAmount` | `customers.cashback_amount` |

The database field map is in `services/database.py`; the model order is in churn `contract.py`. The predictor does not use the source `churn` label, `days_since_last_order`, marital status, address count, or preferred category as inputs.

Numeric inputs must be real numbers/Decimals, not numeric strings. Complaint status accepts Boolean or numeric 0/1. Present missing values are allowed only for tenure and warehouse distance, where missingness occurred in the original source. The remaining four must be usable. A completely empty profile is unavailable.

`prepare_inputs` checks eligibility, builds a one-row DataFrame in exact training order, and represents supported missing values as `NaN`. The fitted helper fills training medians and applies `(value - training_mean) / training_scale`. It never estimates statistics from the current request, removes customers, or writes prepared values back to the database.

### Recovered preparation

The original notebook saved only the Random Forest classifier. The original imputer/scaler statistics were not bundled. The offline recovery command reconstructs their preparation from the unchanged original training CSV and saves a JSON sidecar.

Its exact order is: drop the notebook's excluded columns; fit median imputation on the original numeric inputs excluding the label; impute; deduplicate the reduced/imputed training frame including the retained day field and label; drop `DaySinceLastOrder`; fit `StandardScaler` on the final six inputs; reproduce the original split for verification.

Checks include the recorded source checksum, source shape `(3941, 11)`, deduplicated count 3015, feature order, the saved five-row scaled preview, full transformation parity, missing-value parity, and the existing classifier's recorded ROC-AUC 0.9050 on 603 evaluation rows. The classifier is not trained or rewritten by recovery.

The sidecar contains ordered medians/means/scales, classifier/source/notebook checksums, environment versions, verification results, and the original evaluation limitations. Runtime validates its format and classifier digest before using it. Recovery needs the raw source and notebook; normal prediction needs only the saved `.pkl` and JSON.

`FittedNumericPreparation` requires nonempty unique feature identifiers, matching finite statistic vectors, and positive scales. `apply_fitted_numeric` selects the expected columns, preserves the row index, rejects missing columns/non-scalar/non-numeric/infinite inputs, fills supported missing values, and rejects nonfinite transformed outputs. It returns a new DataFrame without changing the supplied one. The sidecar loader has its own four-entry cache keyed by file identity and classifier digest.

The source's tenure unit and verified future churn horizon are not established. Do not invent them. Recovery reproduces the original workflow, including its recorded preprocessing/evaluation limitations; it does not establish calibration or causal campaign response.

### Inference and output

The artifact must be a fitted `RandomForestClassifier` with the exact six labels and classes `[0, 1]`. Both predicted class and `predict_proba` are checked for valid shapes/classes, finite values, probability bounds, and a sum near one.

```json
{"predicted_class": 0, "churn_probability": 0.12}
```

The numbers above illustrate response shape, not a claimed prediction for a particular customer. Metadata records the model/preparation digests, preparation provenance, and evaluation limitations.

## 10. CLV implementation

### Meaning and artifact

The target is positive purchase revenue in GBP over the next 90 days, using a 288-day history. It is not unlimited lifetime value, profit, returns-adjusted revenue, or a whole-store forecast including new customers.

The saved dictionary contains `pipeline`, ordered `features`, `metadata`, and `prediction_policy`. The selected fitted pipeline is an imputer plus Random Forest regressor; there is no scaling step in this final pipeline. Its imputer uses the saved median/missing-indicator behavior. The backend does not refit it, apply churn scaling, or require an experimental flag. Original evaluation metadata is preserved without becoming an inference gate.

### Reading purchase history

The adapter joins `orders` to `order_items` and `products`, scoped to one store and customer. It selects complete line items for orders inside `[cutoff - 288 days, cutoff)` and checks:

- The product belongs to the same store.
- Original unit price exists.
- Quantity, price, and sales amount are finite and `quantity × unit_price == sales_value` using Decimal arithmetic.
- Known Retail source customer/order/product prefixes are consistent.
- A line timestamp, when present, has the same calendar date as its order. Otherwise the order date is used.

It requests at most 50,001 rows to detect the 50,000-line synchronous limit. Exceeding that limit returns `history_limit_exceeded`; the model is not run on silently truncated history.

The demo population prefixes are `chr`, `cpn`, `dnb`, `evb`, `msg`, and `ret`. Known non-Retail populations are unavailable for this Retail-trained CLV data contract. Identical IDs across populations are not matched. For Retail, `ret:` is stripped when constructing original model identifiers; cancellation prefixes and product-code meanings must survive. Unprefixed future-store identifiers do not receive automatic currency or population equivalence verification simply because a caller declares GBP.

### Logical purchase input and filtering

The pure interface accepts a list/tuple of `PurchaseLine(invoice_id, product_code, timestamp, quantity, unit_price)`. Invoice/product identifiers are strings. They are model-facing identifiers, not database UUIDs. The caller must keep one customer's tenant/source history separate.

The feature builder rejects/excludes missing identifiers, invalid dates, cancellation invoices beginning with `C`, nonpositive or nonintegral quantities, nonpositive/invalid prices, and invalid amounts. The excluded product codes are:

```text
B, ADJUST, ADJUST2, BANK CHARGES, AMAZONFEE, CRUK, D, M, TEST001, TEST002
```

Repeated valid lines are retained. Legitimate postage/carriage codes are not excluded indiscriminately. Conflicting dates within an invoice produce an unavailable result instead of choosing an arbitrary date. Exclusion counts are reported without raw customer values.

The pure builder does not receive customer IDs, so tenant ownership checks belong to the database adapter/caller. Database adapter checks can be stricter than the pure line filter: for example, a missing original price or an inconsistent stored amount rejects the customer's database input rather than silently dropping that line.

### Observation date and exact features

Provide an explicit timezone-free source-local calendar date at midnight. Numeric timestamps, invalid dates, timezone-bearing cutoffs, and intraday cutoffs are rejected by the pure contract. The HTTP interface accepts an ISO date. The machine's current date is never substituted.

The history start is included; the cutoff is excluded. The recorded historical example cutoff is `2011-12-09`; it is not a default for every dataset.

| Ordered feature | Calculation within the valid history |
| --- | --- |
| `recency` | Calendar days from cutoff to last retained purchase |
| `frequency` | Distinct retained invoices |
| `monetary` | Sum of retained invoice revenue in GBP |
| `avg_order_value` | Monetary / frequency |
| `customer_tenure` | Days since earliest retained purchase within this window |
| `total_items_bought` | Sum of retained positive quantities |
| `unique_products_bought` | Distinct retained original product codes |
| `max_single_order_value` | Maximum complete invoice total |
| `min_single_order_value` | Minimum complete invoice total |
| `avg_days_between_orders` | Mean consecutive invoice calendar-day gap |
| `purchase_regularity` | Sample standard deviation of gaps, `ddof=1` |

Invoices are aggregated before computing maximum/minimum order value. Same-day separate invoices create zero-day gaps. One invoice has no gap; two invoices have one gap but no sample standard deviation. Those two interval features may be missing and are handled by the saved imputer. Zero and missing are not interchangeable.

At least one valid history invoice is required. No history produces `insufficient_history`, not a zero prediction. `avg_days_between_orders` is not available as a dedicated column in the existing `customer_features` table; the current exact builder computes it rather than assuming the table already satisfies the saved eleven-feature contract.

### Output

The artifact's ordered features, currency, history/horizon, excluded codes, target, date grain, boundary string, fitted pipeline, prediction-policy string, and dependency versions are validated. Output must be a finite one-element array. The saved nonnegative policy is applied after prediction.

```json
{"predicted_revenue": 120.5, "currency": "GBP", "horizon_days": 90}
```

This is an illustrative response. Metadata includes artifact digest, recorded model status/training time, observation date, history size, valid invoice count, and exclusions. No business high-value threshold is inferred from the amount.

## 11. Results and persistence

`PredictionResult` contains:

| Key | Meaning |
| --- | --- |
| `segment` | Runtime key |
| `status` | `predicted` or `unavailable` |
| `prediction` | Model-specific object, or null |
| `eligibility` | Eligibility details when evaluated; otherwise null |
| `reasons` | Expected operational/data reason objects |
| `metadata` | Artifact identity and relevant preparation/time/unit details |

`SegmentError(code, message)` represents an expected availability problem. `unavailable(...)` creates a common result. Unexpected programming errors are not all swallowed by the predictor.

Database execution adds `customer_id`, `persisted`, and a membership result. Persisted JSON also records `membership_policy` and `membership`; source feature frames and full purchase histories are not saved in the result.

Defaults are `persist=True`, `update_memberships=False`. With persistence, `update_or_create` replaces the one current result. With `persist=False`, prediction can run but no result or membership rows are written. In a dry run, the membership result is not an evaluation of any existing audience membership.

An unavailable result does not mean a probability or amount of zero. Consumers must check status and nullable prediction before displaying or aggregating values. Current-result GET requests return stored results; they do not rescore customers.

## 12. Membership rules

Prediction and membership are separate:

- Churn prediction supplies class and probability; a requested membership compares its probability to the supplied threshold.
- CLV prediction supplies money; a requested membership compares its GBP amount to the supplied threshold.
- Comparison is `prediction >= threshold` before membership-score rounding.
- Churn threshold must be between 0 and 1. CLV threshold is nonnegative. HTTP thresholds allow up to 24 digits with 8 decimal places.
- No default audience threshold is supplied by this implementation.
- Membership changes require both persistence and `update_memberships=True` with an explicit threshold.

Qualifying customers are upserted into `customer_segments`. Churn score is stored with four decimal places. CLV membership score is null: a GBP amount is not placed in the probability-style score field. Its monetary value remains in prediction JSON.

Below-threshold customers and defined data-ineligible customers have the previous membership removed only when updates are requested. The current data-failure set is:

```text
data_unavailable, unsupported_population, missing_unit_price,
inconsistent_purchase_amount, inconsistent_source_identity,
inconsistent_purchase_date, inconsistent_tenant_history, conflicting_invoice
```

Infrastructure/model/registry failures, and other failures outside that set, do not erase a previous membership. The result may still become unavailable while the old membership remains. `membership.updated=False` and `is_member=null` mean no new membership decision was applied, not that the customer is definitely a nonmember.

If a request does not ask for membership updates, existing memberships remain untouched even when the new prediction differs. Stored membership metadata is the snapshot of that execution; `customer_segments` remains the current audience source if another process later changes memberships.

The canonical schema's `workflow_executions.segment_id` references a `customer_segments` membership ID with `ON DELETE SET NULL`, not a platform `segments.id`. Removing a membership can therefore clear that existing workflow reference through the database's own constraint. Preserve this distinction when integrating campaigns or changing membership deletion behavior.

## 13. Transactions, repeat runs, and concurrency

Each database execution is atomic. Persisted PostgreSQL runs take a transaction advisory lock derived from store, segment, and customer identity. Batch API/CLI execution additionally wraps the whole ordered customer list in a transaction. IDs are sorted before execution to keep lock order consistent.

- Repeated calls update the same unique result instead of inserting duplicates.
- A request/database failure rolls back the batch. A normal customer-level unavailable result is not an exception and can be persisted alongside other outcomes.
- An older CLV observation date cannot overwrite a newer stored cutoff. Use a nonpersistent run for an older historical evaluation.
- Churn has no invented observation date; its `computed_at` says when the backend ran, not when the original source snapshot was observed.
- A successful predictor output is checked against the active registry before persistence; an incorrect/missing/multiple-active registration becomes unavailable.
- Registry initialization also uses a PostgreSQL transaction lock to serialize activation.

These are synchronous operations. There is no queue, retry worker, incremental job scheduler, input-fingerprint cache, or exactly-once delivery protocol. The database lock/unique key prevents duplicate current rows; it does not implement a general distributed processing system.

## 14. Authentication and tenant isolation

The API currently uses administrator-issued service tokens, not completed end-user signup/login, Django user-table authentication, Supabase Auth JWTs, or Supabase API keys.

`issue_token(store_id)` signs a payload containing the store UUID and purpose `segments`, using Django timestamped signing and the dedicated salt `customerly.segments.service-access.v1`. It refuses the insecure development fallback key. The management command checks store existence before issuing a token.

Requests use `Authorization: Bearer TOKEN`. The custom authenticator verifies the signature, purpose, UUID, and age. Maximum age defaults to 3600 seconds through `getattr(settings, "SEGMENTS_TOKEN_MAX_AGE", 3600)`; the current code does not expose a separate environment-variable parser for that setting. Invalid, malformed, expired, or wrong-purpose tokens are rejected.

The authenticated principal carries one store ID. The view checks that store exists and filters all customers/results by it. A request-body store ID does not change that scope. If any requested customer belongs elsewhere or does not exist, the entire batch is rejected before scoring; the API does not identify which other store owns the UUID.

Tokens are service credentials with access to the implemented operations for their store; there are no per-user roles or separate read/write token scopes yet. Issue tokens only from trusted administrative terminals. Use HTTPS outside local development. Keep the signing key private; rotating it invalidates existing tokens under the current configuration. A token issued by one backend must be verified using that backend's intended signing secret.

The eventual account system should replace/extend the authenticator while preserving authorized store resolution and every repository filter. Do not replace authentication with a client-supplied `store_id`.

## 15. HTTP API

Root routing mounts `apps.segments.urls` under `/api/`; its URL namespace is `segments`.

| Method/path | Behavior |
| --- | --- |
| `GET /api/segments/` | Catalog for the two configured keys, IDs, registration status, descriptive requirements; not customer-level eligibility |
| `POST /api/segments/<key>/run/` | Read, check, predict, and optionally persist a bounded explicit customer list |
| `GET /api/segments/<key>/results/` | Current stored results for the token's store |
| `GET /api/customers/<uuid>/segments/` | Stored results for one accessible customer, ordered by key |

### Run request

| Field | Default / validation |
| --- | --- |
| `customer_ids` | Required, 1–100 unique UUIDs |
| `observation_date` | Required ISO date for CLV; not needed for churn |
| `currency` | Required `GBP` for CLV |
| `persist` | True |
| `update_memberships` | False |
| `membership_threshold` | Optional unless updates requested; model-specific bounds |

```json
{
  "customer_ids": ["11111111-1111-4111-8111-111111111111"],
  "persist": true
}
```

The UUID is a placeholder. For CLV add `observation_date` and `currency`:

```json
{
  "customer_ids": ["11111111-1111-4111-8111-111111111111"],
  "observation_date": "2011-12-09",
  "currency": "GBP",
  "persist": true,
  "update_memberships": false
}
```

To request memberships, add `update_memberships: true` and a suitable `membership_threshold`. A value such as `0.70` is a churn probability rule; it would mean GBP 0.70 for CLV. Do not reuse the same numerical threshold without its model-specific meaning.

The run response has `segment`, `count`, and `results`. Each result contains its customer UUID, persistence flag, prediction/status, eligibility/reasons, metadata, and membership details. A mix of predicted/unavailable customers can return HTTP 200. Result entries are processed in sorted UUID order, not guaranteed to match request order; associate them by `customer_id`.

### Reading results

List endpoints accept `limit` (default 25, maximum 100), `offset` (default 0), and optional `status=predicted|unavailable`. They return total count, paging values, and rows ordered by customer UUID. Rows include `computed_at`, `observation_date`, and saved result data.

A customer with no stored predictions returns an empty results list. Reading Customer 360 data must not trigger a model run implicitly.

### HTTP outcomes

| Status | Meaning |
| --- | --- |
| 200 | Valid catalog, read, or completed run, including normal unavailable customer results |
| 400 | Invalid fields, duplicate/too many IDs, invalid thresholds, missing CLV context, or rejected stale persistent cutoff |
| 401 | Missing/invalid/expired service token |
| 404 | Unknown key or inaccessible/nonexistent store/customer |
| 503 | Database/storage problem; POST also uses this when its definition has not been initialized |

The catalog can show an uninitialized key with `registered=false` and null ID/requirements. The known-key stored-results view can return an empty list even if its definition has not been initialized. Do not treat GET catalog success alone as full model/database readiness.

## 16. Management commands and setup

Run these PowerShell commands from the repository root. Placeholder UUIDs must be replaced with existing store/customer IDs.

If no virtual environment exists, first install/select Python 3.13.3 and create `.venv` with that interpreter. Verify its exact version before installing dependencies; choosing any Python 3.13 patch release does not satisfy the current strict metadata check automatically.

```powershell
.venv/Scripts/python.exe -m pip install -r backend/requirements/dev.txt
.venv/Scripts/python.exe backend/manage.py check
```

Supply private environment configuration before database operations. The canonical schema/data must exist already. Do not blindly execute the original schema loader against an initialized shared database.

```powershell
.venv/Scripts/python.exe backend/manage.py migrate segments --plan
.venv/Scripts/python.exe backend/manage.py migrate segments
.venv/Scripts/python.exe backend/manage.py initialize_segments
```

Initialization changes segmentation metadata/active model registration. It is not a read-only health check, and it is not performed automatically by catalog GETs.

Issue a service token; the command prints a secret intentionally, so run it privately:

```powershell
.venv/Scripts/python.exe backend/manage.py create_segment_token --store-id STORE_UUID
```

Run explicit customers from the terminal:

```powershell
.venv/Scripts/python.exe backend/manage.py run_segments --segment churn --store-id STORE_UUID --customer-id CUSTOMER_UUID
.venv/Scripts/python.exe backend/manage.py run_segments --segment clv --store-id STORE_UUID --customer-id CUSTOMER_UUID --observation-date 2011-12-09 --currency GBP --dry-run
```

Repeat `--customer-id` for multiple customers, up to the same 100-ID bound. The command validates that every customer belongs to the selected store and runs the batch atomically. It does not select all customers or only new customers automatically. CLI execution is an administrative operation and does not require an API token.

Use `--update-memberships --membership-threshold VALUE` for explicit audience updates. `--dry-run` sets persistence false and cannot be combined with membership updates.

Only when reconstructing the existing churn sidecar offline, run from `backend/`:

```powershell
../.venv/Scripts/python.exe -m apps.segments.predictors.churn.recover_preprocessing
```

This command fits only preprocessing transformers on the checksum-verified original data, verifies the existing classifier, then writes the JSON. It must not be called on uploaded customer data or automatically during requests. It does not retrain either saved model.

For local HTTP use:

```powershell
.venv/Scripts/python.exe backend/manage.py runserver
```

`runserver` is a development server. Normal prediction does not need training notebooks or raw training files; the recorded reference/maintenance checks do.

## 17. Errors and troubleshooting

| Reason / symptom | First checks |
| --- | --- |
| `model_unavailable`, `model_load_failed` | Trusted artifact exists and can be loaded; never point the loader at arbitrary user files |
| `incompatible_environment` | Exact recorded Python/package versions |
| `invalid_artifact` | Correct model type, input order, metadata, fitted state, and prediction contract |
| `model_preparation_unavailable`, `invalid_preparation` | Churn JSON present, correct format/statistics/version metadata, bound to the same classifier digest |
| `model_registry_mismatch` | Exactly one active matching version and full digest; explicitly initialize after a model change |
| `data_unavailable` | Nested eligibility reasons, required values/history; do not replace null results with zero |
| `unsupported_population` | Source population separation; no artificial retail history for churn/coupon/message customers |
| `unsupported_currency` | Pure CLV input currency; HTTP/database service require GBP explicitly |
| `missing_unit_price`, `inconsistent_purchase_amount` | Original price and quantity/amount semantics; no inferred gross/net or currency conversion |
| `inconsistent_tenant_history`, `inconsistent_source_identity` | Broken/mixed store or source relationships |
| `inconsistent_purchase_date`, `conflicting_invoice` | Invoice/line calendar-date integrity |
| `history_limit_exceeded` | Full history exceeds the synchronous cap; add a deliberate larger/background path rather than truncate |
| `invalid_observation_date`, `invalid_purchase_input` | Pure-input shape and explicit source-local date |
| `prediction_failed`, `invalid_prediction` | Prepared input compatibility and finite/valid model outputs |
| 401 | Correct server-issued Bearer token, signing secret, purpose and lifetime |
| 503 | Database connectivity, permissions, migrations, and initialized definitions |
| Older cutoff rejected | Use `persist=false` to inspect an older period without replacing current state |

`SegmentError` is converted to a structured unavailable result at the appropriate boundary. Invalid request/configuration `ValueError`s become HTTP 400 in the run view. Database exceptions become a generic 503 response rather than exposing SQL/credentials. Unexpected programming/validator errors remain real errors to fix.

## 18. Tests and verification

From the repository root:

```powershell
.venv/Scripts/python.exe backend/manage.py check
.venv/Scripts/python.exe backend/manage.py makemigrations --check --dry-run --settings=config.settings.test
Push-Location backend
../.venv/Scripts/python.exe -m pytest apps/segments/tests config/tests/test_startup.py -q
# Full backend suite:
../.venv/Scripts/python.exe -m pytest -q
Pop-Location
```

Pytest selects `config.settings.test` through `backend/pytest.ini`. It overrides the shared database with in-memory SQLite and a test signing secret. Do not run automated database tests with shared Supabase settings. Django's separate test runner must likewise use `--settings=config.settings.test`.

| Test file | What it checks |
| --- | --- |
| `test_eligibility.py` | Presence/missingness, zero/false, invalid scalars/numbers, configured validators, supported missing values, history, multiple policies, and reason privacy |
| `test_fitted.py` | Saved-statistic application, unchanged inputs, batch independence, index/order preservation, invalid inputs/statistics |
| `test_predictors.py` | Existing artifact loading, notebook preparation parity, prediction parity, date/window rules, same-day/missing intervals, no refitting, churn sidecar binding, output validation, and inference without database access |
| `test_database_api.py` | Isolated canonical fixtures, idempotent registration, source reads, persisted unavailable/available results, dry runs, memberships, authorization/expiry, cross-store rejection, result paging, amount integrity, stale dates, registry failures, and rollback |
| `config/tests/test_startup.py` | App registration, entry points, health, mapping command integration, and environment-setting behavior |

Predictor tests extract only selected original pure preparation-function definitions from the notebook. They do not execute notebook training/export cells. Tests may load the trusted existing artifacts but never replace them. SQLite fixtures are test-owned copies of the required table shapes, not copies of live customer data.

The implementation previously passed 213 backend tests and live Supabase verification: one existing customer per predictor was scored and persisted; repeat calls updated the same current row; retrieval endpoints worked; selected customer/order/item fingerprints stayed unchanged; and PostgreSQL result-table RLS/cascading foreign keys were verified. Those live checks are separate from the automated suite and are not rerun simply by reading this document.

During this documentation review on 16 November 2026, the current full backend suite reported **241 passed, 34 skipped**, with 3 passing subtests and 6 existing date-inference warnings. The skipped cases belong to the analytics KPI database tests, which require an explicitly configured `KPI_TEST_DATABASE_URL`. They were not counted as passing live database verification. No live Supabase queries or writes were needed for this documentation review.

Use a dry run first for a new live integration. Any live write smoke test must be explicitly scoped to selected customers and its derived outputs; do not bulk-score or change audiences just to verify connectivity.

## 19. Adding the next segment

### 19.1 Establish its contract before writing database queries

Read its source notes, current training notebook or rule definition, actual artifact, canonical schema, and relevant KPI requirements. Record:

- Stable runtime key and unique display name.
- ML or rule-based execution type.
- Prediction unit/grain: customer, customer-product, order, session, or another entity.
- Exact ordered inputs, meanings, units, accepted types, exclusions, and supported missingness.
- Fitted preparation and its saved state; do not learn an encoder/scaler on uploaded data.
- Time window, cutoff boundaries, future horizon, timezone/date conventions, and eligibility.
- Output structure, numeric/categorical meaning, postprocessing and valid ranges.
- How optional audience membership should be decided and which unavailable reasons invalidate it.
- Artifact identity, dependency versions, training metadata, and source/processed parity evidence.

Do not infer missing information from a filename or generalize churn/CLV rules to the next model. Resolve storage grain and unavailable-data behavior explicitly.

### 19.2 Create its predictor package

```text
backend/apps/segments/predictors/new_segment/
├── __init__.py
├── contract.py
├── features.py
└── predictor.py
```

Add a preparation loader only if its fitted preparation is not already inside its artifact. Keep import-time behavior free of model loading, queries, training, and writes.

Define an `EligibilityPolicy` using logical model-input identifiers. Feature preparation should accept explicit Python data, preserve originals, construct only the required representation, and return unavailable reasons instead of inventing unknown history or outcomes. The predictor should return the shared `PredictionResult` shape and validate outputs before marking them predicted.

### 19.3 Update every current two-model dispatch point

The implementation is not yet a general plugin registry. Review all of these places:

| File / symbol | Required extension |
| --- | --- |
| `services/model_loader.py: ARTIFACTS` | Add the trusted artifact filename, or explicitly design the no-artifact path for a rule-based predictor |
| `services/catalog.py: DEFINITIONS` | Add its unique runtime key, name and description |
| `services/catalog.py: initialize_catalog` | Add correct validation, required-fields metadata, metrics, timestamps and model type; currently the non-churn branch assumes CLV and `segment_type` is `ml` |
| `services/runner.py: run_segment` | Add dispatch and appropriate logical arguments; unknown keys currently fail |
| `services/database.py` | Implement verified, store/customer-scoped canonical reads and unit/source checks |
| `services/execution.py: execute_customer` | Add source-input dispatch; the current non-churn branch assumes CLV |
| `services/execution.py: membership handling` | Add its output-to-membership rule; the current numeric selector assumes churn probability or CLV revenue |
| `services/execution.py: data_failures` | Decide which new unavailable reasons invalidate an existing membership and which preserve it |
| `serializers.py: RunSerializer` | Validate its request context, thresholds and units; the current non-CLV threshold branch assumes churn-style bounds |
| `management/commands/run_segments.py` | Add its key to CLI choices and add any required context flags |
| API catalog/results conventions | Ensure new outputs and filters remain understandable and tenant-scoped |
| Tests and documentation | Add module/parity/database/API coverage and its contract/usage notes |

Do not merely add a third key to `DEFINITIONS`: without the other changes it can be routed through CLV-specific code or get an incorrect threshold rule. Refactor these branches into an explicit predictor registry/protocol when expanding them, with clear per-segment adapters and policies.

The existing `<key>` URL patterns can usually be reused; a new Django app or route per model is not inherently necessary. New request fields or another output grain may require deliberate endpoint/schema changes.

### 19.4 Decide persistence and grain

The current managed table stores one current result per customer/segment/store. It works for the two current customer-level outputs. Do not assume it is sufficient for per-product replenishment, multiple recommendations, session predictions, or historical runs.

For customer-product replenishment, the project already records a conflict between product-level features and the customer-only `customer_features` grain. Decide whether a structured result payload and explicit product identities are enough, or whether a normalized child/result table is required. Do not average product-level features into a customer row merely to fit an existing table.

Category/channel predictions also need their own output/membership rule. The current threshold-based execution is not an automatic fit for them. Define how KPIs and campaigns consume those outputs, and keep money/counts/categories separate from probability-style membership scores.

Use an additive migration for new managed storage. Preserve existing canonical source fields and RLS/tenant boundaries. Initialization, migrations and actual prediction are separate operations.

### 19.5 Register and verify

1. Validate the predictor purely with synthetic/reference inputs and its trusted artifact.
2. Compare backend feature values and predictions with its training/rule workflow. Do not train during prediction or unit tests.
3. Verify support for missing, unavailable, invalid, and boundary-date cases.
4. Apply any reviewed new migrations to an appropriate database.
5. Extend and explicitly run `initialize_segments`. Remember that existing `required_fields` metadata is preserved; deliberately migrate/update it if the contract changes.
6. Verify exactly one active matching artifact registration for ML predictors. Do not fake an ML artifact/version just to route a rule-based segment through existing code.
7. Exercise a dry run through the database path with authorized customer IDs.
8. Exercise scoped result persistence, idempotency, retrieval, and optional memberships.
9. Confirm source values are unchanged and another store's IDs cannot be read or scored.
10. Run the full backend tests, system checks, and migration consistency check; update this guide and the app README.

Adding only another Python package should not add another `INSTALLED_APPS` entry. Review model cache capacity/memory for nine artifacts; the current capacity of four can cause reloads as more models are used.

### 19.6 Minimum acceptance checks for a new predictor

- Exact input order and fitted transformations; category mappings do not depend on the request batch.
- Meaningful units, precision, timestamps, source identity, and no target leakage.
- Legitimate zeros/false values versus missingness.
- Entirely unavailable required data versus supported individual missing values.
- No future rows in historical features; correct window endpoints.
- Valid output shape/range/unit and documented postprocessing.
- No source mutation, import-time I/O, inference-time fitting, or untrusted model paths.
- Customer/store isolation at every new join, including products/sessions/messages.
- Registry version identity and safe replacement of current outputs.
- Correct audience removal/preservation behavior for new failure codes.
- Atomic batch rollback, repeat-run behavior, stale-date handling where relevant, and dry-run no writes.
- HTTP/CLI validation and consumer documentation for its result shape.

## 20. Changing a model version

Replacing a `.pkl` is not automatically a compatible deployment. Check its input/feature/preparation/output contracts, library versions, unit/time semantics and parity first.

If only weights change under the same contract, deploy the trusted artifact, explicitly initialize the registry, and rerun selected customers. The local file digest and active registry must agree. Existing current results are not automatically rescored or invalidated when a file changes; their stored metadata identifies the artifact that produced them.

If churn's classifier changes, its old JSON is deliberately rejected because it is bound to the previous classifier digest. Export/verify the new model's matching fitted preparation rather than reusing the old statistics blindly. The current recovery command is specific to the original dataset/notebook and is not a general retraining/export tool.

If features or output meaning change, update the predictor, database adapter, contracts, tests, catalog metadata, and consumer integration together. Initialization preserves existing definition metadata, and its model-version conflict update does not rewrite the original registered `trained_at`. Handle corrections deliberately.

For an additional rule-based segment, design registration/readiness without forcing `.pkl` loading; the current initializer and registry-match path assume both implemented predictors are ML artifacts.

## 21. Remaining integration work

Implemented segmentation does not mean the whole platform upload workflow is implemented. The remaining work includes:

- Ingestion API/coordinator to handle uploads, mapping approval, source-specific cleaning and validated canonical writes.
- Tracking affected customers and invoking segmentation after committed source changes, with explicit cutoff/currency rules.
- Optional background scheduling, batching across an entire store, retries and operational monitoring.
- Store account/login authorization replacing or integrating the service-token boundary.
- Customer 360 assembly in `analytics`, frontend display, ML-prediction KPI consumption, and campaign audience consumption. The 19 historical FYP1 KPI cards already exist; do not reimplement them as part of segmentation.
- The other seven predictor implementations and any grain-specific storage changes.
- Prediction history or last-successful/last-attempt separation if required by product behavior.

An ingestion coordinator should call shared Python services inside the backend; it does not need to make an HTTP request to a sibling Django app. Use post-commit triggering so segmentation does not observe uncommitted source data. Decide incremental selection/retry semantics before promising automatic new-rows-only processing.

Customer 360 should read stored results with their status, computation time, cutoff, model identity, and units. Displaying a profile should not silently rerun the models, and a missing/unavailable result must not be shown as zero.

Existing historical KPI names must not be confused with model output: the analytics churn-rate card uses the source's recorded `customers.churn` label, and historical CLV is computed from purchases. Those are different from predicted churn probability and future 90-day purchase revenue in `segment_predictions`. The inspected KPI code does not currently read that prediction table.

## 22. References

- [Segmentation app README](../../../backend/apps/segments/README.md): operator/developer usage guide.
- [Backend setup](../../../backend/README.md) and [environment template](../../../backend/.env.example).
- [Architecture overview](../../Overview.md): domain split, including `segments` and `analytics`.
- [Canonical SQL](../../../backend/schema.sql) and [final schema notes](../../notes/schema-notes/final-schema.md).
- [Segment-to-schema mapping](../../notes/customer%20segmentation-notes/segments-schema-mapping.md) and [nine-segment overview](../../notes/customer%20segmentation-notes/segments-overview.md).
- [Transformation contract](../../notes/data_transformation_notes/transformation-contract.md): population identity, dates, money, preservation, and unsupported data.
- [KPI mapping](../../notes/kpi-notes/kpi-schema-mapping.md): downstream data requirements.
- [Churn training implementation/results](churn-risk-model-training-and-results.md) and [original notebook](../../../data_prep/training/churn_prediction.ipynb).
- [CLV training implementation/results](customer-lifetime-value-model-training-and-results.md) and [original notebook](../../../data_prep/training/clv_prediction.ipynb).

The training documents describe the saved experiments; this document describes their backend use. Historical notebook labels are not instructions to reintroduce a runtime baseline comparison or experimental opt-in gate. When descriptions disagree with later code, inspect the named implementation and update the affected notes explicitly rather than silently assuming a behavior exists.
