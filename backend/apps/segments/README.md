# Segmentation app

This app checks whether a customer's data can be used by a segmentation model
and prepares inputs for already trained models. CLV uses the project's final
saved model, and churn uses its recovered training statistics. Uploading a
store's data does not automatically train a new model.

All nine planned segmentation types belong in this one Django app. Each gets
its own package because its required data, preparation steps, and prediction
can be different. Shared checks belong in `services/`.

For now, the app has implementations for **churn** and **CLV**, a shared
eligibility engine, model loading, database execution, result storage, and APIs.

## What is ready and what is still pending

| Part | Current state |
| --- | --- |
| Django app registration | Done: `apps.segments.apps.SegmentsConfig` |
| Separate churn and CLV packages | Implemented, with the readiness limits below |
| Shared eligibility checks | Implemented and tested |
| Churn and CLV eligibility policies | Defined from the current training contracts |
| Reading customer data from Supabase | Connected through store-scoped database adapters |
| Preparing inputs and loading existing `.pkl` files | Implemented; no fitting or artifact changes |
| Running CLV predictions | Available directly through the final saved model |
| Running churn predictions | Available using the unchanged classifier and verified saved preprocessing |
| Shared execution | Available through `services.runner.run_segment` |
| Database execution | Available through `services.execution.execute_customer` |
| Saving predictions or memberships | Implemented; memberships require an explicit threshold |
| API endpoints | Implemented with expiring, store-scoped Bearer tokens |

Existing canonical tables are mapped as unmanaged Django models. Only the new
`segment_predictions` table is managed by this app. Importing predictor packages
does not load model files or open a database connection.

## Current structure

```text
segments/
├── __init__.py
├── apps.py
├── README.md
├── authentication.py
├── models.py
├── serializers.py
├── urls.py
├── views.py
├── management/
│   └── commands/
│       ├── initialize_segments.py
│       ├── create_segment_token.py
│       └── run_segments.py
├── migrations/
│   ├── 0001_initial.py
│   └── 0002_prediction_security.py
├── predictors/
│   ├── __init__.py
│   ├── churn/
│   │   ├── __init__.py
│   │   ├── contract.py
│   │   ├── features.py
│   │   ├── preparation.py
│   │   ├── recover_preprocessing.py
│   │   └── predictor.py
│   └── clv/
│       ├── __init__.py
│       ├── contract.py
│       ├── features.py
│       └── predictor.py
├── services/
│   ├── __init__.py
│   ├── eligibility.py
│   ├── catalog.py
│   ├── database.py
│   ├── execution.py
│   ├── fitted.py
│   ├── model_loader.py
│   ├── results.py
│   ├── runner.py
│   └── validation.py
└── tests/
    ├── __init__.py
    ├── test_eligibility.py
    ├── test_fitted.py
    ├── test_database_api.py
    └── test_predictors.py
```

## How this app fits into the upload flow

The intended complete flow is:

```text
Store uploads data
    → Mapping identifies the uploaded columns and applies approved mappings
    → Preprocessing cleans the data using the agreed rules
    → The ingestion process saves the prepared data
    → Segments reads the customer's data
    → Eligibility checks whether the selected model has enough usable data
    → The predictor prepares inputs and runs the saved model
    → Results are saved for Customer 360, KPIs, and campaigns
```

This is the intended integration, not a statement that every stage is already
connected across the wider platform. Within this app, database reads, prediction,
persistence and retrieval APIs are implemented. The low-level runner still
accepts Python inputs directly for independent testing.

Mapping handles different uploaded column names. The eligibility engine does
not repeat that matching or call the LLM. It checks whether the required data
is actually available after mapping and preparation.

Preprocessing cannot create purchase history or an attribute that was never
supplied. A customer can therefore qualify for churn while being unavailable
for CLV, or the other way around. The checks are per customer and per policy;
one customer's result does not decide eligibility for every customer in a store.

## How the eligibility engine works

The function is `check_eligibility` in `services/eligibility.py`. It takes:

1. **Values for one customer**, supplied as a dictionary.
2. **An eligibility policy**, describing the values and history that must exist.
3. **History counts**, when the policy requires them.

The function uses the input identifiers given in the policy. These identifiers
are not hardcoded database columns. Later, the database-reading code will
provide the verified data under those identifiers. The trained model determines
what it needs; the database determines what is available.

