# Mapping backend implementation

Implemented API integration: 16 November 2026.

## Purpose and architecture

`backend/apps/mapping` is the Django app that proposes canonical schema
targets for uploaded CSV columns. It now exposes HTTP endpoints in addition
to its existing Python/CLI workflow. It does not insert customers, resolve
foreign keys, generate tenant/customer UUIDs, or save uploads in Supabase.
Those operations belong to the future ingestion coordinator.

The flow is:

```text
UTF-8 CSV → local profile → kind detection → exact / documented alias matches
                                          ↓ unresolved columns only
                                   Gemini suggestions
                                          ↓
                              proposal + signed review state
                                          ↓ explicit review
                              approved mappings + signed state
                                          ↓ same original CSV
                              per-table direct-mapping data
                                          ↓
                         preprocessing / ingestion transformation / saving
```

An approved relationship or calculation mapping still needs ingestion's
transformation logic. Applying approved names is not a complete relational
database load. Keep the full reviewed document for relationship requirements,
alternate targets, decisions and audit information.

## Files and responsibilities

| File | Responsibility |
|---|---|
| `schema_catalog.py` | Parse canonical SQL and schema-note provenance; gate upload-mappable targets |
| `aliases.py` | Documented source aliases, alternate targets and requirements |
| `profile.py` | Local value profiling and safe metadata extraction |
| `detect.py` | Ranked source-kind detection, ties, overrides and review flags |
| `mapper.py` | Exact/alias resolution, unresolved LLM stage and collision handling |
| `llm.py` | Gemini SDK, response schema, bounded retry count and suggestion validation |
| `models.py` | Versioned mapping document and status/method models |
| `review.py` | Explicit approve, reject, correct and kind-override operations |
| `apply.py` | Apply only approved direct mappings to per-table CSV outputs |
| `config.py` | Environment/key handling, confidence thresholds and error scrubbing |
| `services.py` | HTTP-safe proposal/review/apply adapters and signed state |
| `serializers.py` | Strict request validation |
| `views.py`, `urls.py` | Authenticated API endpoints |
| `cli.py`, `__main__.py` | Existing standalone workflow |
| `management/commands/map_columns.py` | Django CLI wrapper |
| `eval/` | Existing ground truth and scoring utilities |
| `tests/` | Existing mapper tests plus API integration tests |
| `backend/core/api.py` | Shared service-token API base, CSV limits and bounded JSON parser |

The existing Python mapping dataclasses in `models.py` are not Django ORM
tables. This change adds no models, migrations, or database writes.

## Mapping rules

The canonical contract is parsed from `backend/schema.sql` together with
`docs/notes/schema-notes/final-schema.md`. The mappable gate includes source
fields and documented relationship targets, not arbitrary platform fields
such as tenant IDs or generated primary keys. Exact matches, aliases, LLM
suggestions and manual corrections use this same gate.

Kinds: `customers`, `transactions`, `orders`, `products`, `events`, `campaigns`,
`messages`, `coupons`, `holidays`, `unknown`. Files can contain multiple
entities. Detection scores/ties and a reviewer override are retained in the
document rather than treating the detected kind as an unquestionable fact.

Methods describe how the suggestion arose: `exact`, `alias`, `llm`, `manual`,
`none`. Status describes review: `suggested`, `needs_review`, `approved`,
`rejected`, `unmapped`. Confidence does not approve a mapping. Existing LLM
thresholds are 0.85 for a high-confidence suggestion and 0.50 minimum; low
confidence or ambiguity requires review or leaves the field unmapped.

Only explicit review makes a mapping approved. The API rejects a review
that leaves two approved columns targeting the same canonical column.
The underlying legacy apply function retains its first-wins conflict report,
but the signed API workflow prevents approved duplicate targets upstream.

Only approved mappings with no `requires` operations are directly applied.
`relationship_resolution`, `calculation`, and other requirements produce
explicit skipped entries. Direct application preserves source strings and
row order; it does not cast types, deduplicate, or alter values.

## Gemini configuration and privacy

Use the existing backend environment configuration:

```text
GEMINI_API_KEY=<private key>
GEMINI_MODEL=<model available to the configured account>
DJANGO_SECRET_KEY=<private backend signing secret>
```

