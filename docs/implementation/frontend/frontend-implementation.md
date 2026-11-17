# Customerly frontend implementation

**Current audience behavior:** Prepare audiences queues background jobs (HTTP 202). The page polls durable progress for churn and CLV separately and refreshes actual counts after saved batches. It no longer loops through model work in browser POST requests. See the latest background preparation section below.

Implemented: 17 November 2026. This is the repository-based frontend in
`frontend/`, connected to the existing Django API contract. It is not a hosted
Sites project or an implementation of signup/signin.

## Architecture and files

React, TypeScript and Vite power the app. React Router handles browser routes;
Lucide provides icons and Decimal.js formats monetary strings without first
converting them to floating point. Fonts are bundled locally through Fontsource,
not loaded from a third-party font server. SVG charts and illustrations are
native frontend components; no AI-generated customer/product photos are used.

| File/module | Responsibility |
|---|---|
| `src/App.tsx` | Sidebar, responsive navigation, header search, connection dialog, recent uploads and route boundary |
| `src/api.ts` | Shared authenticated requests, structured errors, query encoding and private downloads |
| `src/connection.tsx` | Memory-only operator token and reconnect/expiry UI |
| `src/hooks.ts` | Abortable read resources, refresh/polling and debounced search |
| `src/format.ts` | Decimal-safe display, source-unit formatting and dates |
| `src/types.ts` | Shared backend response contracts |
| `src/components/ui.tsx` | Consistent forms/status notices, modal focus handling, pagination, confirmation and campaign placeholder |
| `src/components/charts.tsx` | Source-aware area chart and customer/activity donut |
| `src/components/illustrations.tsx` | Blue/purple data illustration |
| `src/pages/Dashboard.tsx` | Overview and grouped full KPI catalog |
| `src/pages/Uploads.tsx` | Upload history, source/file submission, batch workflow and activity feed |
| `src/pages/Review.tsx` | Explicit field decisions, bindings, calculations, identities and model membership settings |
| `src/pages/Customers.tsx` | Customer directory, Customer 360 and paginated histories/order details |
| `src/pages/Audiences.tsx` | Churn/CLV cards and actual member lists |
| `src/styles.css` | Blue/white design, semantic accents and responsive layouts |
| `src/test/` | API transport, formatting and approval-policy tests |
| `tests/workspace.spec.ts` | Browser flow and responsive checks with contract fixtures |

The frontend makes same-origin `/api` requests. No server implementation,
database credentials or table-write logic is copied into the browser. Backend
authorization, canonical validation, prediction eligibility and saving remain
authoritative.

## Routes and visual design

| Route | Screen |
|---|---|
| `/` | Overview dashboard |
| `/metrics` | All 19 KPI cards grouped by category |
| `/uploads` | Paginated import history |
| `/uploads/new` | Source selection/registration and multiple CSV upload |
| `/uploads/:id` | Detected-data review, mandatory preprocessing/save, reports and activity |
| `/customers` | Searchable paginated customer directory |
| `/customers/:id` | Customer 360 with overview and history tabs |
| `/audiences` | Two current audience cards |
| `/audiences/:key` | Paginated membership list and summary |

Visual references informed the white sidebar, light blue canvas, rounded
white cards, sparse illustrations and customer-profile structure. Teal, purple,
amber and green distinguish data/state without replacing textual labels.
Charts only use values returned by the API; no demonstration records are
silently mixed into the connected workspace.

Disconnected screens use useful empty states rather than fabricated totals.
The temporary connection dialog is isolated so a future authentication flow
can replace it. Customer avatars use available initials/identifiers; absent
photos, names, birthdays and other unsupported fields are not invented.

## Authentication and transport

An operator must issue an existing backend store-user token. The connection
dialog verifies it through `GET /api/ingestion/sources/` before activating it.
The token remains in React memory: it is not hardcoded, put in a URL, saved
in local/session storage or printed to logs. Page reloads intentionally require
reconnection. Replacing a token invalidates read resources; disconnecting clears
the protected view. Current APIs do not yet accept Supabase login JWTs.

Requests send Bearer authorization. JSON requests set Content-Type;
multipart requests let the browser set its boundary. GET uses no-store and
abortable resource reads. No automatic POST retry is performed. A 401 opens
reconnection, while typed errors preserve full validation response data so
the UI can show the backend report.

## Upload and approval flow

1. Select an existing source or create one with an actual namespace, data
   profile and currency. Metadata remains subject to backend validation.
2. Select/drop CSVs. Frontend checks `.csv`, the 10-file limit, 5 MiB per file
   and 20 MiB batch cap. The backend still validates encoding, structure,
   headers, row counts and content.
