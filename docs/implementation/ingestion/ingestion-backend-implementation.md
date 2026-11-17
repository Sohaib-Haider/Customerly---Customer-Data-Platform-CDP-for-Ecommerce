# Ingestion backend implementation

Implemented: 17 November 2026.

This is the implementation contract for the upload-to-results backend. It
covers multiple CSVs, mapping, review, cleaning, canonical storage, model jobs,
APIs, operational setup, extension points and the limits of the current scope.

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
│   └── 0002_private_rls.py
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
The managed ingestion migrations add five operational tables:

| Table | Stored information |
|---|---|
| `ingestion_sources` | Store UUID, immutable namespace/name/currency/profile |
| `ingestion_batches` | Store/uploader, source, request key, content fingerprint, status, revision, review and report |
| `ingestion_files` | Private storage reference, original filename, SHA-256, row count, proposal and approved document |
| `ingestion_reviews` | Immutable review decisions for each accepted batch revision and reviewer |
| `ingestion_segment_jobs` | Customer, segment, options, status, attempts and outcome |

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

Action responses contain batch ID/source/status/revision, uploader and stage
times, current review/report, per-file documents/checksums/status and counts
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

## Related documentation

- [Mapping implementation](../mapping/mapping-backend-implementation.md)
- [Preprocessing implementation](../preprocessing/preprocessing-backend-implementation.md)
- [Segmentation implementation](../segmentation/segmentation-backend-implementation.md)
- [Customer 360 implementation](../analytics/customer360-backend-implementation.md)
- [Canonical schema](../../notes/schema-notes/final-schema.md)
- [Canonical SQL](../../../backend/schema.sql)
- [Transformation preservation contract](../../notes/data_transformation_notes/transformation-contract.md)