Do not put secrets in a frontend or commit `.env`. Explicit process values
take precedence over environment files. The existing mapping configuration
loads `backend/.env`, with repo-root `.env` as a fallback. Key-related error
strings pass through the existing scrubber.

Values are profiled locally. Gemini receives column names and derived
metadata such as inferred dtype, null share, approximate distinct count,
shape tags and likely-PII flag; raw cell values/customer records are not sent
in the prompt. Headers themselves can contain sensitive information, so
avoid putting personal information in column names.

The existing local profiler samples up to 2,000 rows and has a 50,000-row
scan cap. The new HTTP adapter's 10,000-row input cap is lower. LLM calls
batch at most 40 unresolved columns per prompt and have up to three attempts
for supported transient failures. HTTP requests run synchronously.

`use_llm` defaults true. Set it false to use deterministic mapping without
provider calls. Missing credentials, unavailable providers or invalid LLM
responses degrade to reviewable output with error information. They do not
erase exact/alias matches or approve fabricated targets. A 200 proposal is
therefore not proof that Gemini ran successfully; inspect `document.llm`.

## Authentication and signed state

All endpoints use the existing store-scoped service token:

```http
Authorization: Bearer <store-scoped-token>
```

This matches analytics/segments authentication. It is not a Supabase JWT or
an end-customer login. A trusted backend operator can use the existing
`create_segment_token --store-id <uuid>` command. Store context comes from
the token, never a client-provided store ID.

Proposal returns `document`, `state`, `expires_at` and `persisted: false`.
The state is compressed, signed metadata containing the document, token
store UUID, original CSV SHA-256 and a fixed expiry one hour from proposal.
It contains no CSV cell payload. It is signed, not encrypted; treat it as
confidential metadata. A review returns updated signed state without
extending the original expiry. Apply requires the same original CSV bytes,
including encoding, line endings and row order.

Clients may display `document` but cannot submit an edited document as
approval. Only validated decisions against signed state are accepted.
Tampering, expiry and cross-store replay return 400. The access token has
its own expiry and can independently cause 401.

There is no durable review/session database in these apps. A still-valid
older state can be replayed after a newer review; stateless signatures do
not provide revocation or a latest-review guarantee. Ingestion must store
the authoritative review version and upload ownership before durable saving.

## API endpoints

Every response uses `Cache-Control: private, no-store`.

### GET `/api/mapping/targets/`

Returns `targets`, the upload-mappable catalog. Each entry includes table,
column, SQL type, nullability, provenance, description, kind, requirements
and citation. Use this for the frontend correction selector.

### POST `/api/mapping/propose/`

Content type: `multipart/form-data`.

| Field | Required | Meaning |
|---|---|---|
| `file` | Yes | UTF-8 comma-separated CSV with a unique, nonblank header |
| `use_llm` | No | Boolean, default true |
| `kind` | No | One of the documented source kinds |

```text
file: <CSV upload>
use_llm: false
kind: customers
```

Response document schema is the existing `column_mapping/v1`: source file,
row count, source columns, profile/kind information, LLM information,
per-column mappings and summary counts. No suggestions are auto-approved.

### POST `/api/mapping/review/`

Content type: `application/json`.

```json
{
  "state": "<state from proposal or last review>",
  "approve": ["email"],
  "reject": ["unneeded_column"],
  "set_targets": [
    {"source_column": "customer_tenure", "target_table": "customers", "target_column": "tenure"}
  ]
}
```

This illustrates request syntax only: a reviewer must choose a semantically
correct target; schema-valid does not mean two attributes mean the same thing.
Optional `approve_exact`, `approve_suggested` and `kind` mirror existing
review operations. Bulk approvals are explicit user decisions, not automatic
confidence-based approval. Corrections record manual provenance.

Do not approve/correct and reject the same column in one request. Corrections
must identify unique source columns and valid mappable targets. Response
contains the updated document/state and a `changes` audit list. Failed
review does not produce an accepted updated state.

### POST `/api/mapping/apply/`

Content type: `multipart/form-data`; required fields: `state` and `file`.

```json
{
  "status": "applied",
  "persisted": false,
  "tables": {
    "customers": {"columns": ["email", "tenure"], "rows": [["one@example.com", "12"]]}
  },
  "report": {"applied": [], "skipped": [], "conflicts": [], "output_files": []}
}
```

