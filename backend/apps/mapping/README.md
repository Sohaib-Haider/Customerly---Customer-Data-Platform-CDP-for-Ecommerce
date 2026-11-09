# apps/mapping — Gemini-assisted canonical column mapping

Django app `apps.mapping` (the README's "LLM column-mapping logic"). Run every
command below from `backend/`. Inside the Django project the same CLI is
available as `python manage.py map_columns <verify|map|review|apply> ...`
once `config/settings` lists `apps.mapping` in `INSTALLED_APPS`.

Real-data evaluation: `eval/ground_truth.json` is the answer key for the 171
columns of the six Kaggle source datasets; score a folder of mapping JSONs with
`python -m apps.mapping.eval.score <dir>`.

Standalone, framework-free Python component of the Customerly CDP final-year
project. It maps the CSV columns of a **future store upload** onto the
canonical schema defined in `backend/schema.sql` (23 tables), using:

1. **exact matching** against canonical column names,
2. **documented aliases** seeded from the existing mapping documentation
   (`segments-schema-mapping.md`, `DUNNHUMBY.md`, `BEHAVIOR_AND_RETAIL.md`,
   `data_prep/transform/*.py`), and
3. **Google Gemini** (official `google-genai` SDK) — only for columns that
   remain unknown or ambiguous.

It integrates with — but changes nothing in — the existing offline
transformation pipeline: `backend/schema.sql` is read-only here, and no file
under `data_prep/transform/` is touched. There is **no Django, no frontend,
and no database connection** anywhere in this package.

```
verify  ->  map  ->  review  ->  apply
(parsing)  (exact/alias/LLM)  (human approval)  (approved-only output)
```

## Install

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements/dev.txt   # google-genai + pytest (run from backend/)
```

## Configure the API key

```bash
copy .env.example .env      # in backend/, then fill in your key
```

| Variable         | Purpose                                             |
|------------------|-----------------------------------------------------|
| `GEMINI_API_KEY` | Gemini API key; read from `.env` or the environment. |
| `GEMINI_MODEL`   | Model name (default `gemini-3.8-flash`; the API error message names a valid model if yours is retired). |

Security properties enforced by this package:

* `.env` is gitignored (`.gitignore` line 4; `.env.example` is the committed
  template and contains no secret);
* the key is passed only to the SDK client constructor — never into prompts,
  JSON documents, logs, or test fixtures;
* every error string surfacing to stdout/stderr/files passes
  `apps.mapping.config.scrub()`, which redacts the key defensively.

## Usage

```bash
# 0) cross-check schema.sql <-> final-schema.md <-> alias dictionary
python -m apps.mapping verify [--show-targets]

# 1) map a CSV's columns (Gemini is used only for the unresolved remainder)
python -m apps.mapping map path/to/upload.csv -o upload.mapping.json
python -m apps.mapping map path/to/upload.csv --no-llm          # offline mode
python -m apps.mapping map path/to/upload.csv --kind transactions  # override kind

# 2) human approval / correction (the ONLY path to `approved`)
python -m apps.mapping review upload.mapping.json --interactive
python -m apps.mapping review upload.mapping.json \
    --approve col_a,col_b --set col_c=table.column --reject col_d \
    --approve-exact --kind transactions

# 3) apply ONLY approved direct mappings -> one CSV per canonical table
python -m apps.mapping apply upload.mapping.json upload.csv -o out/
```

Try it on the bundled sample (fake data, intentionally unfamiliar column
names):

```bash
python -m apps.mapping map apps/mapping/samples/store_upload_sample.csv
```

## How a mapping is represented

`method` (how the target was found) and `status` (where review stands) are
strictly separate axes — **Gemini confidence never implies approval**:

| method                 | meaning                                                |
|------------------------|--------------------------------------------------------|
| `exact`                | source name equals a canonical column name             |
| `alias`                | documented mapping from the existing mapping docs      |
| `llm`                  | proposed by Gemini, validated against the schema gate  |
| `manual`               | set by a human in `review`                             |
| `none`                 | no target found (never invented)                       |

| status                  | meaning                                                       |
|-------------------------|---------------------------------------------------------------|
| `suggested`             | confident exact/alias/LLM result — still awaiting approval    |
| `needs_review`          | ambiguous, decision-entangled, calculated, or low-confidence  |
| `approved`              | explicit human approval (the only state `apply` executes)     |
| `rejected`              | human veto (kept for audit, excluded from persistence)        |
| `unmapped`              | no acceptable canonical target exists                         |

Additional fields per mapping: `confidence` (LLM only), `explanation`,
`requires` (`relationship_resolution` / `calculation`), `decision_note`
(open schema decisions), `alternate_targets` (documented one-source-to-many-
target cases such as `DAY -> orders.day_no + orders.order_date`), `candidates`
(shown when a column is ambiguous), `review_note`, `corrected_from`.

## What may be mapped (provenance-gated)

The eligible target set is **derived from the real files at load time** — not
hardcoded — and printed by `verify --show-targets`:

* columns marked **`[S]`** (source/uploaded) in `docs/notes/schema-notes/final-schema.md`,
* on the 11 file-ingestible tables (customers, products, orders, order_items,
  events, marketing_campaigns, message_sends, coupon_campaigns,
  coupon_redemptions, holidays, causal_data),
* plus FK columns documented as relationship-resolution targets
  (e.g. `events.customer_id`), which are always flagged
  `requires: relationship_resolution`.

Everything else is structurally unreachable: `[P]` platform fields, `[C]`
computed/ML fields (`customer_features.*`, `channel_norm`, ...), platform
tables (`stores`, `store_users`, `ingestion_*`), and fields whose provenance
is still an open decision (e.g. `orders.region`).

## Safety properties

* **No PII leaves the machine.** The prompt carries column names + locally
  computed metadata (dtype, null share, distinct count, value-shape tags,
  likely-PII flag) — never cell values. See `profile.py` and
  `test_prompt_contains_metadata_but_never_raw_values`.
* **Every suggestion passes the schema gate** (`SchemaCatalog.validate`), so
  hallucinated tables/columns are discarded (`invalid_target_discarded`).
* **Graceful degradation.** Missing key, HTTP 401/403, rate limits, API
  errors and invalid model JSON never crash a run: exact/alias results are
  preserved and the rest becomes `needs_review` with a scrubbed reason.
* **Apply is approval-gated.** Only `approved` mappings with empty `requires`
  are written; relationship-resolution and calculated mappings are reported
  as skipped, not silently applied.

## Database contract (future ingestion service)

`MappingResult.to_ingestion_column_mappings_rows(upload_id)` emits rows shaped
exactly like `ingestion_column_mappings` (`upload_id`, `source_column`,
`target_table`, `target_column`, `confidence`) — **emitted, never written**;
this package holds no DB connection. Note the table's
`UNIQUE (upload_id, source_column)` constraint allows one persisted target per
source column, so documented one-source-to-many-target mappings live in the
JSON's `alternate_targets` and must be handled by the loader.

## Tests

```bash
.venv\Scripts\python -m pytest apps/mapping/tests   # from backend/
```

70 offline tests cover: schema parsing/provenance gating, exact + alias
resolution (incl. ambiguity and multi-target cases), kind detection, the
mocked Gemini stage (semantics, thresholds, invalid targets, null answers,
rate limits, missing key), the review/approve/correct/reject flow, the
apply-only-approved gate, and the DB-contract emitter. No test touches the
network or a real credential.

## Known limitations

* Kind detection is heuristic; it is surfaced for review and can be
  overridden (`--kind`), but a wrong override can steer kind-scoped aliases.
* One source column's documented second targets (`alternate_targets`) are
  informational in this JSON — the DB contract cannot persist them.
* The mapper proposes renames only: id-namespacing, FK resolution, date
  arithmetic and validation all remain in `data_prep/transform/` (the offline
  stage), which runs after mapping for future uploads.
