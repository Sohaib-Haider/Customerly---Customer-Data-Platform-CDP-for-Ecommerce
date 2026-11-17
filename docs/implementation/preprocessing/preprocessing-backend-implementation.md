# Preprocessing backend implementation

Implemented API integration: 16 November 2026.

**Integration update, 17 November:** the [ingestion manager](../ingestion/ingestion-backend-implementation.md)
now invokes cleaning with a protected policy for canonical imports. It preserves
identity, money, dates and unknown facts rather than applying every standalone
default to them. This app's standalone API defaults remain as documented below.

## Purpose and boundaries

`backend/apps/preprocessing` cleans supplied data before ingestion saves it.
It now has authenticated HTTP endpoints and a callable Python service. It
does not read/write Supabase, perform column-to-schema mapping, resolve
foreign keys, generate customer UUIDs, or run segmentation predictions.

```text
Mapping approval/application → supplied table data → preprocessing
                                                     ↓
                                          cleaned data + report
                                                     ↓
                          ingestion validates/transforms/saves canonical rows
                                                     ↓
                              segments reads DB and prepares model features
```

There is no generic numeric normalization/scaling or category-to-number
encoding in this app. Model-specific preparation remains in the segments
app, matching each model's training contract. It runs in memory after reading
database data and does not rewrite the original database values.

## Files

| File | Responsibility |
|---|---|
| `preprocessing.py` | Existing composable cleaning functions |
| `pipeline.py` | Existing default configuration, ordered runner and CSV helper |
| `utils.py` | Existing CSV/report utilities |
| `serializers.py` | Strict input and per-step option validation |
| `services.py` | Bounded table adapter, report checks and output conversion |
| `views.py`, `urls.py` | Authenticated config/run endpoints |
| `tests/test_preprocessing.py` | Existing cleaning-function and pipeline tests |
| `tests/test_api.py` | New API, validation and failure tests |
| `backend/core/api.py` | Shared authentication, input limits and CSV/JSON parsing |

No new database models, migrations, jobs or ingestion workflow were added.
The existing underlying cleaning functions and default enabled flags remain
unchanged by this API work.

## Default pipeline

All ten existing steps remain enabled, in this order:

| Step | What it does |
|---|---|
| `clean_column_names` | Lowercase and clean column names; suffix some collisions |
| `trim_whitespace` | Trim string cells and treat configured blank-like text as missing |
| `validate_required_columns` | Check configured required column names |
| `infer_column_types` | Infer numeric, datetime, categorical or text types |
| `remove_constant_columns` | Drop all-null columns or columns with only one distinct non-null value |
| `remove_duplicates` | Remove duplicate rows, keeping the first by default |
| `fix_data_types` | Cast according to explicit types or inference |
| `parse_dates` | Parse selected/inferred datetime columns |
| `handle_missing_values` | Default numeric median or categorical/text/date mode filling |
| `handle_outliers` | Default numeric IQR outlier capping |

Removing normalization does not make every original value immutable. The
existing outlier cap can change extreme revenue/price/quantity values, type
conversion can coerce values, filling changes missing entries, and constant
removal can remove source keys. These behaviors are explicitly configurable;
the API did not silently disable them.

Ingestion must choose appropriate settings for canonical business data and
protect source keys/required fields. For example, disable outlier capping if
actual recorded monetary amounts must remain unchanged; disable constant
removal when processing one customer or one row whose columns are all constant.
This is a configuration decision, not model normalization.

## Authentication and routes

Use the existing store-scoped service-token authentication:

```http
Authorization: Bearer <store-scoped-token>
```

It is the same current mechanism used by mapping, analytics and segments.
The API is stateless: authentication authorizes processing the submitted
payload, not looking up an upload by ID. It does not persist uploaded data.
Use future ingestion ownership checks when processing stored uploads.
Responses carry `Cache-Control: private, no-store`.

### GET `/api/preprocessing/config/`

Returns ordered `steps`, the complete `default_config`, input `limits`, and
`model_scaling: false` / `category_encoding: false`. Use this as the source
for a frontend configuration screen rather than copying defaults into UI code.

### POST `/api/preprocessing/run/`

Accepts either JSON table data or multipart CSV, not both.

JSON example:

```json
{
  "columns": ["revenue", "category"],
  "rows": [[100000, " A "], [200000, "B"], [null, "C"]],
  "config": {"handle_outliers": {"enabled": false}}
}
```

This `columns`/`rows` shape can be passed directly from a mapping apply
response's individual `tables[table_name]` value. CSV mode uses
`multipart/form-data` with `file` and optional `config` containing a JSON
object encoded as a string. CSV is UTF-8 with a unique nonblank header;
blank cells become null on input, while literal text such as `NA` initially
remains text. Existing downstream cleaning/type inference then applies.

Successful response:

```json
{
  "status": "completed",
  "persisted": false,
  "columns": ["revenue", "category"],
  "rows": [[100000, "A"], [200000, "B"], [150000, "C"]],
  "row_count": 3,
  "remaining_missing_values": 0,
  "report": {
    "rows_before": 3,
    "rows_after": 3,
    "columns_before": 2,
    "columns_after": 2,
    "steps_executed": [],
    "per_step": {},
    "column_types": {}
  }
}
```

The report above is abbreviated. Real responses include executed steps,
success/error state, row/column counts, inferred types, renamed columns,
duplicates removed and missing-value counts where the underlying pipeline
records them. The API always returns a report even if `generate_report`
was false. JSON has no DataFrame index; rows align with returned columns.