### Checking values

For each `ValueRequirement`, the engine checks the following in order:

1. **Was the input supplied?** An absent dictionary key is reported as
   `missing_input`.
2. **Is it one value?** A list, dictionary, or other collection is rejected
   because this requirement expects one value for one customer.
3. **Is it missing?** `None`, blank or whitespace-only text, and pandas/NumPy
   missing values such as `NaN`, `pd.NA`, and `NaT` are treated as missing.
4. **Is a numeric value finite and real?** Infinity and complex numbers are
   rejected.
5. **Does it satisfy any extra rule?** A configured `validator` can check the
   expected type, range, or other model-specific conditions.

`0` and `False` are valid present values. They are not treated as missing.
Extra inputs that the policy does not ask for are ignored.

Type, range, and unit rules are not inferred automatically. They must be
configured for the relevant model. Without an extra validator, a present text
value is not automatically converted into a number or rejected for being text.

### When a missing value is allowed

Missing values are rejected by default. A requirement can use
`allow_missing=True` only when the trained model's saved preparation supports
that missing value.

For example, a model may have a fitted imputer that handles a missing computed
feature. The eligibility engine can allow that feature's value to be `None`
and report it in `allowed_missing`.

This does not fill the value. The fitted preparation will handle it later.
An entirely absent input key still fails, even with `allow_missing=True`.
The data-preparation code must supply the expected input explicitly.

### Checking history

A `HistoryRequirement` defines a minimum number of valid records. The engine
checks whether a count was supplied, whether it is a nonnegative integer, and
whether it reaches the required minimum. A Boolean is not accepted as a count.

The engine does not read orders, inspect dates, or count purchases itself.
The code preparing the data must first apply the model's valid-purchase rules
and date window, then supply the resulting count. Counting all uploaded rows
without those checks would not establish eligibility.

### Making the decision

All requirements in the policy must pass. The engine returns every detected
data issue, so the caller can explain why that customer's segment is unavailable.
Policies with no requirements, duplicate keys within the same requirement group,
or invalid requirement settings are rejected as configuration errors.

If a custom validator raises an exception, that error is allowed to reach the
caller. A broken rule should be fixed rather than reported as missing customer
data.

### What the result contains

Call `result.as_dict()` to get these fields:

| Field | Meaning |
| --- | --- |
| `eligible` | `True` when every configured check passes; otherwise `False` |
| `reasons` | The input identifier, reason code, and explanation for each issue |
| `allowed_missing` | Inputs whose missing values were explicitly permitted |

The available reason codes are:

| Code | Meaning |
| --- | --- |
| `missing_input` | A required input was not supplied |
| `missing_value` | The input exists but has no usable value |
| `invalid_value` | The value is a collection, an invalid number, or fails its extra rule |
| `missing_history` | A required history count was not supplied |
| `invalid_history` | The history count is not a valid nonnegative integer |
| `insufficient_history` | The valid history count is below the required minimum |

The engine does not change the customer's values. Returned reasons do not
include those values.

### A small code example

The names below are example input identifiers, not table or column names:

```python
from apps.segments.services.eligibility import (
    EligibilityPolicy, HistoryRequirement, ValueRequirement, check_eligibility,
)

policy = EligibilityPolicy(
    values=(ValueRequirement("input_a"),),
    histories=(HistoryRequirement("window_a", minimum_records=1),),
)
result = check_eligibility(
    {"input_a": 0}, policy, history_counts={"window_a": 0},
)
print(result.as_dict())
```

The result is:

```json
{
  "eligible": false,
  "reasons": [
    {
      "key": "window_a",
      "code": "insufficient_history",
      "message": "Not enough validated history records."
    }
  ],
  "allowed_missing": []
}
```

The value `0` passed its availability check. The history requirement failed
because it needed one valid record and received a count of zero. With a count
of one, this example would be eligible.

The engine only returns this decision. The predictor called by `run_segment`
uses it to skip unavailable customers. It also checks that the saved model and
preparation match their required input and dependency contracts.

## Churn

The churn model was trained to predict the source dataset's churn classification.
Its implementation is in `predictors/churn/`. The saved model is
`backend/ml_models/churn_model.pkl`.