3. Send repeated `files` multipart fields, `source_id` and a generated
   `request_key`. Preserve the same key after a failed request; changing files
   or selected source generates a new key.
4. Navigate to the returned batch and automatically start mapping for a new
   uploaded batch. Resuming an already mapped/saved batch does not remap it.
5. Display each original attribute with the proposed destination and method.
   Confidence is explanatory, never approval. The user explicitly approves,
   corrects or ignores primary mappings. Bulk approval is an explicit click.
6. Advanced settings allow source-key bindings, supported date/multiply/channel
   calculations, occurrence identity columns and opt-in exact-row deduplication.
   Controls mirror the backend's closed supported operations; they do not send
   arbitrary expressions. Every file needs applicable approved input; an entirely
   unrelated file requires starting a batch without it.
7. Churn is enabled by default, matching ingestion. CLV requires declared GBP
   source data plus an explicit observation date. Membership updates require
   a user-entered threshold. No threshold is silently chosen for either model.
8. **Approve & preprocess** submits the current review, runs validation, then
   calls process only after success. A 422 displays the saved validation report
   and never triggers process. Already validated batches can resume saving,
   which still invokes the backend's mandatory save-time revalidation.
9. Only a successful save response displays the checkmark/confetti dialog.
   It distinguishes saved data from pending predictions. Failed prediction
   work does not present the already imported records as lost.

The form is locked during submission. The original business values/identifiers
remain protected by ingestion's policy; this interface never says filling or
deduplication occurred unless supported by the actual report/events. Standalone
preprocessing defaults are not substituted for the protected import policy.

## Progress, restoration and retries

Batch detail supplies status/revision/active operation. The events endpoint
supplies ordered real timestamps. The batch page accumulates cursor events
and polls while a request, active operation or prediction work remains. The
feed is not a fabricated timer or a percentage estimate. Backend step events
can arrive after the stage transaction commits, as documented in ingestion.

Upload history can reopen durable reviews/reports. Client state is not the
authoritative saved review; the frontend consumes returned revisions instead
of guessing them. Errors remain actionable, downloads use authenticated blob
requests, and job retries use `retry-segments/`, not another import.

Async batch reads are generation-checked across route/token changes so a late
response cannot replace a newer customer's/store's view. Read-resource hooks
abort obsolete requests. The backend's revision/operation locks continue to
protect writes even if the user reloads during a request.

## Dashboard and metrics

Overview uses the existing total-customer, total-revenue, total-order,
average-order-value and revenue-by-month cards, audience summaries and latest
batch history. The all-metrics screen loads the KPI catalog and renders its
categories and permitted breakdown options.

Customer totals may combine disjoint source counts in the selected scope;
money is displayed separately per source/currency. Revenue charts select one
source rather than combining unlike currencies. Source unavailability reasons
remain accessible. No arbitrary previous-period growth percentage or unsupported
date filter is displayed.

The frontend renders charts; their aggregation comes from backend results.
It never computes whole-store totals from one paginated customer table. Monetary
strings use Decimal for formatting; model probabilities are displayed as
percentages with their saved computation time and context.

## Customer 360

Directory search supports source IDs, email and phone with source filtering.
Profile reads are read-only and never trigger prediction. Sections include
available contact attributes, per-source actual purchases, behavior counts,
stored churn/CLV results, memberships and optional stored feature snapshots.
Orders, events, messages and coupons load in separate paginated tabs. Order
detail shows original stored quantities, prices and discount fields without
subtracting discounts twice.

Unavailable predictions and absent data have explicit states. Actual observed
net revenue is separate from future CLV. Messaging purchase flags are not
invented order-attributed revenue. Feature timestamps remain visible because
opening a profile does not refresh the stored feature row.

## Audience cards and memberships

Exactly two current segment types are shown: churn risk and future high-value /
CLV. Cards show actual membership counts, availability/configuration state and
last computation time. Clicking opens the saved membership list; customers link
to their existing Customer 360 pages. Predictions alone do not create membership.

Each card has a grey disabled Create campaign button, lock icon and accessible
Coming soon tooltip. No campaign sending, editing or fake activation is wired.
Membership policy is configured explicitly in upload review; there is no hidden
rule assigning all customers with a prediction to an audience.

## Responsive behavior and accessibility

Desktop uses a fixed sidebar and roomy card grids. Tablet layouts collapse
content columns; mobile uses a toggled sidebar, stacked panels and horizontally
scrollable data tables. Forms have labels, keyboard focus is visible, modals
trap/restore focus and close on Escape, and navigation has a skip link. Reduced
motion disables confetti and other animation. SVG charts include labels and
focusable observations, with textual legends/captions.

## Startup, deployment and verification

