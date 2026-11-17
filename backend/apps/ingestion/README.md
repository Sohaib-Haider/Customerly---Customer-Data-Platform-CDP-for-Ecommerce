# Ingestion manager

Coordinates private multi-CSV uploads, mapping review, protected cleaning,
canonical validation, transactional Supabase saving and durable segmentation.

**Flow:** CSVs → mapping → approval and relationships → protected cleaning →
canonical validation → Supabase save → segmentation → Customer 360 / KPIs.

Use `POST /api/ingestion/sources/`, then `/api/ingestion/batches/`. Each batch
has `map/`, `review/`, `validate/`, `process/` and `retry-segments/` actions.
Requests require a signed store-user token and current review revision.

From `backend/`:

```powershell
python manage.py migrate ingestion
python manage.py initialize_segments
python manage.py create_ingestion_token --store-id <uuid> --user-id <uuid>
python manage.py process_ingestion_jobs --watch
```

Token output is secret. Store and store-user records must already exist;
this is operator-issued access until normal account login is implemented.
The new token also works with existing analytics and segment APIs.

Private CSVs live in `INGESTION_PRIVATE_ROOT` (default
`backend/private_uploads/`, gitignored). Do not serve that directory publicly.
Run `purge_ingestion_uploads` for completed files older than 30 days.

See the [complete implementation and API guide](../../../docs/implementation/ingestion/ingestion-backend-implementation.md)
for exact requests, schema rules, worker operation, supported layouts,
limitations, tests, and frontend integration.
