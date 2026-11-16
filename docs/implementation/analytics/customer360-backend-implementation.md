# Customer 360 backend implementation

Implemented and verified: 16 November 2026.

This document explains the Customer 360 architecture, database reads, API
contract, frontend integration, tests, and extension points.

## Architecture

Customer 360 lives in the existing analytics Django app. `customer360` is a
Python package containing modules; it is not an additional Django app.

```text
backend/apps/analytics/
├── kpis/                         Existing 19 store-level KPI cards
├── customer360/
│   ├── __init__.py
│   ├── selectors.py              Parameterized, scoped database reads
│   ├── services.py               Profile assembly and JSON conversion
│   ├── serializers.py            Query validation and pagination
│   ├── views.py                  Authentication, HTTP responses and errors
│   └── urls.py                   Customer API routes
├── tests/
│   └── test_customer360.py        Real selector and HTTP tests
├── urls.py                       Includes Customer 360 routes
└── views.py                      Existing KPI views
```

Customer 360 assembles existing records when requested. It does not store
another copy of customer data or calculate model predictions. The existing
KPI engine computes store aggregates; separate customer-scoped queries power
the profile so store totals never appear as personal metrics.

```text
Upload → mapping → preprocessing → canonical database save
                                      ↓
                         explicit segment execution
                                      ↓
                         saved results / memberships
                                      ↓
Frontend → authenticated Customer 360 GET → existing customer data + results
```

The upload coordinator and automatic prediction triggers remain separate
work. Customer 360 can read data already present in the database.

## Database configuration and sources

This package uses the existing Django PostgreSQL connection configured from
`backend/.env`. It needs no additional Supabase client or API key. The frontend
calls the backend and must never receive database credentials.

| Table | Read purpose |
|---|---|
| `customers` | Canonical profile, contact fields and recorded attributes |
| `orders`, `order_items` | Actual purchases, original prices, sales and discounts |
| `products` | Product names, categories, brands and departments |
| `events` | Behavioral summaries and history |
| `message_sends` | Messaging summaries and history |
| `coupon_redemptions` | Recorded coupon outcomes and history |
| `segment_predictions` | Current saved predictions and unavailable results |
| `customer_segments`, `segments` | Explicit memberships, names and assignment times |
| `customer_features` | Optional stored feature snapshot and computation time |

No tables or migrations were added. The canonical schema and existing
segmentation migrations must already be installed. Missing storage causes
503 rather than a misleading empty profile. Customer 360 does not read
store-level `kpi_snapshots` as customer KPIs.

Every customer data query binds the authenticated store UUID. Customer
details also bind customer UUID, and order details additionally bind order
UUID. Product metadata joins require the product to belong to the order's
store. Request values never become table or column names; history table and
sort-column names come only from a closed registry.

## Authentication

All endpoints require the same store-scoped service token currently used by
segmentation and KPI APIs, through `SegmentTokenAuthentication`:

```http
Authorization: Bearer <store-scoped-token>
```

The authenticator validates the signature, purpose and expiry. The current
default token lifetime is one hour. The token's store controls access;
supplying a different `store_id` in query parameters cannot change it.

A trusted backend operator can issue a token with the existing command:

```powershell
python manage.py create_segment_token --store-id <store-uuid>
```

Tokens are credentials. Never embed a permanent service token, database
password, or signing secret in a frontend bundle. Future platform login
should provide the appropriate authenticated store principal. The present
service token can read customers across its store; individual staff roles
and permissions are outside this package.

Missing and foreign-store customers return identical 404 responses. An
order belonging to a different customer also returns 404. A store with no
matching customers gets an empty list. Successful responses include
`Cache-Control: private, no-store` because they may contain contact details.

## Endpoints

All endpoints below use GET. They accept no uploads or prediction requests.

| Path | Purpose |
|---|---|
| `/api/customers/` | Search and select customers |
| `/api/customers/{customer_id}/360/` | Combined customer profile and summaries |
| `/api/customers/{customer_id}/orders/` | Paginated order summaries |
| `/api/customers/{customer_id}/orders/{order_id}/` | Order summary and paginated items |
| `/api/customers/{customer_id}/events/` | Paginated behavior events |
| `/api/customers/{customer_id}/messages/` | Paginated message records |
| `/api/customers/{customer_id}/coupons/` | Paginated coupon records |

The existing segmentation endpoint
`/api/customers/{customer_id}/segments/` is unchanged. The combined 360
response supplies saved predictions and explicit memberships together.

### Customer search

```http
GET /api/customers/?search=example.com&source=ret&limit=25&offset=0
```

`search` optionally matches a case-insensitive literal substring of source
customer ID, email or phone number. Maximum length: 255. `%` and `_` are
escaped literal characters rather than wildcard operators.

