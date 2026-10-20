# data_prep/transform

Offline transforms: raw Kaggle CSVs → canonical-schema CSVs (`backend/schema.sql`).

Transformed data exists **only to test the platform end-to-end** (upload → column mapping →
feature computation → dashboards, minus ML segment prediction). ML training runs separately
on the raw per-segment datasets, so these scripts do **not** compute features or aggregate —
they clean, map, namespace ids, align dates, and shape rows.

| File | Member | Notes |
|---|---|---|
| `common.py` | shared | id namespace + per-dataset date strategy — the single source for both; extend it instead of re-deriving |
| `dunnhumby.py` | A | Segments 6/7/8 — see `DUNNHUMBY.md` (mappings, gaps, Open Decisions touched) |
| `validate.py` | shared | NOT NULL / PK / UNIQUE / FK / calendar checks before anything is loaded |
| `run_dunnhumby.py` | A | CLI, writes one CSV per canonical table + `audit.json` |
| `fixtures/dunnhumby/` | A | synthetic source CSVs for dry runs (`data_prep/raw/` and `data_prep/processed/` are gitignored) |

Conventions every transform must follow:

1. **One namespace prefix per source dataset** (`common.SOURCE_NAMESPACES`) so ids from
   different datasets can never collide inside `UNIQUE (store_id, source_*)`.
2. **Deterministic ids** — `common.uid(table, key)`; re-running must produce identical
   output so reloads are idempotent.
3. **A date strategy per dataset** — `common.DATASET_ANCHORS` + `common.anchor_for()`.
   Day-number sources declare their own anchor; real-date sources use
   `window_offset()`/`shift_date()` instead. **Never reuse another dataset's anchor** —
   the global `ANCHOR_DATE` was removed precisely because it let a real-date source be
   shifted by a synthetic one.
4. **No silent drops** — every removed source row is counted with a reason and a source
   file tag, and every column with no canonical target is reported as a note
   (`audit.json → notes`).
5. **Evidence, not invention** — mappings cite `final-schema.md` and
   `segments-schema-mapping.md`; conflicts with the schema are recorded against the
   relevant Open Decision, never resolved locally. `docs/decisions/data-transformation-plan.md`
   is excluded and must not be used as a source.
6. **Preserve behaviour** — no rescaling, filtering for cleanliness, or ML features; returns
   and negative values stay.
