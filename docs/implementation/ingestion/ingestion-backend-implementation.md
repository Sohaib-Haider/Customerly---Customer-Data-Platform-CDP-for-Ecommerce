# Ingestion backend implementation

Implemented: 17 November 2026.

Documentation completeness review: 17 November 2026. The frontend-support
extension now implements the timestamped activity feed described in section
21. Audience membership APIs are implemented in the existing segments app.

This is the implementation contract for the upload-to-results backend. It
covers multiple CSVs, mapping, review, cleaning, canonical storage, model jobs,
APIs, operational setup, extension points and the limits of the current scope.

Contents:

1. [Architecture](#1-architecture)
2. [Files](#2-files)
3. [Database records and migrations](#3-database-records-and-migrations)
4. [Authentication and uploader identity](#4-authentication-and-uploader-identity)
5. [Source registration and currency](#5-source-registration-and-currency)
6. [Private uploads, limits and retention](#6-private-uploads-limits-and-retention)
7. [Mapping review and file relationships](#7-mapping-review-and-file-relationships)
8. [Protected cleaning and canonical validation](#8-protected-cleaning-and-canonical-validation)
9. [Record identity and update rules](#9-record-identity-and-update-rules)
10. [Transactions, status and retries](#10-transactions-status-and-retries)
11. [Segmentation jobs](#11-segmentation-jobs)
12. [API contract](#12-api-contract)
13. [Setup and frontend connection](#13-setup-and-frontend-connection)
14. [Verification and extension](#14-verification-and-extension)
15. [Internal call sequence and exact defaults](#15-internal-call-sequence-and-exact-defaults)
16. [Revisions, write boundaries and restart behavior](#16-revisions-write-boundaries-and-restart-behavior)
17. [Related-file example and supported canonical targets](#17-related-file-example-and-supported-canonical-targets)
18. [Compiler details and report interpretation](#18-compiler-details-and-report-interpretation)
19. [Operational readiness and troubleshooting](#19-operational-readiness-and-troubleshooting)
20. [Frontend requirements discussed but not implemented yet](#20-frontend-requirements-discussed-but-not-implemented-yet)
21. [Frontend activity-feed extension](#21-frontend-activity-feed-extension)

## 1. Architecture

Ingestion is one Django app: `backend/apps/ingestion`. It calls sibling Python
services directly; it does not make internal HTTP requests between apps.

**Pipeline:** Multiple CSVs → per-file mapping → approval and relationship
confirmation → protected preprocessing → canonical validation → transactional
Supabase save → durable segmentation → Customer 360 / KPI reads → frontend.

| Component | Responsibility |
|---|---|
| Ingestion | Upload ownership, private files, durable review, relationships, strict conversion, IDs, saving and prediction work |
| Mapping | Proposed column correspondence, schema gate and explicit review operations |
| Preprocessing | Existing composable cleaning functions, invoked with ingestion-specific settings |
| Segments | Read canonical history, apply fitted model preparation, predict and store current results |
| Analytics | Read stored customer data/results and calculate supported source-aware KPIs |

Campaign execution, the other seven segment implementations, Postgres-source
connectors, normal store signup/login and frontend screens are outside this
CSV-ingestion implementation. Existing trained artifacts were not retrained.

## 2. Files

```text
backend/apps/ingestion/
├── apps.py
├── models.py                         Durable operational records
├── authentication.py                 Verified signed store-user principal
├── serializers.py                    Strict API contracts
├── storage.py                        Private filesystem storage
├── compiler.py                       Approved-row cleaning, schema conversion and relationships
├── services.py                       Batch lifecycle and transactional saving
├── jobs.py                           Durable prediction execution/retries
├── source_metadata.py                Tenant-specific source/currency declarations
├── views.py
├── urls.py
├── migrations/
│   ├── 0001_initial.py
│   ├── 0002_private_rls.py
│   └── 0003_batch_active_operation_id_batch_active_stage_and_more.py
├── management/commands/
│   ├── create_ingestion_token.py
│   ├── process_ingestion_jobs.py
│   ├── purge_ingestion_uploads.py
│   └── verify_ingestion.py
├── tests/test_ingestion.py
└── README.md
```

Supporting edits register the app in `config/settings/base.py`, mount routes
in `config/urls.py`, ignore private uploads, extend source metadata handling
in analytics, and preserve source identifiers/currency rules in the existing
CLV adapter. Existing general preprocessing defaults remain unchanged.

## 3. Database records and migrations

The original `backend/schema.sql` remains the 23-table canonical contract.
The managed ingestion migrations now add six operational tables:

| Table | Stored information |
|---|---|
| `ingestion_sources` | Store UUID, immutable namespace/name/currency/profile |
| `ingestion_batches` | Store/uploader, source, request key, content fingerprint, status, revision, review and report |
| `ingestion_files` | Private storage reference, original filename, SHA-256, row count, proposal and approved document |
| `ingestion_reviews` | Immutable review decisions for each accepted batch revision and reviewer |
| `ingestion_segment_jobs` | Customer, segment, options, status, attempts and outcome |
| `ingestion_activity_events` | Ordered timestamped stage/cleaning/job events, attempt UUIDs and safe metadata |

The coordinator also writes the existing canonical `ingestion_uploads` row
for each file and persists approved primary column mappings to
`ingestion_column_mappings` when business data is saved. Full mapping audit,
relationship bindings and calculations remain in the durable review document;
the canonical mapping table cannot hold all that information.

Store/uploader UUIDs in the new operational tables are scalar identifiers;
ownership is enforced in authentication and service queries. Managed tables
reference one another using normal foreign keys. A batch request key is
unique per store, a namespace is unique per store, a review revision is unique
per batch, and each batch/customer/segment has at most one prediction job.

Migration 0001 depends on the existing segment-storage migrations. Migration
0002 enables PostgreSQL RLS on all five new tables without adding public
policies. The backend owner-capable database connection can still operate;
application tenant filtering remains essential. Migrations do not create
or replace the externally managed canonical business tables.
Migration 0003 adds the event table with RLS and batch operation-lease fields.

## 4. Authentication and uploader identity

Use `Authorization: Bearer TOKEN`. Unlike a store-only service token,
ingestion requires a signed `store_user_id` claim. The trusted command checks
that the user exists in the specified store with owner/admin/member role:

```powershell
python manage.py create_ingestion_token --store-id <store-uuid> --user-id <store-user-uuid>
```

The output is a secret. It uses the existing token salt/purpose and default
one-hour expiry, so it also works with analytics and segments. Ingestion
rechecks the claimed user's current store membership on every request.
The client cannot choose an uploader by sending a UUID in the upload body.

Store-only tokens are rejected by ingestion. Missing/foreign batches and
files return 404. Responses, including file downloads, use
`Cache-Control: private, no-store`. No database credentials or storage paths
are returned. Operator-issued tokens are not an implementation of interactive
store login, account creation or per-role fine-grained permissions.

## 5. Source registration and currency

Before uploading, register a source with a stable namespace. Multiple CSVs
from that source can reference each other's customer, product and order keys.
Separate source namespaces are never merged by similar IDs/contact fields.

Supported analytics profiles: `generic`, `retail`, `transactions`, `churn`,
`events`, `messages`, `coupons`. Profiles describe supported data semantics;
they are not assertions that an upload is one of the demo datasets.

`retail` and `transactions` require a declared currency. The backend validates
the currency-code shape; the uploader is responsible for verifying the actual
source units. No currency conversion or external currency verification occurs.
The current CLV model accepts GBP only and still checks original unit prices
and quantity-times-price consistency before prediction.

Namespaces `ret`, `dnb`, `chr`, `evb`, `msg`, `cpn`, and `unknown` are reserved
for existing populations. Arbitrary uploads do not receive those labels.
Source metadata is immutable; create a different namespace when semantics
change. Namespaces are lowercase letters/digits/underscores, start with a
letter, and have at most 40 characters.

Customer 360 resolves registered names/currencies within the token store.
KPIs retain actual namespace keys and use the declared profile to determine
supported metrics. Unsupported profile/card combinations are unavailable;
money without verified declaration is not labeled as a supported currency.
The original demo KPI rules remain unchanged. Registered custom sources in
the standalone KPI CLI require an explicit store scope.

## 6. Private uploads, limits and retention

`INGESTION_PRIVATE_ROOT` defaults to `backend/private_uploads/` and can be set
in the backend environment. Files use generated UUID filenames, never client
paths. Original names are metadata. This directory is gitignored, has no public
media route and must not be exposed by a web server. POSIX permission settings
request owner-only files/directories; Windows deployments should protect the
root using their filesystem ACLs.

Each batch supports 1–10 CSV files, at most 5 MiB per file, 20 MiB total,
10,000 data rows per file and 200 columns per file. CSV must be UTF-8,
comma-separated, rectangular and have unique nonblank headers. Identical file
content cannot be included twice in one batch. The adapter rejects invalid
input before writing upload records. JSON action bodies are bounded to 5 MiB.

Each file has a SHA-256 checksum. Mapping and validation recheck stored bytes.
The content fingerprint uses source UUID and file checksums; the request key
prevents accidental duplicate batch creation. Reusing a key with different
content/source returns 409. Reusing it with the same content returns the
existing batch. Changing only the filename does not make identical data new.

Failed normal creation removes its newly written files. Private file writes
and DB transactions are not one distributed transaction; an abrupt process
kill between them can leave an orphan file, which an operator must reconcile.
Use durable private storage/a persistent volume in deployment. A public
Supabase Storage bucket is not configured by this implementation.

```powershell
python manage.py purge_ingestion_uploads --older-than-days 30
```

This removes raw files from completed batches older than the retention window
while retaining audit records, checksums and imported business data. Unfinished
or failed batches retain originals for correction/retry; their eventual
administrative deletion is an operator policy. Purged originals return 404.

## 7. Mapping review and file relationships

Each file is mapped independently using the existing mapper. `use_llm: false`
disables provider calls. Default true allows the current Gemini integration;
missing credentials/provider failures produce reviewable proposals rather than
fabricated approval. Mapping is synchronous and bounded by upload limits.

Proposals and approvals are stored in ingestion tables, so long-lived reviews
do not depend on the standalone mapping API's one-hour signed-state document.
Revisions prevent stale edits and processing. Remapping clears current approval
and validation; prior accepted review versions remain as audit records.

Review includes every file exactly once. It can approve/reject/correct proposed
targets, explicitly bind source keys to canonical foreign-key columns, specify
supported calculations and choose occurrence identity columns. Rejected and
unmapped ordinary columns do not enter the working data. Explicitly approved
binding/calculation/identity inputs are selected data too, not arbitrary extra
columns forwarded to preprocessing.

Primary approved targets must be unique. Tenant IDs, platform UUIDs,
workflow references and arbitrary noncanonical targets cannot be supplied.
Bindings are resolved using the current store and source; they do not accept
uploaded database UUIDs as authority. Matching source keys are required;
records are never joined by row position or fuzzy identity matching.

Supported calculations are intentionally explicit and limited:

- `multiply`: two approved source values into `order_items.sales_value`.
- `date`: one source timestamp/date into `orders.order_date`; optional
  `format` uses an explicit Python strptime pattern such as `%d/%m/%Y`.
- `channel_norm`: an approved channel into `message_sends.channel_norm`.
  Supported declarations: email, sms, push, web_push, mobile_push, whatsapp.

Unknown calculations are rejected. No arbitrary executable expressions or
automatic financial formulas are accepted. Multi-part campaign keys and other
unsupported layouts need a deliberate contract/extension, not guessed joins.

## 8. Protected cleaning and canonical validation

Ingestion preserves original file row numbers and approved fields together.
It does not independently clean separate customer/order/product arrays and
then attempt to realign them. Canonical tables are built after cleaning.

The ingestion cleaning policy differs deliberately from the standalone
preprocessing defaults:

- Identifiers, leading zeros, dates, numeric amounts, booleans and relationship
  keys stay attached to their original rows and undergo strict conversion.
- Ordinary descriptive fields use the existing preprocessing pipeline for
  trimming. Inference, generic casting, date guessing, constant removal,
  missing-value filling, outlier capping and independent duplicate removal are
  disabled in this ingestion-specific call.
- Missing optional facts remain unknown. One customer's contact detail, an
  identifier, a purchase amount or history is never filled from another row.
- Required values are validated before saving. Missing model inputs can make
  a segment unavailable; a cleaned table is not proof that all segments apply.
- Deduplication is opt-in per file, compares all selected values together,
  and keeps the first original row reference. Genuine repeated lines/events
  are retained by default.

General preprocessing app defaults were not changed. This policy is visible
in per-file reports, including protected columns and removed duplicate counts.
Some protected fields do not pass through pandas inference at all; that is
how exact money and source identities remain intact.

Canonical types/nullability come from the existing schema catalog. Conversion
checks integer bounds, finite Decimal amounts, declared numeric scale/precision,
string lengths, strict true/false or 1/0 booleans and ISO dates/timestamps.
Source-local TIMESTAMP fields reject offsets; TIMESTAMPTZ requires one.
Non-ISO order dates need an explicitly approved date calculation/format.
Amounts use Decimal and direct database parameters, not the preprocessing
HTTP API's float JSON representation. No normalization or automatic rounding
is performed. Order-line/header calendar dates must agree when supplied.

Required missing product names are errors; no invented product descriptions
or synthetic basket IDs are generated. Holidays are shared reference data:
tenant uploads can reference exact existing date/name rows but cannot create
or modify the platform calendar.

## 9. Record identity and update rules

Natural source IDs identify customers, products, orders, marketing campaigns,
coupon campaigns and messages. Stored source IDs are `namespace:original_id`.
Existing canonical IDs are reused; new ones are deterministic UUID5 values
including store, table and source identity.

Order items, events, coupon-redemption occurrences and causal promotions lack
universal source-ID columns. Review can supply `identity_columns` for each
such table. Those selected source values form a stable composite key, enabling
updates across changed files. Without them, identity uses source namespace,
file SHA-256 and original row number. Repeated lines within a file remain
distinct. Same bytes replay safely; reordered/changed files are new occurrences.
Validation returns this warning so clients can request stable line/event keys
when updates or overlapping exports are intended. File-row IDs do not promise
business deduplication across arbitrary exports.

Conflicting values for the same identity across a batch block validation.
Nonconflicting partial records merge. Blank/missing uploaded fields do not
erase existing non-null fields, and absent columns are not included in updates.
There is no explicit field-clearing or delete-record operation in this version.
Creation times are preserved on update; update times refresh where supported.

## 10. Transactions, status and retries

Batch status: `uploaded` → `mapped` → `reviewed` → `validated` →
`segments_pending` → `completed`, with `validation_failed` or `segments_failed`
when appropriate. Review/remap invalidates earlier validation. Per-file states
track uploaded/mapped/reviewed/saved stages. Stages are durable checkpoints,
not live row-by-row percentage progress.

Validation is a preview and makes no business writes. It reports the first
blocking issue with available file/row/field context, plus cleaning/count
information on success. Correct the review and validate again.

Process requires the validated current revision, locks the batch, serializes
same-source writes with a PostgreSQL advisory lock, rebuilds/revalidates against
current data, then saves parents before children. All business records,
canonical mapping records and prediction jobs commit together. Constraint
failure rolls everything back; the review/uploads remain for retry. Repeating
process on an already saved revision returns its current result.

Post-save prediction failure never rolls back imported data. Jobs are durable
before execution begins. The HTTP process/retry action attempts up to 100 jobs;
larger backlogs return 202 and remain available for a worker. Import preparation
and saving are synchronous; HTTP timeouts can require retrying the same request.
There is no Celery/Redis dependency or automatic worker service installation.

## 11. Segmentation jobs

Churn runs by default unless disabled in review. CLV must be explicitly enabled
with a source-local observation date and verified GBP source currency. Thresholds
are required only when membership updates are requested. Merely producing a
prediction does not assign membership.

Jobs cover new/updated customers and customers affected by order/activity
changes, including previous owners when supported records are reassigned.
The segment service fetches canonical history and owns model preparation.
Current results go into the existing prediction table, not ingestion tables.

Job states: `pending`, `completed`, `failed`, `superseded`. Genuine unavailable
data is a completed attempt with an unavailable prediction; model/configuration
or infrastructure failures are retryable failures. An older CLV cutoff that
would replace a newer stored prediction is superseded. Jobs use row locks and
savepoints; an interrupted attempt rolls back to its previous durable state.

```powershell
python manage.py process_ingestion_jobs --limit 100
python manage.py process_ingestion_jobs --retry-failed --limit 100
python manage.py process_ingestion_jobs --watch
```

Run the watch command under the deployment's process supervisor, or schedule
the one-shot command. It is not automatically launched by Django. Status
reconciliation also handles a restart after the final job but before batch
summary update. Retry never reimports the source CSVs.

## 12. API contract

All routes are under `/api/ingestion/` and require the store-user token.

| Method/path | Purpose |
|---|---|
| GET / POST `sources/` | List/register immutable source metadata |
| GET / POST `batches/` | List/create a multi-file batch |
| GET `batches/{id}/` | Review documents, status, reports and job counts |
| GET `batches/{id}/jobs/` | Paginated customer IDs, prediction attempts and outcomes |
| GET `batches/{id}/events/` | Ordered timestamped activity with an incremental cursor |
| POST `batches/{id}/map/` | Map all files |
| POST `batches/{id}/review/` | Save approved decisions/relationships/calculations |
| POST `batches/{id}/validate/` | Preview cleaning and canonical validation |
| POST `batches/{id}/process/` | Commit the validated batch and attempt predictions |
| POST `batches/{id}/retry-segments/` | Resume pending/failed jobs |
| GET `batches/{id}/files/{file_id}/` | Download an owned retained original CSV |

Source registration JSON:

```json
{"namespace":"myshop","name":"My store exports","currency":"GBP","analytics_profile":"retail"}
```

Batch creation uses multipart fields `source_id`, `request_key`, and repeated
`files` entries. Keep the same request key when retrying the same upload.
Lists accept `limit` (1–100, default 25) and `offset` (default 0).

Mapping request:

```json
{"revision":0,"use_llm":false}
```

Review example for one mixed customer/order CSV:

```json
{
  "revision":1,
  "files":[{
    "file_id":"<file-uuid>",
    "set_targets":[
      {"source_column":"customer","target_table":"customers","target_column":"source_customer_id"},
      {"source_column":"order","target_table":"orders","target_column":"source_order_id"},
      {"source_column":"date","target_table":"orders","target_column":"order_date"}
    ],
    "bindings":[
      {"source_column":"customer","target_table":"orders","target_column":"customer_id"}
    ]
  }],
  "segment_options":{
    "churn":{"enabled":true},
    "clv":{"enabled":true,"observation_date":"2026-11-17","update_memberships":false}
  }
}
```

Other file review options: `approve`, `reject`, `approve_exact`,
`approve_suggested`, `calculations`, `identity_columns`, `deduplicate`.
Example occurrence key: `"identity_columns":{"order_items":["line_id"]}`.
The source column must exist in that file. To disable all segmentation,
explicitly disable churn and omit/disable CLV.

Validate/process/retry bodies contain only the current revision:

```json
{"revision":2}
```

Action responses contain batch ID/source/status/revision, uploader and batch
creation/update times, current review/report, per-file documents/checksums/status and counts
of jobs by state. Private storage paths and token contents are excluded.
Validation errors are saved under `report.errors`; saved outcomes include
canonical record counts, affected-customer count and job count.

Status codes: 200 successful action/read; 201 newly created source/batch;
202 saved with pending prediction work; 400 invalid request/review; 401 missing
or unauthorized uploader token; 404 inaccessible records/files; 409 stale
revision, conflicting idempotency/source metadata or save constraint conflict;
413 input limit; 422 failed preview validation; 503 inaccessible storage/DB.
A process-time revalidation failure can return 400; business writes roll back.

## 13. Setup and frontend connection

From `backend/`, using the existing private database/signing environment:

```powershell
python manage.py migrate ingestion
python manage.py initialize_segments
python manage.py create_ingestion_token --store-id <uuid> --user-id <uuid>
python manage.py runserver
```

Use a valid existing store/store-user pair; this implementation does not invent
an uploader. Do not put the signing secret or database URL in a frontend bundle.
The frontend should register/select a source, upload files, map, show and save
review decisions, validate, show errors/warnings, process, then poll status.
Use returned customer UUIDs from existing analytics customer-search APIs to
open Customer 360. KPI APIs now recognize compatible registered source profiles.
The batch's `jobs/` endpoint supplies affected customer UUIDs and per-job
availability/failure reasons without returning unbounded prediction arrays.
Normal login and a chosen same-origin proxy/CORS deployment policy remain
separate integrations.

In deployments, use persistent private storage, apply migrations, initialize
the exact artifact registry and supervise the prediction worker. Bounded
synchronous imports are the first version, not an unlimited distributed bulk
loader. Very large/unsupported files must be split or handled through an
explicit future ingestion adapter; arbitrary CSV syntax does not imply enough
information for canonical records or all segmentation models.

## 14. Verification and extension

```powershell
..\.venv\Scripts\python.exe -m pytest apps/ingestion/tests -q
..\.venv\Scripts\python.exe -m pytest -q
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py verify_ingestion
```

The isolated tests use temporary SQLite canonical tables/private storage. They
cover multi-file/mixed-entity imports, approvals, calculations, relationship
resolution, exact Decimal compilation, leading zeros, real repeated lines,
stable occurrence updates, conflicts, stale revisions, idempotent requests,
tenant/uploader isolation, transactional rollback, preservation of old values,
prediction failures/retries, worker recovery, private downloads and retention.

`verify_ingestion` requires configured PostgreSQL and registered artifacts.
It creates a temporary store/user/source and four CSVs, exercises the real
HTTP flow, both fitted models, saved results, Customer 360, source-aware revenue,
exact amounts, import retry safety and RLS. All sample database records roll
back and temporary files are removed. The migrations/model registry remain.
No real Gemini calls are made because mapping uses `use_llm: false`.

Live verification passed on 17 November 2026 after applying the two ingestion
migrations. The connected database initially had no segment/model registry
rows, so the existing initialization command registered the two unchanged
artifacts before the successful check. No original business rows were changed.
Final offline verification: 23 ingestion tests passed; full backend suite
330 passed, 34 opt-in PostgreSQL KPI tests skipped, 3 subtests passed and
9 existing pandas date-inference warnings. Django system checks found no
issues, migration drift checks reported no changes, and documentation links
and code fences validated. A second PostgreSQL connection also confirmed
that import-creation and same-source transaction locks could not be bypassed.

To extend: add reviewed calculations to the closed compiler registry and
serializers; validate their semantics and precision. Add new segment dispatch
support to the segments app first, then ingestion option/job contracts. New
source profiles need explicit analytic applicability and currency rules.
Never add arbitrary SQL identifiers/expressions from client input or turn an
unavailable result into a fabricated zero.

## 15. Internal call sequence and exact defaults

The HTTP views validate requests and delegate to the following functions.
These are also the main entry points for developers tracing this implementation:

| Function | Current behavior |
|---|---|
| `create_batch` | Validate every file, establish checksum/request identity, save private originals and upload records |
| `map_batch` | Check bytes, call `map_csv` once per file, retain proposals, invalidate current review/preview |
| `review_batch` | Apply explicit decisions, validate bindings/calculations, save approval documents and an immutable review version |
| `validate_batch` | Call `compile_batch`, save a preview report or the first blocking error, without saving business records |
| `process_batch` | Recompile under transaction locks, call `save_records`, persist approved primary mappings and prediction jobs |
| `run_jobs` | Call the existing `execute_customer` with each job's reviewed options, then reconcile batch status |
| `batch_detail` | Assemble current batch/file/review/report data and job counts for the frontend |

The compiler follows `selection` → `clean_rows` → strict field conversion →
record merge/identity → relationship resolution → required-value checks.
`fetch` reads canonical tables; `save_records` performs parameterized writes.
Only a closed set of schema tables/fields can supply SQL identifiers.

Defaults that callers must understand:

- New batch: status `uploaded`, revision `0`, empty review/report.
- Map action: `use_llm` defaults true; no action automatically approves output.
- File review: approval/rejection/correction/binding/calculation lists default
  empty; bulk-approval flags default false; deduplication defaults false.
- Review options: churn defaults enabled with membership updates disabled.
  CLV is not added automatically; enable it explicitly with an observation date.
- Segment option: `enabled` defaults true when that option is supplied;
  `update_memberships` defaults false. No membership threshold is invented.
- Request JSON is strict: unknown fields are rejected. For example, supplying
  `store_id`, `uploaded_by`, `status`, or server storage paths cannot override
  the authenticated context or lifecycle.
- Accepted review operations are applied to the current proposal document,
  not patched onto an arbitrary client-edited mapping document. Include the
  complete desired decisions for every file when submitting a replacement review.
- A source re-registration with identical metadata returns its existing ID;
  changed metadata under the same namespace returns 409.

## 16. Revisions, write boundaries and restart behavior

Example of one successful batch:

| Action | Submitted revision | Resulting revision | Stored batch status |
|---|---|---|---|
| Create batch | None | 0 | `uploaded` |
| Map files | 0 | 1 | `mapped` |
| Review all files | 1 | 2 | `reviewed` |
| Validate | 2 | 2 | `validated` or `validation_failed` |
| Process | 2 | 2 | `segments_pending`, then `completed`/`segments_failed` |
| Retry segment work | 2 | 2 | Reconciled prediction-work status |

Remapping or accepting another review increments the revision; validation,
saving and prediction retries do not. Submitted stale revisions return 409.
Clients must read the returned revision rather than calculate it themselves.

Remapping/reviewing is blocked after business data has been saved. Submit a
new batch for corrections. Failed preview validation can be corrected through
another review. Process requires a successful preview for the current review
and revalidates against current database contents before writing; the preview
is not a frozen database snapshot or a separate stored cleaned dataset.

Create/map/review/validate/process each use their own database transaction.
Mapping and preview work are synchronous while the batch is locked. Stage
results become visible when that action's transaction finishes; a polling
client cannot observe committed progress for every intermediate function.
Do not hold a single client-side operation open across approval and assume it
is one database transaction.

PostgreSQL creation locks use a hash of the store context. Business saving
also uses a hash of store/source context. The batch itself uses row locking.
This serializes conflicting requests while allowing other stores/sources to
operate. SQLite tests verify functional behavior but not PostgreSQL locking;
the live verification checks advisory-lock exclusion from another connection.

Each prediction job is attempted in a transaction, with execution inside a
nested savepoint. A process interruption before that job commits leaves its
previous durable state available for retry. The worker does not create a
separate persisted `running` job state. `attempts` increments on a committed
attempt; `result` holds the latest recorded job outcome, not an attempt history.
Prediction values themselves remain in `segment_predictions`.

## 17. Related-file example and supported canonical targets

The following four files illustrate the implemented multi-file contract.
Names here are examples; users do not have to upload files with these names.

`customers.csv`:

```csv
customer,email
001,one@example.com
```

`products.csv`:

```csv
product,title
p1,Widget
```

`orders.csv`:

```csv
order,customer,date
o1,001,2026-11-01
```

`lines.csv`:

```csv
line,order,product,quantity,price,sales
L001,o1,p1,2,10.25,20.50
```

Register one source, upload all four files in one batch and map them. Then
review the following targets and bindings using the returned file UUIDs:

| File/column | Approved destination | Review operation |
|---|---|---|
| customers/customer | `customers.source_customer_id` | Primary mapping |
| customers/email | `customers.email` | Primary mapping |
| products/product | `products.source_product_id` | Primary mapping |
| products/title | `products.name` | Primary mapping |
| orders/order | `orders.source_order_id` | Primary mapping |
| orders/date | `orders.order_date` | Primary mapping, or explicit date calculation if needed |
| orders/customer | `orders.customer_id` | Relationship binding |
| lines/order | `order_items.order_id` | Relationship binding |
| lines/product | `order_items.product_id` | Relationship binding |
| lines/quantity | `order_items.quantity` | Primary mapping |
| lines/price | `order_items.unit_price` | Primary mapping |
| lines/sales | `order_items.sales_value` | Primary mapping |
| lines/line | Stable `order_items` identity | `identity_columns` selection |

For example, the lines-file review portion can be:

```json
{
  "file_id":"<lines-file-uuid>",
  "set_targets":[
    {"source_column":"quantity","target_table":"order_items","target_column":"quantity"},
    {"source_column":"price","target_table":"order_items","target_column":"unit_price"},
    {"source_column":"sales","target_table":"order_items","target_column":"sales_value"}
  ],
  "bindings":[
    {"source_column":"order","target_table":"order_items","target_column":"order_id"},
    {"source_column":"product","target_table":"order_items","target_column":"product_id"}
  ],
  "identity_columns":{"order_items":["line"]},
  "deduplicate":false
}
```

Include this object together with the other three files in the review's
`files` list; it is not a separate endpoint request by itself. The line key
does not become a new canonical SQL column. It is approved identity metadata
used when constructing the record UUID. Canonical relationships are resolved
to UUIDs only after source keys have been validated.

The same implementation accepts mixed-entity CSVs: one approved source column
can provide a primary source ID and an explicitly reviewed binding to another
table. Existing mapping alternate-target information is retained in documents;
it does not independently authorize every alternate write.

Supported target tables follow the existing catalog:
`customers`, `products`, `orders`, `order_items`, `events`,
`marketing_campaigns`, `message_sends`, `coupon_campaigns`,
`coupon_redemptions`, `holidays`, `causal_data`.
Holidays have the read-only-reference restriction described above. Importing
historical campaign/message/coupon data is not campaign execution. Platform,
workflow, computed-feature, model-registry and KPI bookkeeping tables are not
upload targets.

Canonical parent records must exist in the batch or already exist in the
same source/store. A file containing only orders is not enough to create
missing customers automatically. Natural-source records with no source ID,
unresolvable foreign keys and conflicting values stop the import.

## 18. Compiler details and report interpretation

Natural-key lookups preserve existing canonical UUIDs. New UUIDs use the
UUID5 namespace URL and the string
`customerly:ingestion:{store}:{table}:{source_key}`. Natural `source_key`
values already include the declared namespace. Composite occurrence keys
encode the reviewed identity values as compact JSON. Fallback occurrence keys
contain `file:{sha256}:row:{record_position}` inside the source namespace.

Record positions start at 2 to account for the header. They are CSV-record
positions, not necessarily physical text-line numbers when a quoted cell spans
multiple lines. Filtering/deduplication does not renumber retained records.

Omitted new-record defaults are limited to existing schema conventions:
platform creation/update timestamps, zero line-discount fields and false
message-engagement flags. Existing values are preserved when fields are absent.
Source facts, product names, event timestamps, relationship keys and basket
identifiers are not invented to satisfy required columns.

Multiplication uses a Decimal context sized to the operand coefficients,
avoiding the normal 28-digit context silently rounding an approved line-amount
calculation. Conversion also rejects non-finite values and values beyond
PostgreSQL numeric limits. No implicit money quantization or currency exchange
occurs. Model-specific purchase validation remains the segment app's job;
an actual discounted sale can be imported even when it is incompatible with
the current CLV model's quantity-times-original-price requirement.

Successful validation reports contain:

| Key | Interpretation |
|---|---|
| `files` | Per-file selected-row counts, protected fields, cleaning report and duplicate-removal count |
| `warnings` | For example, changed/reordered files need stable occurrence keys to avoid new occurrences |
| `counts` | Number of compiled identities per canonical table, not separate inserted-versus-updated counts |
| `affected_customers` | Added after saving: number of customers scheduled/considered for prediction work |
| `segment_jobs` | Added after saving: jobs associated with enabled segment options |

A file's `cleaning` value can be null when it contains only protected fields
and does not need the descriptive-field pipeline. This is a protected
processing path, not an accepted skip-validation option. Process still calls
the compiler after a successful validation action. General missing-value
filling and duplicate-removal statistics must not be fabricated for such files.

Prediction jobs include current affected customers and prior customer owners
when supported records are reassigned. Reassigned order lines consider their
old and new orders. The implementation does not recompute every historical
customer on every upload or turn a source-profile declaration into eligibility.

Saved job results expose prediction availability/reasons, while the existing
segment APIs provide full saved predictions. Counts of completed jobs do not
mean every customer qualified for a model; unavailable data can be a completed
attempt. `segments_failed` means imported data remains saved but prediction
work needs attention.

## 19. Operational readiness and troubleshooting

| Situation | Current interpretation/action |
|---|---|
| Store-only token rejected | Issue an ingestion token for a real store-user membership; do not supply uploader IDs in request bodies |
| State/revision conflict | Reload the batch, inspect its current review and retry with the current revision |
| Required field or relationship missing | Correct review/input and validate again; do not invent IDs or records |
| Empty compiled output | Review selected fields and blank source rows; no successful empty business import is accepted |
| Source metadata conflict | Reuse identical metadata or deliberately choose another namespace; existing metadata is immutable |
| Failed save | Business writes roll back; inspect the constraint/data and retry the validated batch when corrected |
| Missing model registry/artifact | Initialize/check the exact existing artifacts, then retry prediction jobs without importing again |
| Pending jobs after a restart | Run the one-shot worker or a supervised `--watch` process |
| Failed jobs repeatedly retried | Inspect registry/configuration/data; `--watch --retry-failed` has no automatic exponential backoff |
| Original CSV download returns 404 | Confirm ownership and whether retention already purged it |
| Browser request blocked across origins | Configure the chosen frontend proxy/CORS arrangement; server-side API tests do not establish browser CORS |
| Gemini is not configured | Exact/alias proposals and explicit review still work; a proposal can include provider-unavailable information |

The last readiness check in this chat confirmed the private signing key was
configured and the live deterministic ingestion/model path worked. It also
found no configured Gemini key and no CORS middleware. Those observations
are dated verification results, not a guarantee of future environment state.
The live mapping check deliberately used no LLM and does not certify a Gemini
provider/model connection. The production requirements file/deployment process
also needs deployment preparation; this guide does not claim a production
server or supervised worker has been installed or started.

Tests and Django checks can run without shared Supabase writes. The live
`verify_ingestion` command does contact the configured database, temporarily
writes its own test records and rolls them back. Applying migrations and
initializing the registry are intentional persistent changes; the verification
command itself does not undo those setup operations. No credentials are stored
in this documentation.

## 20. Frontend requirements discussed but not implemented yet

The activity feed and audience APIs have now been implemented as described
below and in the segmentation guide. These remaining items are frontend or
separate integration work:

- A persistent activity feed is available through cursor polling. An SSE or
  WebSocket transport is not implemented or required for the first frontend.
- The frontend's automatic upload-to-mapping transition. Upload and mapping
  are separate current API actions; no existing backend call automatically
  maps a new batch on creation.
- The frontend's single "Approve & preprocess" action. It can coordinate
  review, validation and process calls, but those are separate current endpoints.
  The mandatory successful-validation gate is already enforced by process.
- The save-confirmation animation/confetti. It must follow confirmed business
  saving and distinguish pending predictions from a failed import; it is UI work.
- Audience counts and member-list APIs now exist in segments. Explicit
  membership thresholds still need an agreed application policy; no thresholds
  are automatically invented by this extension.
- Any additional dashboard aggregates identified while selecting charts.
  Chart rendering belongs to the frontend; aggregate facts belong to backend
  analytics. Existing KPI/customer APIs should be reused where sufficient.
- Supabase signup/signin and the `store_users.auth_user_id` integration.
  A SQL change discussed with the teammate is not proof that authentication
  code/migrations have been implemented or the current tokens replaced.

Keep these extensions in the existing ingestion/segments/analytics/users
domains as appropriate. Campaign execution, normal login and frontend screens
are not implemented by the backend activity/audience extension.

## 21. Frontend activity-feed extension

The existing ingestion app now exposes a durable activity feed. No new Django
app was created. Implementation lives in `activity.py`, `ActivityEvent` in
`models.py`, lifecycle hooks in services/compiler/jobs, `EventsView`, the events
query serializer, and migration 0003. Existing business records and trained
models are unchanged.

### Event schema and cursor API

`ActivityEvent` stores an increasing BigAutoField ID, batch foreign key,
optional operation UUID, stage, code, level, message, metadata and the actual
`occurred_at` timestamp. A `(batch, id)` index supports incremental reads.
Stages include upload, mapping, review, preprocessing, saving and segmentation.
Levels are info/success/warning/error as emitted by server code.

```http
GET /api/ingestion/batches/{batch_id}/events/?after=0&limit=50
Authorization: Bearer <store-user-token>
```

`after` is a nonnegative event ID, default zero; `limit` is 1–100, default 50.
The endpoint returns ascending events, `next_after`, `has_more`, batch `status`
and `activity` containing the current operation ID/stage/start time. Use the
returned cursor for the next poll and retain it when reloading the screen.
IDs may have gaps; they are not percentages or contiguous per-batch counters.
The query is scoped to the authenticated batch owner store before events are
read. Foreign batches return 404, invalid query parameters 400, and all responses
remain private/no-store. Empty results do not mean an active operation is done.

Batch detail also includes `activity`, with `latest_event_id`. Existing batches
are not given invented historical events; their feed can be empty until a new
action occurs. Creation/update timestamps remain available independently.

### Events emitted

| Code | Meaning |
|---|---|
| `upload.completed` | Private files/upload records accepted |
| `mapping.started` | Mapping action accepted and starting |
| `mapping.file_completed` | A file proposal was created; includes method/status counts |
| `mapping.completed` / `mapping.failed` | Mapping attempt finished or failed |
| `review.completed` | Approved review and relationship decisions persisted |
| `preprocessing.started` | Approved-data cleaning/validation attempt started |
| `cleaning.file_completed` | Protected file processing finished; includes actual row/duplicate counts |
| `cleaning.step_completed` | A step actually reported by the existing cleaning runner completed |
| `cleaning.duplicates_removed` | Explicitly approved deduplication really removed rows |
| `preprocessing.completed` / `preprocessing.failed` | Validation passed or the attempt failed |
| `saving.started` | Save-time revalidation and transactional saving started |
| `saving.completed` / `saving.failed` | Business save committed, or that save attempt failed |
| `segmentation.queued` / `segmentation.skipped` | Durable work queued, or no jobs requested/needed |
| `segmentation.job_completed` / `segmentation.job_failed` / `segmentation.job_superseded` | A real job outcome committed |
| `segmentation.completed` / `segmentation.failed` | Batch prediction status changed to a final outcome |
| `operation.interrupted` | An expired unfinished attempt was replaced by a new request |

Cleaning messages do not claim median filling, outlier capping or duplicate
removal when those operations did not run. The existing protected policy still
applies. Save-time revalidation can create cleaning events with `stage: saving`;
those are another real pass, not duplicate UI notifications to relabel as a
single preprocessing run. Job metadata contains identifiers/outcomes, not raw
CSV cells, passwords or contact details.

### Visibility, concurrency and recovery

The API wraps mapping/preprocessing/saving in `activity.operation`. It commits
the start event and active operation lease before calling the long synchronous
service. Another frontend request can therefore see the stage currently active.
Completion/failure events are committed afterwards, and the lease is cleared.
Failed saves never produce `saving.completed`. Existing idempotent save replays
do not add another saving start/success pair.

Per-file and cleaning-step events are recorded inside their action transaction.
Their timestamps reflect when they ran, but they become readable after that
transaction completes. The feed is not a live row-by-row stream. A rolled-back
save discards its internal progress events; the visible start and failed outcome
remain. A preview-validation failure can retain real cleaning events before its
validation error. HTTP operation wrappers provide start/end events; direct
service callers wanting the same lifecycle should use the operation wrapper too.

Concurrent HTTP actions on the same batch receive 409 while an operation lease
is active; review cannot change during that operation. The default lease is
1,800 seconds and can be changed using the Django setting
`INGESTION_OPERATION_TIMEOUT_SECONDS` (not a separately parsed environment
variable). After a server interruption, a new eligible request can replace an
expired lease and logs that recovery. Existing row/advisory locks and revision
checks remain authoritative; this is not permission to bypass a live transaction.

The frontend should show an active-stage indicator, poll for events, and only
show its save-success/confetti screen after `saving.completed` or a confirmed
saved batch status. Predictions can still be pending or fail independently.
This extension does not automatically chain upload → mapping or approval →
validation → saving; the frontend coordinates those existing API calls.

### Migration, tests and scope

Apply migration 0003 with `python manage.py migrate ingestion`. It enables
RLS without public policies on the new activity table. Existing operational
tables keep their prior RLS settings. No event history is backfilled.

`tests/test_activity.py` verifies timeline events/cursors, real timestamps,
save replay, failure-without-false-success, an active stage observable during
mapping, concurrent-request rejection, expired-lease recovery and tenant scope.
The extended live `verify_ingestion` command verifies timestamped events and
both audience member APIs using explicit zero thresholds only for its temporary
test records. Those test thresholds are not application defaults. Test records
roll back, and temporary private files are removed.

Verification: 10 new activity/audience tests passed. Full suite: 340 passed,
34 opt-in KPI database tests skipped, 3 subtests passed and 9 existing pandas
warnings. Migration drift checks passed. Migration 0003 is applied to the
configured Supabase database, and the extended live verification passed for
events, both audience APIs, real models, Customer 360, KPI, locking and RLS.
Its temporary database records rolled back and files were removed. The
existing KPI endpoints already provide total customers, source-separated revenue,
orders, average order value, revenue-by-month and new-customers-by-month. No
additional analytics aggregation or frontend code was added for this extension.

## Related documentation

- [Mapping implementation](../mapping/mapping-backend-implementation.md)
- [Preprocessing implementation](../preprocessing/preprocessing-backend-implementation.md)
- [Segmentation implementation](../segmentation/segmentation-backend-implementation.md)
- [Customer 360 implementation](../analytics/customer360-backend-implementation.md)
- [Canonical schema](../../notes/schema-notes/final-schema.md)
- [Canonical SQL](../../../backend/schema.sql)
- [Transformation preservation contract](../../notes/data_transformation_notes/transformation-contract.md)