The notebook uses six customer inputs: tenure, distance from warehouse to home,
number of registered devices, satisfaction score, complaint status, and cashback
amount. These describe the training inputs, not implemented database mappings.
The churn label itself is not an input.

The `.pkl` contains only the estimator. Its missing preprocessing statistics
have now been reconstructed from the checksum-verified original training CSV
using the notebook's exact preparation order, without retraining or changing
the classifier. They are saved in `backend/ml_models/churn_preprocessing.json`.

Recovery checks matched the notebook's saved scaled preview, verified full
transformation and missing-value handling, and reproduced the saved ROC-AUC
of 0.9050 on the same 603 evaluation rows. The JSON records the source,
notebook and classifier checksums, dependency versions, and verification results.
These checks establish reproduction of the existing workflow, not a new
independent model evaluation.

The segmentation function `apps.segments.services.fitted.apply_fitted_numeric` fills
permitted missing values with the saved training medians and scales each input
using the saved training mean and standard deviation. It never learns values
from an upload. The same customer therefore gets the same prepared inputs
whether supplied alone or alongside other customers.

The source does not establish a verified future prediction period or tenure
unit. The implementation does not invent either. The current code validates
and orders inputs, checks the saved classifier and matching preprocessing,
applies the saved transformation, then returns the predicted class and churn
probability. If the saved preparation is missing, mismatched, or invalid, it
returns an unavailable result instead of scoring unscaled data.

The original notebook fitted preprocessing before splitting and selected the
model using its evaluation partition. Recovery preserves those limitations;
it does not establish probability calibration or campaign effectiveness.

Supply a dictionary with these exact **model input labels**:
`Tenure`, `WarehouseToHome`, `NumberOfDeviceRegistered`, `SatisfactionScore`,
`Complain`, and `CashbackAmount`. These are not hardcoded database mappings.
Values must already be numeric, with `Complain` accepting a Boolean or 0/1.
Strings are not silently converted. Present missing values for `Tenure` and
`WarehouseToHome` are permitted because those inputs had missingness in the
original source and are handled by the reconstructed imputer. The other four
inputs must be usable. Absent keys still fail eligibility, and an entirely
empty customer profile is not filled and predicted.

To reproduce the verified JSON again, run from `backend/`:

```powershell
../.venv/Scripts/python.exe -m apps.segments.predictors.churn.recover_preprocessing
```

This is an offline maintenance command. It reads the original training source
and notebook, reconstructs transformers, evaluates the existing classifier,
and writes the JSON only after verification. It never trains a classifier,
runs the notebook's training cells, changes either `.pkl`, or processes uploaded
customer data. Prediction itself requires only the saved files, not the raw
training dataset or notebook.

See [churn training notes](../../../docs/implementation/segmentation/churn-risk-model-training-and-results.md).

## CLV

CLV estimates a customer's future purchase value. Its implementation is in
`predictors/clv/`. The saved model is
`backend/ml_models/clv_model.pkl`.

The artifact bundles a pipeline, feature order, metadata, and prediction policy.
It estimates 90-day purchase revenue from a 288-day history. Its recorded status
is retained in its metadata exactly as saved by the training notebook. That
historical evaluation label does not block prediction or require opt-in. The
runner uses the final `.pkl` directly after input and compatibility checks;
it does not compare against or substitute a baseline at runtime.

Its eleven features describe purchase recency, frequency, total spending,
average order value, tenure within the history window, items bought, distinct
products bought, maximum and minimum invoice values, average gap between
orders, and variation in those gaps. They must be computed from the correct
purchase history; they are not simply eleven values taken from one uploaded row.

Some interval features are legitimately missing for customers with few orders.
The saved pipeline supports those missing features. No valid purchase history
is a different situation and should produce an unavailable result, not an
invented zero prediction. The current model's monetary output is in GBP and
must retain its currency and prediction period when integration is added.

The feature builder filters cancellation invoices, accounting/manual/test
product codes, missing identifiers, invalid dates, nonpositive or fractional
quantities, and nonpositive/invalid prices. Repeated valid lines are retained;
legitimate postage and carriage codes are not automatically removed. Reasons
for excluded lines are counted without returning customer values.

History is `[observation date - 288 days, observation date)`. The start is
included and the observation date is excluded. Inputs use source-local calendar
dates; supply an explicit timezone-free date at midnight. No machine-current
date is substituted. Valid lines are aggregated into complete invoices before
computing order-value features. Conflicting dates within an invoice are
reported rather than silently choosing a date. Interval variation uses sample
standard deviation (`ddof=1`), preserving missing intervals and same-day zeros.