`source` optionally filters a namespace such as `ret`, `dnb`, `chr`, `evb`,
`msg` or `cpn`. It accepts letters, digits, underscore and hyphen, up to 50
characters. A valid unknown namespace simply returns no matching customers.

Results include canonical customer columns and a `source` object with
`code`, `name` and `currency`. They sort by source ID and UUID. There is no
customer display-name column in the current schema; use a source ID or
available contact information in the frontend.

### Pagination

Every list/history endpoint, including order-item details, accepts:

| Parameter | Default | Allowed values |
|---|---|---|
| `limit` | 25 | Integer from 1 to 100 |
| `offset` | 0 | Nonnegative integer |

```json
{
  "count": 120,
  "limit": 25,
  "offset": 0,
  "results": []
}
```

`count` is the total matching record count, and `results` is the requested
page. History responses also include `customer_id`. An offset after the
final record returns an empty page with count preserved.

Orders sort by order date descending and UUID. Other histories sort by
their timestamp descending and UUID. Items sort by UUID. UUIDs break ties
deterministically. Offset pagination is not a frozen snapshot across
requests; ongoing ingestion can change subsequent pages.

### Combined profile

```http
GET /api/customers/{customer_id}/360/
```

| Response field | Meaning |
|---|---|
| `customer` | Canonical attributes and nullable contact fields |
| `source` | Customer namespace, known dataset name and currency |
| `purchases.status` | `available` if order headers exist, otherwise `no_records` |
| `purchases.by_source` | Purchase summaries separated by order-source namespace |
| `activity.events` | Counts and first/last timestamps by recorded event type |
| `activity.messages` | Sent, opened, clicked, purchase-flag and unsubscribe counts by normalized channel |
| `activity.coupons` | Recorded row count and redeemed row count |
| `segments.predictions` | Saved current results with status and computation time |
| `segments.memberships` | Explicit saved memberships, scores and assignment times |
| `feature_snapshot` | Existing saved row, or `not_computed` if absent |
| `notes` | Guidance on coverage, predictions and freshness |

Purchase summary fields include `recorded_orders`, `purchase_orders`,
`nonpositive_orders`, `orders_without_lines`, `observed_net_revenue`,
`average_order_value`, `first_purchase_date`, `last_purchase_date`,
`observed_from`, `observed_to`, source metadata and any average-unavailability
reason. History arrays are fetched separately to keep the profile bounded.

Each prediction includes `segment_id`, `name`, `segment_key`, `status`,
`observation_date`, `computed_at` and the full saved `result` document.
The result preserves prediction values, eligibility, reasons and model
metadata where recorded. A missing prediction is an empty list entry set;
an unavailable prediction remains unavailable, never a zero score.

Prediction and membership lists are distinct. Producing a prediction does
not automatically assign the customer to a segment; membership requires
explicit evaluation through the segmentation execution policy.

### Order details and other histories

Order lists provide recorded header data, source metadata, line count, net
revenue and merchandise-unit count. Order detail has this shape:

```json
{
  "customer_id": "<uuid>",
  "order": {"id": "<uuid>", "source": {"code": "ret", "currency": "GBP"}},
  "items": {"count": 4, "limit": 25, "offset": 0, "results": []}
}
```

Items include original quantities, unit prices, sales, discounts and product
metadata. Event/message/coupon histories return their canonical record
columns. Message records include campaign IDs and saved engagement flags;
coupon records include coupon/campaign IDs and redemption status.

No missing campaign attribution, contact consent, delivered/open timestamp,
or offered-coupon denominator is invented. Coupon-source synthetic order
records must not be presented as genuine multi-item baskets.

## Financial, source and availability rules

- Revenue sums recorded line `sales_value`. Discounts are not subtracted a
  second time. Original values are not scaled, normalized or rewritten.
- Positive net order totals count as purchase orders and determine average
  order value. Nonpositive orders remain visible and net into actual revenue.
- Orders without lines remain visible with an unknown total, rather than
  becoming zero-value purchases.
- First/last purchase dates use positive purchase orders; observed date
  bounds include all order headers.
- Purchase summaries group by order-source namespace. GBP and USD are not
  added together into one customer lifetime total.
- Source metadata reuses `analytics/kpis/sources.py`: `ret` is GBP and `dnb`
  is USD. Unverified currencies stay null; unprefixed customer IDs use
  `unknown` metadata. No currency conversion occurs.
- Coupon-source average order value is null with a reason because those
  records represent synthetic transaction lines.
- Merchandise units exclude `KIOSK-GAS` and `MISC SALES TRAN`, matching KPI
  conventions. Those departments' revenue remains included.
- Actual observed revenue is separate from future predicted CLV, whose
  currency, horizon and model identity remain in its saved result.
- Similar IDs or contact fields do not merge independent source populations.
- Message purchase flags are not linked order revenue. Coupon rows can be
  redemptions only, so no unsupported redemption rate is calculated.