See the [frontend README](../../../frontend/README.md) for commands. Vite serves
locally on port 5173 and proxies API/health paths to Django on port 8000, with
an optional backend-only environment override. Static production hosting needs
an SPA fallback and an actual `/api` reverse proxy; the dev proxy is not part
of `dist/`. Normal user authentication, HTTPS/deployment configuration, private
backend storage and worker supervision remain release prerequisites.

Build/type checks use `npm run build`; Vitest covers request/auth/multipart
behavior, non-retried writes, validation propagation, exact money and approved
field selection. Playwright checks desktop/mobile layouts, token connection,
upload → mapping → approval → validation → save, failed-validation blocking,
and audience/campaign behavior. Browser fixtures mirror the API contract and
do not write shared Supabase. Existing backend live verification remains a
separate check; a fixture test is not evidence that real Gemini is configured.

Final browser/build verification results are recorded after checks complete.

## Related guides

- [Ingestion implementation](../ingestion/ingestion-backend-implementation.md)
- [Customer 360 backend](../analytics/customer360-backend-implementation.md)
- [Segmentation and audience APIs](../segmentation/segmentation-backend-implementation.md)

### Automatic prediction refresh

PredictionRefresh runs after connecting/reloading, independently of the current page. It POSTs `/api/segments/refresh/` sequentially until remaining=false, stops on errors/disconnection, and refreshes displayed resources when complete. Existing saved predictions, including unavailable outcomes, are reused. The development token remains memory-only, so a browser reload still requires reconnection. Generated predictions do not invent membership policies; member counts and saved prediction counts remain distinct. See segmentation implementation notes for currency and historical observation-date rules.

### Explicit audience preparation (current behavior)

The Prepare audiences header button now opens a CLV revenue-cutoff dialog, then calls POST /api/audiences/prepare/ through prediction generation and paginated membership assignment. Churn uses the model's positive class; CLV uses the user-entered GBP cutoff. Progress and errors are shown, saved predictions are reused and customer lists read the resulting memberships. This replaces the automatic connection-time PredictionRefresh trigger. Non-JSON API responses now include HTTP status and endpoint with restart guidance instead of the generic unexpected-response message.

### Diagnosing proxy failures

The Vite development proxy now returns a JSON 502 explanation and logs a safe upstream error code when Django cannot be reached or its connection breaks. Restart npm run dev to load this configuration. Segmentation progress logs are in the Django terminal, with per-batch run IDs and detailed stages; see segmentation implementation notes. This does not establish the cause of a particular 502 without inspecting the running servers.

## Background audience preparation (current)

The Audiences header button still asks for an explicit CLV revenue cutoff, then sends one POST to /api/audiences/prepare/. HTTP 202 returns durable jobs rather than making the browser wait for model execution. There is no repeated browser loop performing inference.

The page polls /api/audiences/jobs/ and /api/audiences/ every two seconds after each previous response completes. Churn and CLV each show waiting/processing/completed/failed, checked/total customers, predicted/unavailable/reused counts and last update time. Member pages also poll for saved customer lists. These are frequent database-backed updates after each committed batch, not WebSocket events or per-row animation. Failed segments show a safe error code; the same button resumes their checkpoint. Completed segments notify resource hooks once per job pair, avoiding refresh loops. Token changes discard preparation UI state and stale POST responses.

A background worker must run in a separate terminal: python backend/manage.py process_segment_jobs --watch --workers 2 --batch-size 100. If it is absent, queued jobs remain visible as waiting for a worker. Browser closure or reconnection does not stop the jobs. Production needs a supervised worker process. The operational table migration was applied to the configured database. Existing normal sign-in remains deferred.

Audience counts come from real saved memberships. Churn uses the classifier's positive label; CLV uses the requested GBP cutoff. The backend bulk-saves predictions, memberships and job counters in the same transaction. Old predictions are reused; incomplete data is shown as unavailable rather than fabricated. See the latest segmentation implementation section for the queue contract, lease recovery, bounded histories and benchmark results.

### Customer-specific availability in Customer 360

The profile now hides unavailable or unscored prediction cards, sections with no recorded purchase/activity/message/coupon data, empty audience memberships, missing contact fields, and null/blank recorded attributes and stored features. History tabs are offered only when the summary reports records for that customer. If a selected history tab becomes unavailable after refreshing or switching customers, the view falls back to Overview. A remaining single card spans the available grid width.

This is frontend presentation only: no database values, eligibility rules, API results or prediction outputs change. Genuine numeric zero and boolean false values remain visible when they belong to recorded data, including valid zero-valued model predictions. A zero activity count with no events hides the behavior card instead of suggesting that missing evidence proves no behavior. The frontend production build passed.