The pipeline predicts once for one customer's eleven features. Negative
estimates are clipped to zero using the saved prediction policy. Missing
history, invalid outputs, unsupported currencies, incompatible dependencies,
and unavailable artifacts return structured unavailable results.

See [CLV training notes](../../../docs/implementation/segmentation/customer-lifetime-value-model-training-and-results.md).

## Adding the remaining segments

Add another package under `predictors/` when its implementation is ready.
The existing eligibility engine and runner can be extended for other predictors.
Model selection/version management and persistence remain future work.
Each predictor retains its own input and output contract.
The current database mappings were checked against the live Supabase schema.
Only the two implemented predictors are registered; the other seven remain future work.

## Calling the runner without a database

Run Python from `backend/` using the project's virtual environment:

```python
from apps.segments.services.runner import run_segment
from apps.segments.predictors.clv.features import PurchaseLine

result = run_segment(
    "clv",
    purchases=[
        PurchaseLine("invoice-1", "00123", "2011-12-01", 2, "10.50"),
        PurchaseLine("invoice-2", "00456", "2011-12-05", 1, "8.25"),
    ],
    observation_date="2011-12-09",
    currency="GBP",
)
print(result.as_dict())
```

`PurchaseLine` has five logical fields: `invoice_id`, `product_code`,
`timestamp`, `quantity`, and `unit_price`. Identifiers must be strings so
leading zeros survive. Numeric quantity and price strings are parsed using
decimal arithmetic. This is a Python input format, not a database schema.

Each call must contain only one customer's records from one tenant and source
population. The future database adapter is responsible for that isolation;
this database-free function cannot verify ownership. Supply source product and
invoice identifiers that retain the training policy's cancellation/code
meaning, rather than opaque database UUIDs.

For churn:

```python
result = run_segment("churn", values={
    "Tenure": 15,
    "WarehouseToHome": 29,
    "NumberOfDeviceRegistered": 4,
    "SatisfactionScore": 3,
    "Complain": False,
    "CashbackAmount": 143.32,
})
print(result.as_dict())
```

This returns `status="predicted"` when the saved model and matching JSON are
available. Its prediction contains `predicted_class` and `churn_probability`.
There is no option to bypass saved preprocessing if it is unavailable.

The shared result contains `segment`, `status`, `prediction`, `eligibility`,
`reasons`, and `metadata`. Only `status="predicted"` carries a prediction.
CLV predictions contain `predicted_revenue`, `currency`, and `horizon_days`.
Metadata identifies the loaded artifact by SHA-256 and records the observation
date, model status, history counts, and exclusions where available. These are
predictions, not campaign memberships; no high-value threshold is invented.
Churn also identifies the saved preparation and retains its evaluation limitations.

## Model loading and dependencies

The existing `churn_model.pkl` and `clv_model.pkl` under
`backend/ml_models/` are loaded, plus the verified `churn_preprocessing.json`
for churn. The loader accepts a segment identifier, not
an uploaded path. The files are trusted project artifacts; do not replace them
with untrusted uploaded pickle files. Loading is lazy and cached by local file
path, modification time, and size. Importing the app does not load a model.

Scikit-learn version warnings stop loading. CLV additionally checks the recorded
Python, NumPy, pandas, scikit-learn, and joblib versions exactly:

| Dependency | Recorded version |
| --- | --- |
| Python | 3.13.3 |
| NumPy | 2.5.4 |
| pandas | 3.0.6 |
| scikit-learn | 1.9.1 |
| joblib | 1.6.0 |

Backend model dependencies are pinned to these versions. A mismatch
returns `incompatible_environment`; it is not silently ignored. The recovered
churn preparation records and checks the same versions and is bound to the
classifier's checksum. Churn's original classifier still has no full training
metadata bundle. Runtime prediction never fits statistics, retrains models,
or writes artifacts. The separate recovery command fits only preprocessing
transformers on the original source and exports their verified statistics.

Do not run generic batch normalization, outlier capping, categorical encoding,
or batch-based missing-value filling on these model inputs first. Preserve
source values through ingestion and apply each model's fitted preparation at
prediction time. Churn uses the shared fitted helper; CLV already contains its
own fitted imputer and must not be preprocessed twice.

