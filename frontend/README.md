# Customerly frontend

React + TypeScript + Vite frontend for the existing Django/Supabase backend.
It contains the full guided CSV upload flow, KPI dashboard, customer directory
and Customer 360, and the two current audience views. Campaign controls are
disabled and normal signup/signin remains deferred.

## Local startup

Use Node 22.17 or a newer compatible LTS release. From `frontend/`:

```powershell
npm ci
npm run dev
```

Run Django separately from the repository root:

```powershell
.venv/Scripts/python.exe backend/manage.py runserver 127.0.0.1:8000
```

Apply segmentation migrations once, then keep the audience worker running in
another terminal from the repository root:

```powershell
.venv/Scripts/python.exe backend/manage.py migrate segments
.venv/Scripts/python.exe backend/manage.py process_segment_jobs --watch --workers 2 --batch-size 100
```

**Prepare audiences** starts background jobs and shows separate progress for
churn and CLV. Without that worker, jobs stay queued. Results and memberships
are saved together in small batches; displayed counts poll the database rather
than waiting for the entire run. Failed jobs resume from their last saved batch.

Open `http://127.0.0.1:5173`. Vite proxies `/api` and `/health` to Django on
port 8000. Set `BACKEND_PROXY_TARGET` in `frontend/.env` if the backend uses a
different address. The dev server binds locally; no unrestricted CORS is needed.

## Accounts (sign in / create account)

The app opens on **Sign in**: **Continue with Google**, or an email + password.
New accounts name their store; teammates who sign in with Google using the
email they were added with go straight to that store. **Forgot password?**
emails a one-time reset link (printed in the Django terminal until Gmail is
configured). The backend
(`backend/apps/users`) keeps people signed in with an HttpOnly cookie and the app
renews the workspace token automatically. Apply `python manage.py migrate users`
once; the Google button appears when `GOOGLE_CLIENT_ID` is set in `backend/.env`
(see `backend/apps/users/README.md`).

To use operator-issued developer tokens instead, set `VITE_ACCOUNT_SIGNIN=false`
in `frontend/.env` (the browser tests do this), or choose **Use a developer
workspace token instead** on the sign-in page.

Use **Connect workspace** and enter an operator-issued store-user token:

```powershell
# From backend/; the store/user must already exist and belong together.
python manage.py create_ingestion_token --store-id <uuid> --user-id <uuid>
```

The command's output is a secret. The frontend only retains it in memory;
reloads require reconnection. Never use database credentials, a Supabase secret
key or the Django signing secret as the frontend token. Supabase login JWTs
are not accepted by the current backend authentication implementation.

## Workflow

Select/register a source → upload CSVs → automatic mapping → review fields,
relationships/calculations and optional audience policies → **Approve &
preprocess** → mandatory validation → save → inspect prediction progress.

The backend must have ingestion migrations and the existing model registry
initialized. Run `process_ingestion_jobs --watch` under a supervisor for
pending work. Gemini configuration is optional for manual review/exact-alias
mapping; missing Gemini credentials do not silently approve unknown columns.

## Checks

```powershell
npm run build
npm test
npm run test:browser
```

Browser tests use installed Microsoft Edge in headless mode and a contract
mock of backend responses. They never send operator tokens or write to Supabase.
Run the backend's `verify_ingestion` command separately for rolled-back live
database/model verification. Screenshots are written to ignored `test-results/`.

## Production hosting

`npm run build` produces `dist/`. Host it with a single-page-app fallback to
`index.html`, and forward `/api` to the Django deployment. The Vite development
proxy is not included in the static build; `vite preview` alone is not a full
production API gateway. Deploy API authentication, HTTPS, private upload storage
and a prediction worker as described in the backend guide. Normal account
signup/signin is required before real customer release.

See the [frontend implementation guide](../docs/implementation/frontend/frontend-implementation.md)
for screen/API contracts, state handling, styling and testing details.
