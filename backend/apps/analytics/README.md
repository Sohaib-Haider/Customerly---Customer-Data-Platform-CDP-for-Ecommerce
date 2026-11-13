# apps/analytics: KPI cards

Django app for the store dashboard analytics. It currently holds the 19 FYP1
KPI cards (`kpis/`). The Customer 360 view will live alongside them.

The card list and the FYP1/FYP2 split are defined in
`docs/notes/kpi-notes/kpi-schema-mapping.md` ("FYP KPI scope").

## Run

From `backend/`, with `DATABASE_URL` in the environment or in `backend/.env`:

```bash
python -m apps.analytics.kpis                   # all 19 cards
python -m apps.analytics.kpis --card 3 9        # by id or key
python -m apps.analytics.kpis --category Revenue
python -m apps.analytics.kpis --card 1 --by month   # breakdown filter
python -m apps.analytics.kpis --json            # dashboard/API payload
python -m apps.analytics.kpis --sql 3           # print a card's SQL (paste into Supabase SQL Editor)
```

Inside Django (once `apps.analytics` is in `INSTALLED_APPS`):
`python manage.py compute_kpis --card 1 --by month`.

Breakdowns (`--by`): card 1 Total Revenue supports `month`, `week`, `region`,
`brand`, `department`, `category`; card 12 Total Orders supports `month`,
`week`, `region`.

## The 19 cards

| # | Card | Sources |
|---|------|---------|
| 1 | Total Revenue | ret, dnb, cpn |
| 2 | Revenue by Month | ret, dnb, cpn |
| 3 | Average Order Value | ret, dnb |
| 4 | Historical CLV (Actual) | ret |
| 5 | Top 20% Customers' Revenue Share | ret |
| 6 | Total Customers | all six |
| 7 | New Customers by Month | ret, dnb, cpn |
| 8 | Repeat Purchase Rate | ret, dnb |
| 9 | Customer Churn Rate | chr |
| 10 | Churn Rate by Preferred Category | chr |
| 11 | Avg Satisfaction & Complaint Rate | chr |
| 12 | Total Orders | ret, dnb |
| 13 | Avg Items per Order | ret, dnb |
| 14 | Top 10 Products by Revenue | ret, dnb |
| 15 | View > Cart > Purchase Funnel | evb |
| 16 | Open Rate (Email / Push) | msg |
| 17 | Click-Through Rate | msg |
| 18 | Campaign Conversion Rate | msg |
| 19 | Coupon Redemption Rate | cpn |

Sources: `ret` Online Retail II (GBP), `dnb` Dunnhumby (USD), `cpn` Predicting
Coupon Redemption, `evb` behavior events, `msg` Direct Messaging, `chr`
customer churn. They are disjoint populations in different currencies and time
windows.

## Rules the cards follow

From the feasibility rules in `kpi-schema-mapping.md`:

- Money is reported **per source**, never summed across sources.
- A source that cannot support a card is reported as **unavailable with a
  reason**, never as zero.
- Coupon-dataset orders are single transaction lines, so they are excluded
  from basket metrics (orders, average order value, repeat rate, items per order).
- Online Retail II cancellations are netted into revenue but are not counted
  as orders.
- Dunnhumby fuel and miscellaneous transactions (`KIOSK-GAS`,
  `MISC SALES TRAN`) store meter-style quantities; they count as revenue but
  are excluded from unit counts and product rankings.
- Dunnhumby only records redeemed coupons, so its redemption rate is
  unavailable (no offered count).

## API

Routed under `/api/` (see `config/urls.py`). Uses the same store-scoped
bearer token as the segments API; the store always comes from the token.

| Method | Path | Returns |
|--------|------|---------|
| GET | `/api/kpis/` | The 19 cards: id, key, name, category, kind, unit, sources, breakdowns |
| GET | `/api/kpis/<id or key>/` | One computed card for the token's store |
| GET | `/api/kpis/<id or key>/?by=month` | The card broken down by a supported dimension |

Responses: `404` unknown card, `400` unsupported breakdown, `503` database
unavailable.

## Output

`run_card` / `--json` returns, per card: `id`, `key`, `name`, `category`,
`kind` (`value`, `series`, `table`), `unit`, `results` (per source),
`unavailable` (source: reason), `sources` (name, currency) and `note`.

## Tests

```bash
python -m pytest apps/analytics/tests                     # definition checks
KPI_TEST_DATABASE_URL=postgresql://... python -m pytest apps/analytics/tests   # + run every card
```