Missing values become JSON null, datetimes use ISO formatting, and numeric
columns retain their original units. pandas JSON uses up to 15 digits of
floating precision; this is not an exact arbitrary-precision money transport.
The ingestion design must account for exact decimal requirements before
saving canonical NUMERIC values. Category dtype retains labels rather than
turning them into integer codes.

## Configuration contract

Omitted steps/options use existing defaults. The API merges provided step
options into those defaults; a request need not repeat every setting.
Unknown step names or options are rejected, including removed normalization
or encoding steps. Every step accepts Boolean `enabled`.

| Step | Additional options |
|---|---|
| `clean_column_names` | None |
| `trim_whitespace` | `columns` list or null |
| `validate_required_columns` | `required_columns` list, `raise_on_missing` Boolean |
| `infer_column_types` | `categorical_threshold` between 0 and 1 |
| `remove_constant_columns` | None |
| `remove_duplicates` | `subset` list or null; `keep` first, last, or JSON false |
| `fix_data_types` | `type_map` object: int, float, str, datetime, category or bool |
| `parse_dates` | `columns` list/null, `formats` list/null |
| `handle_missing_values` | `columns`, strategy type_aware/drop/mean/median/mode/constant, scalar `fill_value` |
| `handle_outliers` | `columns`, method iqr/zscore, action cap/remove/flag, positive `z_threshold` |

Column references use names after `clean_column_names`. Unknown references
in selected columns, duplicate subsets or type maps are rejected before
running. Required-column checks additionally verify the final output so
constant removal cannot silently drop required keys and still report success.
If required validation is enabled, absent required columns cause failure at
the API boundary even when legacy `raise_on_missing` is false.

The legacy date function uses the first supplied format; a format list does
not provide a complete multi-format fallback parser. Type inference, mode
filling and conversion are heuristic and do not replace canonical schema
validation. Some legacy casts catch coercion problems internally, so a
successful step is not proof that all original values survived unchanged.
The API does not promise that every missing value is fillable; inspect
`remaining_missing_values` and the final data.

## Failure handling

The existing `run_pipeline` catches step exceptions and continues, returning
partial data. That legacy behavior remains available to existing callers.
The new service always requests a report and returns HTTP 422 if any step
reports an error. It does not return cleaned rows as accepted output in that
case. It also rejects an output with no rows/columns or missing required
columns after cleaning. Report counts/booleans remain typed on error.

| Status | Meaning |
|---|---|
| 200 | Completed cleaning; inspect remaining missing values and report |
| 400 | Invalid input structure, malformed CSV or configuration |
| 401 | Missing, invalid or expired access token |
| 405 / 415 | Unsupported method or content type |
| 413 | Adapter body/file/row limit exceeded |
| 422 | Step failure, empty cleaned output or missing required output columns |

CSV files and JSON bodies are bounded to 5 MiB. Table inputs support up to
10,000 rows and 200 columns, with at least one row/column. Column names must
be unique, nonblank strings up to 255 characters; cells must be finite scalar
JSON values or null. Ragged/nested data is rejected. Framework parsing also
has its own malformed/multipart limits. Overlong JSON arrays can be rejected
by serializer validation with 400 before the shared row guard runs.

## Calling from ingestion

In the same backend process, prefer direct Python calls:

```python
from apps.preprocessing.services import run

cleaned = run(columns=table["columns"], rows=table["rows"], config=chosen_config)
```

This service applies the API validation/failure policy. Existing
`run_pipeline(df, config=..., generate_report=True)` remains available to
DataFrame callers, but those callers must inspect step errors themselves.
`preprocess_csv(...)` remains a file-oriented helper with optional output,
chunking, backups and reports; HTTP processing does not enable its inplace
or backup modes and does not expose arbitrary server paths.

Ingestion must retain upload/store ownership, mapping review and original
field availability, resolve relationships/calculations, validate canonical
types/required values, save transactionally and trigger segmentation after
successful commit. Filling missing values does not prove genuinely observed
data exists for every segment. Do not infer eligibility merely because a
filled table has no nulls.

This package neither loads models nor calls the segment API. Reading
Customer 360 later retrieves stored business values and saved predictions
through analytics. A separate-origin frontend also needs the project's
chosen CORS policy or same-origin proxy arrangement.

## Tests and extension

From `backend/`:

```powershell
..\.venv\Scripts\python.exe -m pytest apps/preprocessing/tests -q
..\.venv\Scripts\python.exe -m pytest -q
```

API tests cover authentication, defaults, deduplication, median filling,
units/labels, CSV and JSON, partial config, malformed/nested input, unknown
options, row/body caps, null output, preserved outlier behavior, required
columns, empty output and explicit step failures. A mapping API test passes
approved table output directly into preprocessing. They do not contact
Supabase or Gemini.

Final verification: 42 new mapping/preprocessing API tests passed. Full
backend suite: 307 passed, 34 opt-in PostgreSQL KPI tests skipped, 3 subtests
passed, with 9 pandas date-inference warnings. Django system checks and
documentation links passed. No shared database data was changed.

To add a cleaning step, update the core pipeline/defaults, strict API option
serializer and tests together. Preserve the distinction between general
cleaning and model-specific feature preparation. Do not add schema writes
or replace real business amounts with model-scaled features here.

## Related documentation

- [Preprocessing README](../../../backend/apps/preprocessing/README.md)
- [Mapping implementation](../mapping/mapping-backend-implementation.md)
- [Customer 360 implementation](../analytics/customer360-backend-implementation.md)
- [Segmentation implementation](../segmentation/segmentation-backend-implementation.md)