Category encoding and numeric normalization have been removed from the separate
preprocessing app, including their functions, pipeline steps, and settings.
Other existing cleaning steps retain their original behavior. In particular,
outlier capping and batch-based missing-value filling still need appropriate
ingestion configuration; they are not model-specific preparation. No new
storage-policy validation or model-specific helper remains in that app.

All saved model preparation belongs to `segments`. Churn fills supported
missing values and applies its saved training scale; CLV uses its fitted
pipeline. These operations prepare inputs in memory. They never update source
customer attributes, orders, products, or purchase amounts.

## Database integration

The Supabase connection is read from the untracked `backend/.env`. Credentials
are not stored in this README or source code. The canonical schema was inspected
before implementing these mappings:

- `customers` supplies the six churn attributes. The source churn label is never
  selected as a predictor input.
- `orders`, `order_items`, and `products` supply CLV purchase lines. Reads are
  limited by store, customer and the explicit history window.
- `segments` describes the two predictors and their required data.
- `ml_models` registers the exact local artifacts by version and full checksum.
- `customer_segments` holds optional current audience memberships.
- `segment_predictions` stores each customer's current prediction or unavailable
  result, observation date, model metadata, and computation time.

Existing canonical tables are **unmanaged**: migrations do not recreate them.
The app adds `segment_predictions` and Django maintains its migration history.
The new result table has row-level security enabled with no public-client
policies; it is accessed through the trusted backend connection. Its foreign
keys cascade result deletion if a parent is deleted. No source table's existing
columns or constraints were changed.

Prediction amounts and memberships are separate. CLV GBP values are saved in
result JSON, never forced into the probability-style membership `score`.
A CLV membership has a null score; its monetary prediction remains available
from the result. Churn membership scores contain the probability.

The repository's `backend/schema.sql` remains the canonical upload/business
schema. Apply this app's migrations as well when preparing a fresh database;
the derived result table is owned by these migrations, not the CSV loader.

### Initialization on another team member's checkout

From the project root:

```powershell
.venv/Scripts/python.exe backend/manage.py migrate segments
.venv/Scripts/python.exe backend/manage.py initialize_segments
```

The existing canonical schema must already be present. Initialization registers
only churn and CLV and activates the current local artifact versions. Repeating
it does not create duplicate definitions or model versions. Existing segment
metadata is preserved. Churn's registry training timestamp uses artifact file
modification time as an explicitly labelled estimate because the original
classifier did not record its training timestamp; CLV uses its saved timestamp.

### Source-data boundaries

The demo contains unrelated source populations within one store. Churn snapshot
customers are not matched to Retail customers. Known non-Retail demo populations
are unavailable for this Retail-trained CLV contract. Retail source prefixes are
removed only when converting identifiers to the model's input representation.

The CLV adapter checks product ownership, invoice/line date agreement, and
quantity-times-price consistency. It requires the original unit price and an
explicit GBP declaration. It will not convert currencies or silently ignore
cross-store products. More than 50,000 purchase lines for one customer produces
an unavailable result instead of silently truncating history.

All source table reads include the authenticated store/customer scope. Source
values are not rewritten. Repeated valid transaction lines remain separate.

## API access

The account/login app is separate work. Until it is integrated, these endpoints
use **administrator-issued service tokens scoped to one store**, rather than
accepting arbitrary store IDs or depending on nonexistent Django user tables.
Tokens are signed with the private `DJANGO_SECRET_KEY` from the local/deployment
environment and expire after one hour by default. They are not Supabase API
keys or database passwords. Keep the signing secret and issued tokens private;
use HTTPS outside local development. Rotating the signing secret invalidates
issued tokens. Team backends should use the intended server's signing secret.

Issue a token from a trusted terminal:

```powershell
.venv/Scripts/python.exe backend/manage.py create_segment_token --store-id STORE_UUID
```

Supply it as `Authorization: Bearer YOUR_TOKEN`. There is no public token-issuance
endpoint. A development fallback signing key is rejected. This service-token
mechanism can later be replaced by account authentication that resolves the
same authorized store scope.

