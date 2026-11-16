# Preprocessing app

General data cleaning before ingestion saves canonical business records.
The existing ten default steps remain enabled; model scaling and category
encoding belong to segments.

Authenticated endpoints:

- `GET /api/preprocessing/config/` — ordered steps, defaults and limits.
- `POST /api/preprocessing/run/` — CSV upload or JSON `columns`/`rows`, with
  optional per-step config; returns cleaned data and a report.

Uses existing store-scoped Bearer authentication. No database reads/writes.
Step errors return 422 rather than accepted partial output. Existing IQR
outlier capping can change extreme values; configure it deliberately.

See the [full implementation and API guide](../../../docs/implementation/preprocessing/preprocessing-backend-implementation.md)
for request examples, options, errors, pipeline behavior and ingestion use.