Namespace currency metadata follows the current transformed-source contract.
The canonical schema has no currency column; future datasets need an explicit
source/currency contract before the backend can label their money safely.

PostgreSQL NUMERIC values are returned as decimal strings, preserving
precision: for example `"100000.123456789"`. Counts stay integers, booleans
stay booleans, and model values already saved as JSON numbers retain that
representation. The frontend should use a suitable decimal/money formatter.

Dates and timestamps use ISO format. Source-local timestamps without a time
zone stay without a time zone. UUIDs are strings; missing fields remain null.

No records means no stored evidence, not proof that the customer never
performed an action. An empty coupon summary has count zero and can have a
null redeemed sum. A missing feature row has `status: not_computed` and
`data: null`. An existing row has `status: stored` and retains `computed_at`.
The endpoint neither refreshes features nor resolves the schema's existing
feature-grain and refresh decisions.

The combined profile performs multiple reads rather than a frozen database
snapshot. Concurrent ingestion/scoring may update different sections between
reads. Use saved timestamps to communicate freshness; opening the profile
does not calculate or refresh predictions.

## Errors

| Status | Meaning |
|---|---|
| 200 | Successful read, including legitimate empty histories |
| 400 | Invalid query parameters |
| 401 | Missing, invalid or expired authentication |
| 404 | Missing customer/order or inaccessible tenant/customer scope |
| 405 | Unsupported method, such as POST to the profile |
| 503 | Database or required storage unavailable |

Database exception details are not returned to clients. There are no write
endpoints, automatic membership updates, model loads or scoring calls.

## Frontend connection steps

1. Obtain store authentication through the current backend token mechanism
   or the future login integration.
2. Call `/api/customers/` for customer search and selection.
3. Pass the selected canonical UUID to `/api/customers/{id}/360/`.
4. Display actual purchases separately from predictions, with appropriate
   currencies, timestamps and unavailable/missing states.
5. Fetch history pages as the corresponding sections open. Use order UUIDs
   to open line-item detail pages.
6. Handle expired authentication, inaccessible customers and temporary
   storage failures distinctly; do not display failures as zero metrics.

Routes are available through the existing Django server. A frontend served
from another origin additionally needs the project's chosen CORS policy or
same-origin proxy; this package does not enable unrestricted CORS.

## Verification

From `backend/`:

```powershell
..\.venv\Scripts\python.exe -m pytest apps/analytics/tests/test_customer360.py -q
..\.venv\Scripts\python.exe -m pytest -q
```

New tests use the real selectors and HTTP views against isolated SQLite
canonical tables derived from `backend/schema.sql`, with a test-only
`split_part` implementation. Schema fixtures are cleaned up and partial
unmanaged-model targets restored for existing segmentation tests. They do
not connect to shared Supabase. PostgreSQL Decimal conversion is also tested
directly because SQLite numeric results have different Python types.

Coverage includes authentication, customer/order isolation, literal search,
source filtering, pagination and validation, personal revenue and returns,
source/currency separation, coupon basket limitations, saved prediction and
membership reads, empty profiles, decimal/time conversion, sanitized errors,
and assertions that profile/order reads issue only SELECT statements.

Verification on 16 November 2026:

- 24 Customer 360 tests passed.
- Full backend suite: 265 passed, 34 skipped, 3 subtests passed.
- Six existing preprocessing date-inference warnings remain.
- The 34 skipped tests are opt-in PostgreSQL KPI tests; their dedicated test
  database URL was not configured.
- Live Supabase: 39 successful HTTP checks across all six source populations,
  including list, combined profile, histories and available order details.
  Checks ran inside a PostgreSQL read-only transaction. No customer details,
  credentials or tokens were printed and no business records were changed.
- Django system checks reported no issues, and documentation links resolved.

## Extending the implementation

Keep reads in `selectors.py`, interpretation in `services.py`, validation in
`serializers.py`, and routing in views/URLs. Bind request values as parameters.
Scope every customer read by authenticated store and customer UUID; paginate
collections with deterministic ordering and preserve unknown values.

New segment modules appear through their saved prediction/membership rows;
Customer 360 does not need to dispatch their models. Document new result
units and display rules. Upload saving belongs to ingestion, cleaning to
preprocessing, and model preparation/scoring to segments. Store KPI formulas
stay in the KPI package. Campaign execution remains a separate responsibility.

## Related files

- [Project architecture](../../Overview.md)
- [Canonical schema notes](../../notes/schema-notes/final-schema.md)
- [Canonical SQL](../../../backend/schema.sql)
- [Analytics README](../../../backend/apps/analytics/README.md)
- [KPI contracts](../../notes/kpi-notes/kpi-schema-mapping.md)
- [Segmentation implementation](../segmentation/segmentation-backend-implementation.md)