| Method and path | Purpose |
| --- | --- |
| `GET /api/segments/` | List the two registered predictors and required data |
| `POST /api/segments/churn/run/` | Read customer attributes, predict, and optionally persist |
| `POST /api/segments/clv/run/` | Read purchase history, prepare features, predict, and optionally persist |
| `GET /api/segments/churn/results/` | Retrieve current stored churn results |
| `GET /api/segments/clv/results/` | Retrieve current stored CLV results |
| `GET /api/customers/CUSTOMER_UUID/segments/` | Retrieve a customer's saved results for Customer 360 |

Example churn body:

```json
{
  "customer_ids": ["CUSTOMER_UUID"],
  "persist": true
}
```

Example CLV body:

```json
{
  "customer_ids": ["CUSTOMER_UUID"],
  "observation_date": "2011-12-09",
  "currency": "GBP",
  "persist": true
}
```

Use an appropriate explicit source-local cutoff for your data. The date above
is the historical Retail model's recorded observation cutoff, not today's date.
A request supports 1–100 unique customer UUIDs. Every identifier must belong to
the token's store; otherwise the whole request is rejected before any scoring.
The store cannot be changed by adding a `store_id` to the request body.

Set `persist=false` for a read/predict-only run. It writes no prediction or
membership rows. Persisted reruns update the same current result rather than
creating duplicates. PostgreSQL locks serialize concurrent runs for the same
store/customer/segment. An older CLV cutoff cannot replace a newer stored
result; use a nonpersistent run to examine an older period.

A batch is atomic: a database/request failure rolls back the entire batch.
Customer-level ineligibility is a normal persisted `unavailable` result with
reasons, not a fabricated zero prediction. Registry mismatches fail closed.

### Optional membership updates

Prediction alone does not decide who belongs in a campaign audience. To update
memberships, explicitly add both:

```json
{
  "update_memberships": true,
  "membership_threshold": "0.70"
}
```

For churn the threshold is a probability between 0 and 1. For CLV it is a
nonnegative GBP amount. The customer qualifies when the prediction is at least
the supplied threshold. There are no invented default business thresholds.
Below-threshold or data-ineligible customers have the previous membership
removed when membership updates are requested. Transient model/registry failures
do not erase an existing membership. Results record whether an update happened
and which threshold was requested. No membership changes occur in a dry run.

Result-list endpoints accept `limit` (1–100), `offset`, and an optional `status`
of `predicted` or `unavailable`. Responses include counts and pagination values.
Unauthenticated/expired-token calls return 401, inaccessible customers 404,
invalid requests 400, and database/storage availability failures 503.

## Running from the terminal

```powershell
.venv/Scripts/python.exe backend/manage.py run_segments --segment churn --store-id STORE_UUID --customer-id CUSTOMER_UUID
.venv/Scripts/python.exe backend/manage.py run_segments --segment clv --store-id STORE_UUID --customer-id CUSTOMER_UUID --observation-date 2011-12-09 --currency GBP --dry-run
```

Repeat `--customer-id` for a bounded batch. Membership updates additionally need
`--update-memberships --membership-threshold VALUE`. Terminal commands are
administrator operations; API requests require a token.

## Remaining platform work

This app now supports source reads, both model preparations, prediction,
eligibility, current-result storage, optional memberships, and authenticated
retrieval/execution APIs. Customer 360 UI, account signup/login, ingestion event
triggers, scheduling, and the remaining seven predictors belong to later work.
The historical model-evaluation metadata is preserved rather than rewritten.

## Running the checks

From the repository root in PowerShell:

```powershell
.venv/Scripts/python.exe backend/manage.py check
Push-Location backend
../.venv/Scripts/python.exe -m pytest apps/segments/tests config/tests/test_startup.py -q
Pop-Location
```

These tests cover missing and invalid inputs, valid zero/false values,
explicitly allowed missing features, history counts, custom rules, separate
segment decisions, unchanged inputs, and app startup. Predictor tests also load
the existing artifacts, compare features with the notebook's original pure
preparation functions, compare CLV predictions, and verify churn uses its saved
transformation and never scores raw values. Notebook training cells are never run. Tests use synthetic records
and isolated database settings; automated tests do not connect to Supabase, retrain models, or change
artifacts. Database/API tests create isolated canonical fixture tables, exercise
source reads, persistence, authorization, membership removal and batch rollback.
A separate live check verified the Supabase path with source fingerprints unchanged.
