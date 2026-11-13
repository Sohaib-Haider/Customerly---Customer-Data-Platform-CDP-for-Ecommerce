# Backend development setup

Run these PowerShell commands from the repository root using Python 3.13:

```powershell
.venv/Scripts/python.exe -m pip install -r backend/requirements/dev.txt
# Copy only if backend/.env does not already exist; preserve existing secrets.
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
.venv/Scripts/python.exe backend/manage.py check
.venv/Scripts/python.exe backend/manage.py runserver
```

Open `http://127.0.0.1:8000/health/`. This is a database-independent liveness
endpoint, not a check of Supabase connectivity or schema readiness.

## Settings and credentials

Management commands, ASGI and WSGI default to `config.settings.dev`. Settings
read `backend/.env`; process environment variables take precedence. The local
development secret is a fallback only. Production requires a private
`DJANGO_SECRET_KEY`, explicit `DJANGO_ALLOWED_HOSTS`, and `DATABASE_URL`, with
`DJANGO_SETTINGS_MODULE=config.settings.prod` set in the deployment environment.
Production assumes HTTPS; trusted proxy headers must be configured for the
actual hosting environment before deployment. `runserver` is for local use only.

Without `DATABASE_URL`, development uses Django's disabled database backend.
Startup and `/health/` work, but database operations intentionally fail. No local
database file is created as a fallback.

When your teammate supplies access, put the existing project's PostgreSQL
Session pooler URL in your own `backend/.env` as `DATABASE_URL`. Use the exact
host, port and username from Supabase's Connect panel and a URL-encoded database
password. PostgreSQL connections default to `sslmode=require` and no persistent
Django connections. Database credentials are distinct from Supabase API keys
and account passwords. Never commit `.env` or share credentials in logs.

This foundation does not execute migrations, load CSVs, or create canonical
tables. Verify the shared database schema and settle SQL/migration ownership
before doing any of those operations. Framework auth/session tables are not
created by registering their apps. No authentication endpoints are implemented.

## Verification

```powershell
.venv/Scripts/python.exe backend/manage.py check
Push-Location backend
../.venv/Scripts/python.exe -m pytest
Pop-Location
```

Pytest uses `config.settings.test`, which overrides database configuration with
an isolated in-memory SQLite database. It never connects to Supabase. Do not
override the test settings with shared database settings. For Django's test
runner, explicitly use `--settings=config.settings.test` as well.

Mapping remains available standalone (`python -m apps.mapping ...` from
`backend/`) and through `python manage.py map_columns ...`. Placeholder domain
folders are not registered yet. Segmentation now has database integration,
prediction storage and authenticated APIs; see [its setup guide](apps/segments/README.md).
Use Python 3.13.3 for the current artifacts. Serialized-model dependencies are
pinned to their recorded versions and checked before inference.

The mapping `verify` command currently reports an existing provenance mismatch
for `orders.region` in the schema documentation and exits nonzero. This is a
schema-review finding, separate from Django startup; the foundation does not
change the schema or mapping rules to hide it.