The sample report lists are abbreviated. Real reports contain the approved
and skipped mappings and reasons. `tables` holds all directly applicable
table data in `columns`/`rows` form. If nothing is directly applicable,
status is `no_applicable_mappings` with an empty tables object; inspect the
report rather than treating this as a completed ingestion.

Returned data is inline, not a persistent downloadable file. Server temporary
paths are removed from the public report. Temporary CSVs are automatically
deleted when processing finishes or fails. No original client file is edited.

## Limits and errors

CSV limits: 5 MiB, 10,000 data rows, 200 columns. At least one data row is
required. Header names must be nonblank, unique strings of at most 255
characters. Ragged rows, invalid UTF-8, NUL bytes and malformed CSV are rejected.
State strings accept at most 2,000,000 characters. Review JSON bodies are
bounded to 5 MiB. Requests accept only their declared fields.

| Status | Meaning |
|---|---|
| 200 | Operation succeeded; inspect mapping/review/apply status |
| 400 | Invalid file/options/decisions, expired/tampered/foreign state or wrong CSV |
| 401 | Missing or invalid/expired access token |
| 405 / 415 | Unsupported method or content type |
| 413 | Adapter file/body/row limit exceeded |

Framework/parser limits can also reject malformed or excessive multipart
requests. No database connection is needed for these operations, though a
private signing key and valid authentication are required. Infrastructure
errors are not disguised as completed mapping work.

## Calling from ingestion and the frontend

Frontend: obtain store authentication, load targets, propose a CSV, display
the mappings, send explicit review decisions, then apply using the latest
state and original file. Keep the reviewed full document for ingestion.

Within the same Django process, ingestion should call the existing Python
mapper/reviewer/apply functions or these service adapters directly instead
of making HTTP requests to sibling apps. Derive store context from trusted
authentication. Persist upload ownership and the authoritative review first.

For each applied table, pass `columns` and `rows` to preprocessing. Preserve
source-key relationships alongside that data. Resolve source IDs to canonical
UUIDs, calculations, alternate-target population, tenant tagging, schema/type
validation and transactional database persistence remain ingestion work.
Mapping detects column correspondence; it does not establish segment
eligibility or guarantee sufficient genuine history after cleaning.

`MappingResult.to_ingestion_column_mappings_rows(upload_id)` is an existing
shape emitter, not a persistence/approval gate: it can emit proposed targets.
The future coordinator must explicitly filter to approved mappings before
persisting those rows. Its canonical table has no status/method/audit columns,
so keep the reviewed document separately through the chosen ingestion design.

## Existing CLI and tests

From `backend/`, the existing workflow remains available:

```powershell
python -m apps.mapping verify
python -m apps.mapping map --help
python -m apps.mapping review --help
python -m apps.mapping apply --help
python manage.py map_columns verify
..\.venv\Scripts\python.exe -m pytest apps/mapping/tests -q
```

API tests cover authentication, allowed targets, unapproved mappings,
explicit/manual approval, duplicate targets, rejection, state tampering,
expiry/store scope, original-file binding, malformed/oversized input,
provider failure, relationship skipping, and a complete mapping-to-cleaning
flow. Tests use mocked Gemini behavior and make no paid provider calls or
database writes.

Final checks: 42 new mapping/preprocessing API tests passed. The full backend
suite passed 307 tests with 34 opt-in PostgreSQL KPI tests skipped, 3 subtests
passed and 9 pandas date-inference warnings. Django checks and documentation
links passed. No real Gemini request or database write was made for this work.

The existing catalog verification command parsed 23 tables and 82 mappable
fields across all 11 ingestible tables. It reported the existing open
`orders.region` provenance decision and exited nonzero; this field remains
excluded from upload mapping rather than guessing its source provenance.
This change does not resolve that schema decision or alter the schema.

## Related documentation

- [Mapping app README](../../../backend/apps/mapping/README.md)
- [Preprocessing implementation](../preprocessing/preprocessing-backend-implementation.md)
- [Canonical schema](../../notes/schema-notes/final-schema.md)
- [Canonical SQL](../../../backend/schema.sql)
- [Segmentation implementation](../segmentation/segmentation-backend-implementation.md)
