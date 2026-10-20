# Dunnhumby Transform — Member A (Segments 6, 7, 8)

**Status:** Draft — for review (real Kaggle data transformed and validated green; open
questions for the team listed in §7)
**Last Updated:** 2026-10-19

Turns the 8 `Dunnhumby: The Complete Journey` CSVs into canonical-schema CSVs that can
be bulk-loaded into `backend/schema.sql` — shaped data only, no feature computation.

**Not a source for this work:** `docs/decisions/data-transformation-plan.md` (excluded by
its own header). Nothing here is derived from it.

---

## 1. How to run

The real 8 CSVs live in `data_prep/raw/dunnhumby/` (gitignored — re-downloadable from
`frtgnn/dunnhumby-the-complete-journey`), then:

```bash
python data_prep/transform/run_dunnhumby.py --raw data_prep/raw/dunnhumby --out data_prep/processed/dunnhumby
```

Options: `--store-id <uuid>` stamps one tenant on every row (default: a deterministic
placeholder, see §4); `--skip-validate` forces output even when checks fail.

Dry run without real data (synthetic, schema-shaped fixtures):

```bash
python data_prep/transform/run_dunnhumby.py --raw data_prep/transform/fixtures/dunnhumby --out data_prep/processed/dunnhumby
```

Last **fixture** run: **VALIDATION PASS — 71 checks, 0 failures**.
Last **real-data** run: see §1.1.

### 1.1 Real-data results

Measured on the actual Kaggle files (not fixtures), so the counts below are the
deliverable's real shape:

| Source file | Raw rows | Canonical table | Emitted |
|---|---:|---|---:|
| `transaction_data.csv` | 2,595,732 | `order_items` | 2,595,732 (0 lost) |
| 2,595,732 lines → 276,484 baskets | | `orders` | 276,484 |
| `hh_demographic.csv` (801) + transacting households | | `customers` | 2,500 |
| `product.csv` | 92,353 | `products` | 92,353 |
| `causal_data.csv` | 36,786,524 | `causal_data` | 36,771,279 |
| `campaign_desc.csv` | 30 | `coupon_campaigns` | 30 |
| `coupon_redempt.csv` | 2,318 | `coupon_redemptions` | 2,288 |
| `campaign_table.csv` (7,208), `coupon.csv` (124,548) | | — | 0 (no target, §7) |

Validation: **PASS, 71 checks, 0 failures.** Every drop is attributed to its source file
in `audit.json → dropped_by_source`.

**Three defects the fixtures could not expose, all found and fixed on real data:**

1. **`TRANS_TIME` is zero-padded `HHMM`, and pandas was eating the leading zero.**
   Read as `int64`, `"0955"` became `955` → 3 digits → rejected → **25,188 orders
   silently lost their timestamp** (every basket in hours 00–09). Fixed by pinning
   `dtype=str` on that column and accepting the 3-digit form in `parse_trans_time`.
   **Re-verified after the fix: `NULL timestamps: 0`, all 24 hours present.**
2. **`TRANS_TIME` is 4-digit `HHMM` (`1631` = 16:31), not `HHMMSS`.** A 6-digit parse
   would have shifted every timestamp by hours. Fixed in `common.parse_trans_time`.
3. **`causal_data.display`/`mailer` are promo codes, not 0/1 flags** — see §7.

Output volume to be aware of: `causal_data.csv` is **4.5 GB / 36.8 M rows** (§7).

| File | Role |
|---|---|
| `common.py` | id namespace + calendar offset helpers (the single source of both) |
| `dunnhumby.py` | column mappings, cleaning, all 8 transforms |
| `validate.py` | NOT NULL / PK / UNIQUE / FK / calendar-invariant checks |
| `run_dunnhumby.py` | CLI entry point, writes CSVs + `audit.json` |
| `fixtures/dunnhumby/` | synthetic source CSVs (tracked; `raw/` and `processed/` are gitignored) |

**Output:** 8 CSVs + `audit.json` (row counts, every dropped row with a reason, every
unmapped column, validation result) in `data_prep/processed/dunnhumby/`.

---

## 2. What is emitted

| Canonical table | Source | Sample fixture rows |
|---|---|---|
| `stores` | platform (tenant row for this demo) | 1 |
| `customers` | `hh_demographic.csv` + `transaction_data.csv` household roster | 6 |
| `products` | `product.csv` | 9 |
| `orders` | `transaction_data.csv` (one row per `BASKET_ID`) | 7 |
| `order_items` | `transaction_data.csv` (one row per line) | 11 |
| `causal_data` | `causal_data.csv` | 8 |
| `coupon_campaigns` | `campaign_desc.csv` | 4 |
| `coupon_redemptions` | `coupon_redempt.csv` | 4 |

**Not emitted:** `events`, `marketing_campaigns`, `message_sends`, `holidays`,
`customer_features`, `segments`, `kpi_snapshots` — no source columns in this dataset,
or they are platform-computed at runtime. Deliberate absences are listed in §7.

Load order (FK-safe):

```text
stores -> customers -> products -> orders -> order_items
       -> coupon_campaigns -> coupon_redemptions -> causal_data
```

---

## 3. Per-file column mapping

Citations: `final-schema.md` (target shape), `segments-schema-mapping.md` (Segments 6/7/8),
`Replenishment-Ready.md` / `Cross-Sell Opportunity.md` / `Seasonal Purchase.md`.

### transaction_data.csv → `orders` + `order_items`

| Source | Target | Note / citation |
|---|---|---|
| `BASKET_ID` | `orders.source_order_id` | ESSENTIAL (mapping doc L320) |
| `household_key` | `orders.customer_id` → `customers.id` | FK resolved via customer namespace |
| `DAY` | `orders.day_no` | ESSENTIAL, native 1–711, kept verbatim for interval math (mapping L321) |
| `DAY` | `orders.order_date` | `anchor_date + (day_no - 1)` (mapping L322, L358) — see §5 |
| `WEEK_NO` | `orders.week_no` | native 1–104 (mapping L326) |
| `STORE_ID` | `orders.source_store_id` | mapping L327 says target `orders.store_id (INT)`; the real column is `source_store_id` per **Open Decision #3** (tenant FK `store_id` is UUID) |
| `TRANS_TIME` | `orders.order_timestamp` | real time-of-day for this source — `DAY` + `TRANS_TIME` is the documented Dunnhumby option in **Open Decision #5** (final-schema L1044). **Format is 4-digit `HHMM`** (`1631` = 16:31), verified on the real file — `common.parse_trans_time` handles HHMM/HMMSS/HHMMSS |
| — | `orders.region` | no source column → NULL (**Open Decision #4**) |
| `QUANTITY` | `order_items.quantity` | ESSENTIAL (mapping L324) |
| `SALES_VALUE` | `order_items.sales_value` | net amount as-is; `RETAIL_DISC`/`COUPON_MATCH_DISC` must **not** be subtracted again, and `COUPON_DISC` is reimbursed to the store (mapping pricing note L124) |
| `RETAIL_DISC` | `order_items.retail_disc` | carried even though Segments 6/7/8 list it as "not needed" (L352/L409/L484) — schema needs it and it preserves behaviour |
| `COUPON_DISC` | `order_items.coupon_disc` | same |
| `COUPON_MATCH_DISC` | `order_items.coupon_match_disc` | same |
| — | `order_items.other_discount` | 0.0, no source column |

Financials stay on `order_items`, never on `orders` (blueprint principle 6).

### product.csv → `products`

| Source | Target | Note / citation |
|---|---|---|
| `PRODUCT_ID` | `products.source_product_id` | ESSENTIAL (mapping L323) |
| `DEPARTMENT` | `products.department` | MODERATE/STRONG (L328, L382, L451) |
| `BRAND` | `products.brand` | MODERATE (L329, L383, L452) |
| `COMMODITY_DESC` | `products.commodity_desc` | MODERATE (L330, L384, L453) |
| `COMMODITY_DESC` (fallback `SUB_COMMODITY_DESC`, then `PRODUCT_ID`) | `products.name` | **derived** — `name` is `NOT NULL` `[S]` (final-schema L218) but this dataset has no name column (§6) |
| `MANUFACTURER`, `SUB_COMMODITY_DESC`, `CURR_SIZE_OF_PRODUCT` | — | documented "not needed" (L348–350, L406–408, L481–483) |
| — | `products.category`, `products.unit_price` | no source → NULL (**Open Decision #11**) |

### hh_demographic.csv → `customers` (roster only)

All 7 demographic attributes (`AGE_DESC`, `MARITAL_STATUS_CODE`, `INCOME_DESC`,
`HOMEOWNER_DESC`, `HH_COMP_DESC`, `HOUSEHOLD_SIZE_DESC`, `KID_CATEGORY_DESC`) have **no
canonical column** — Segments 6/7/8 all record "All demographics — Not included per user
request" (L345, L401, L478). The file is therefore used only to complete the customer
roster (households that appear in `hh_demographic` but never transact are kept, flagged
in `audit.json`). Its columns are dropped, not silently renamed.

### causal_data.csv → `causal_data`

| Source | Target |
|---|---|
| `PRODUCT_ID` | `causal_data.product_id` |
| `STORE_ID` | `causal_data.source_store_id` (NOT NULL here) |
| `WEEK_NO` | `causal_data.week_no` |
| `display` / `mailer` | `causal_data.display` / `mailer` (VARCHAR(50), **raw codes, not 0/1 flags** — see below) |

> **Real-data finding (2026-10-19):** the segment docs describe `display`/`mailer` as
> promotion *flags*, but the real file carries promotion **codes**: `display` ∈ `0`–`9`,
> `mailer` ∈ `A`,`D`,`F`,`H`,`J`,`L`… (plus `0`). `schema.sql` already types both as
> `VARCHAR(50)`, so raw values are stored verbatim — lossless and faithful. Consequence:
> the `promo_response_rate` formula (mapping L468, "purchases when `display/mailer=1`")
> would match only the code `1`, not "any promotion". **Team decision needed** on whether
> "promo exposed" means `!= '0'` / non-empty, or a specific code. This transform does not
> resolve it.

Segment 8 requires all five (mapping L454–455), and the join to `orders` is on
`(product_id, source_store_id, week_no)` with no re-mapping (mapping L472) — that is what
`promo_response_rate` (Seasonal Purchase Propensity card) consumes.

### campaign_desc.csv → `coupon_campaigns`

`CAMPAIGN` → `source_campaign_id`, `DESCRIPTION` → `campaign_type`, `START_DAY` /
`END_DAY` → `start_date` / `end_date` via the same anchor as `orders` (§5).

### coupon_redempt.csv → `coupon_redemptions`

`household_key` → `customer_id`, `COUPON_UPC` → `coupon_id`, `CAMPAIGN` → `campaign_id`.
`redemption_status` = `TRUE` (every row in this file *is* a redemption).
**`DAY` is dropped**: `coupon_redemptions` has no timestamp column (final-schema L656) and
no KPI requires one.

> **Scope note:** all three segment files list `coupon*.csv` / `campaign*.csv` as
> "Coupon data — not needed" (L351, L411, L485). They are mapped anyway because the Member A
> brief puts all 8 files in scope and these two tables exercise real platform surface
> (`coupon_campaigns`, `coupon_redemptions`). They serve the *platform demo*, not
> Segments 6/7/8. If the team disagrees, delete the two `_coupon_*` outputs — nothing else
> references them.

---

## 4. ID remapping / unified namespace

Implemented in `common.py` — one place, so every member's transform agrees.

- **Namespace prefix** on every source id: `dnb:` + raw value (`dnb:501`, `dnb:90001`).
  Namespace constant: `common.SOURCE_NAMESPACES` (`"dunnhumby" → "dnb"`). Prefixing is
  what keeps `source_customer_id` unique if two datasets land in the **same** demo store —
  `household_key 501` from Dunnhumby and a `customer_id 501` from another source would
  otherwise collide inside `UNIQUE (store_id, source_customer_id)`.
  Provenance is preserved, not destroyed: `source_*` columns still show exactly which raw
  value produced the row (blueprint principle 4).
- **UUIDs** are `uuid5(fixed_namespace, table + "|" + namespaced_key)` — deterministic, so
  re-running produces byte-identical ids and reloads are idempotent. Keys are derived from
  *(table, source key)* only, deliberately **not** from `store_id`, so changing the tenant
  UUID with `--store-id` does not invalidate any child id.
- `order_items` keys are `(BASKET_ID, PRODUCT_ID, occurrence)`; `causal_data` keys are
  `(STORE_ID, PRODUCT_ID, WEEK_NO)` (its natural key); `coupon_redemptions` keys are
  `(household, coupon, campaign)`.

## 5. Date alignment (per-dataset strategy)

Dunnhumby has **no real dates** — only native day numbers (`DAY` 1–711). So it invents
them from its own declared anchor, `common.anchor_for("dunnhumby")`:

```text
order_date   = anchor(2016-01-01) + (day_no - 1)   # segments-schema-mapping.md L322/L358
order_timestamp = order_date + TRANS_TIME          # Open Decision #5 — a real time, not synthesised
start/end_date  = anchor + (START_DAY/END_DAY - 1)  # campaigns, same anchor as orders
day_no / week_no = native values, kept verbatim for interval + seasonal maths
```

Order dates and campaign windows therefore share **one** calendar, so KPIs over
`order_date` and features over `day_no`/`week_no` stay consistent. `validate.py` asserts
`order_date == anchor + (day_no - 1)` on every row.

> `2016-01-01` is the documented default (mapping L358). The excluded
> `data-transformation-plan.md` proposes a different anchor — deliberately not used.

### Why the anchor is per-dataset, not global

The anchor used to be a single module-level `common.ANCHOR_DATE` constant. That was a
trap for Members B/C: a source with **real** timestamps (Online Retail II's
`InvoiceDate`, mapped straight to `order_date` at mapping L101) has no business being
shifted by Dunnhumby's anchor. A member reading `common.py`, told to "reuse it, don't
re-derive", could plausibly route real dates through `align_day` — and silently move them
by ~1460 days, or off the calendar entirely, with no error and no warning.

So the anchor is now declared **per dataset** in `common.DATASET_ANCHORS`, and:

- `anchor_for("dunnhumby")` returns it; `anchor_for(<unknown>)` **raises** rather than
  defaulting, because guessing an anchor would invent dates for data that has them.
- `align_day(day_no, anchor)` requires the anchor explicitly.
- `window_offset(real_dates, window_start)` + `shift_date(value, offset)` are the
  provided path for real-date sources (per `data-transformation-plan.md` L90,
  "`InvoiceDate` → shift to store window"), computed once from the source's own minimum
  and applied uniformly — never inferred per row
  (`2.7-constraints.md`: offsets must be documented and auditable).
- `validate.py` takes the dataset's anchor as a parameter; a source with real dates
  passes `None` and the anchor check is skipped (with a note) rather than asserting
  against a timeline it was never meant to sit on.

## 6. Derived values (flagged, not silent)

Per blueprint principle 3, computed values are platform-owned; these four are forced by
`NOT NULL` constraints or by the absence of a source column, and each is printed in the
run output:

| Column | Why | Status |
|---|---|---|
| `order_items.unit_price` | `NOT NULL`, no unit-price column in this source. Derived `sales_value / quantity` (0.0 when `quantity = 0`), so `quantity * unit_price` reconstructs `sales_value` exactly — consistent with the pricing note (mapping L124: "use `sales_value` directly for Dunnhumby"). | derived — team to confirm |
| `products.name` | `NOT NULL` `[S]`, no name column. Derived from `COMMODITY_DESC` (else `SUB_COMMODITY_DESC`, else the source id). | derived — team to confirm |
| `customers.*` (all except keys) | fed only by the churn dataset, which is held out (`docs/submission/2.7-constraints.md`) | NULL by design |
| `products.unit_price`, `products.category`, `orders.region` | no documented source column | NULL (**Decisions #11, #4**) |

**KPI impact, so nobody is surprised later:** `orders.region` NULL ⇒ *Revenue by Region* /
*Orders by Region* cards are empty for this tenant; `products.unit_price` NULL ⇒ *Average
Product Price* card empty. Both are surfaced as validation warnings.

## 7. Files/columns with no canonical target (gaps, not invented)

| Source | Why nothing is emitted |
|---|---|
| `coupon.csv` (`COUPON_UPC`, `PRODUCT_ID`, `CAMPAIGN`) | the canonical schema has **no coupon→product table**; no segment file maps these columns anywhere |
| `campaign_table.csv` (`DESCRIPTION`, `household_key`, `CAMPAIGN`) | campaign **exposure**, not redemption. `coupon_redemptions.redemption_status` would have to be set to `FALSE` for rows that were never redeemed — that fabricates meaning, so it is not done |
| `hh_demographic.csv` 7 demographic columns | excluded per user request in all three segment files (§3) |
| `events` table | this dataset has no view/cart/purchase event stream |

These are reported in the terminal output and in `audit.json → notes`. Nothing is
dropped quietly.

### 7.1 Open questions for the team (raised by the real data, not decided here)

**(a) `display`/`mailer` are promo *codes*, but Segment 8's formula assumes flags.**
Measured over all 36.8 M source rows:

| column | distinct values |
|---|---|
| `display` | `0,1,2,3,4,5,6,7,9,A` (10 — `0` is the commonest at 21.0 M) |
| `mailer` | `0,A,C,D,F,H,J,L,P,X,Z` (11 — `0` at 11.5 M, `A` at 17.1 M) |

`segments-schema-mapping.md` L468 defines `promo_response_rate` as "purchases when
**`display/mailer=1`**". As written that matches only `display='1'` (1.1 M rows) and
**`mailer='1'`, which does not exist at all** — so the mailer half of the predicate can
never be true. The natural reading is "exposed to *any* promotion" = `display != '0' OR
mailer != '0'`, but that is an interpretation, and `documented formula ≠ real data` is a
decision for the team, not for this script. Stored verbatim either way (schema is
`VARCHAR(50)`, so nothing is lost by deferring).

**(b) The schema's `causal_data` UNIQUE key forces real information loss.**
`UNIQUE (store_id, source_store_id, product_id, week_no)` matches the natural key, and
**15,245 source rows collide on it — all 15,245 with a *different* `display`/`mailer`**
than the row kept (e.g. `(361, 27658, 59)` has `('0','D')` and `('6','D')`). So a store×
product×week genuinely can carry two promo codes, and the constraint collapses them.
Options: widen the key to include both codes, or accept losing the second promo per
cell. Recorded, not resolved — this is exactly the kind of conflict
`AGENTS.md` says to log rather than silently pick a side on.

**(c) Volume check before anyone loads this.** `causal_data.csv` alone is **4.5 GB /
36.8 M rows** — ~17× `order_items`. Loading it costs real time and disk, and the join in
(a) runs over it. Coverage against orders: 10,038 of 13,939 distinct
`(source_store_id, week_no)` pairs in `orders` exist in `causal_data` (**72%**), so
`promo_response_rate` is computable for most — not all — store-weeks. Worth confirming
the platform wants the full table rather than, say, only weeks that orders touch.

**(d) `customers.first_purchase_date` stays NULL** even though this source *could* supply
it (`MIN(order_date)` per household). Deriving it is aggregation, which the brief rules
out. Consequence: the *New Customers* KPI renders empty for this tenant until the
platform computes it.

## 8. Cleaning rules (behaviour preserved)

Rows are only removed when they could not be loaded or would break a constraint; nothing
is rescaled, rebalanced, or aggregated:

- empty/unparseable `BASKET_ID`, `PRODUCT_ID`, `household_key`, `DAY`
- line items whose product is absent from `product.csv` (FK `ON DELETE RESTRICT`)
- duplicate `PRODUCT_ID` (first kept), duplicate `(store, product, week)` causal rows
  (the schema's unique constraint), duplicate redemptions
- campaigns missing usable `START_DAY`/`END_DAY` (both `NOT NULL`)
- redemptions referencing a missing campaign or household

Every drop is counted **per reason** in `audit.json → dropped`; negative quantities /
`SALES_VALUE` (returns) are *kept* — they are real behaviour. `RETAIL_DISC` is negative in
the source (a discount reduces value) and is stored verbatim, not sign-flipped.

**Real-data quirks handled by the loader** (none of which the fixtures exercised):

- `TRANS_TIME` is 4-digit `HHMM`, not `HHMMSS` — a naive 6-digit parse silently shifted
  every timestamp; fixed in `common.parse_trans_time` after inspecting the real file.
- `causal_data.csv` is ~700 MB / millions of rows, so its dtypes are pinned at read time
  (`PRODUCT_ID` as string) in `load_raw` to keep it from arriving as float noise.
- IDs arrive as floats when pandas upcasts a column containing blanks — `common.source_id`
  normalises `27160.0` → `dnb:27160` so keys stay stable.

## 9. Validation

`validate.py` re-checks the constraints `backend/schema.sql` actually declares:

- column list + order matches every emitted table
- `NOT NULL` on all required columns
- `UNIQUE (store_id, source_customer_id)`, `UNIQUE (store_id, source_order_id)`,
  `UNIQUE (store_id, source_product_id)`, `UNIQUE (store_id, source_store_id, product_id, week_no)`
- 12 foreign keys (incl. `orders.customer_id`, `order_items.*`, both coupon FKs)
- calendar invariants: anchor alignment, `week_no` ∈ 1–104, `day_no` ∈ 1–711
- source row accounting (raw lines → emitted + recorded drops)

Not validated: value vocabularies (`stores.plan`, `event_type`, `channel`, …) —
`schema.sql` declares no CHECK constraints for them because the sources conflict
(**Open Decision #6**). `stores.plan` is written as `pro` (one of the three values
listed in `final-schema.md` table 1) and can be changed at load time.

## 10. Open Decisions this transform touches

| # | Decision | What this transform does today |
|---|---|---|
| #3 | `store_id` collision | `STORE_ID` → `orders.source_store_id` / `causal_data.source_store_id`, tenant stays `store_id UUID` |
| #4 | `orders.region` source | left NULL (no geo column here) |
| #5 | `orders.order_timestamp` source | populated from `DAY` + `TRANS_TIME` — a real time component for this source; NULL when `TRANS_TIME` is missing |
| #6 | channel/plan value sets | `stores.plan = 'pro'` provisionally |
| #11 | `products.unit_price` source | left NULL (no catalog price; price exists only per transaction line) |

Plus three issues that only surfaced on real data, recorded in **§7.1** rather than
closed: the `display/mailer` code-vs-flag mismatch with the `promo_response_rate`
formula, the 15,245 genuine `causal_data` key collisions the schema's UNIQUE forces, and
the 4.5 GB `causal_data` volume question.

None of these are resolved here — they are recorded decisions, not mine to close.

---

## Changelog

| Date | Change |
|---|---|
| 2026-10-19 | Initial draft: `common.py`, `dunnhumby.py`, `validate.py`, `run_dunnhumby.py`, synthetic fixtures, mapping/gap documentation. Validated green (71 checks) on fixtures; real Kaggle data still to be dropped into `data_prep/raw/dunnhumby/`. |
| 2026-10-19 | Real dataset received (8 CSVs, `data_prep/raw/dunnhumby/`). Two fixture-invisible bugs/assumptions corrected from inspection of the real files: `TRANS_TIME` is 4-digit `HHMM` (parser fixed), and `causal_data.display`/`mailer` are promotion codes (`0-9` / `A,D,F,H,J,L…`), not 0/1 flags — stored verbatim, formula impact recorded as an open team question. |
| 2026-10-19 | Real run completed green (§1.1). **Fixed a defect the run exposed:** pandas read `TRANS_TIME` as int64, dropping the zero pad (`"0955"` → `955`) so all 25,188 baskets in hours 00–09 got `order_timestamp = NULL`; pinned `dtype=str` and taught `parse_trans_time` the 3-digit form. **Fixed row accounting:** drops are now tagged with their source file instead of guessed from the reason string (coupon drops had been miscounted as transaction drops) and a raw−dropped=emitted cross-check was added. **Added §7.1** with three real-data questions for the team: `display/mailer` codes vs the documented `=1` predicate, 15,245 `causal_data` UNIQUE collisions that discard a differing promo code, and 4.5 GB output volume. |
| 2026-10-21 | Re-verified end to end after the fixes: **0 NULL `order_timestamp`** across 276,484 orders with all 24 hours present; **0 transaction line drops** (2,595,732 → 2,595,732); drops attributed by source (`causal_data.csv: 15,245`, `coupon_redempt.csv: 30`); validation 71 checks, 0 failures. An earlier background attempt silently produced no output (0-byte log, stale files) and was re-run in the foreground to get the verified result. Temporary audit scripts removed. |
| 2026-10-21 | **Made the date strategy per-dataset** (§5) so Members B/C cannot inherit Dunnhumby's synthetic anchor. Removed the module-level `common.ANCHOR_DATE`; added `common.DATASET_ANCHORS` + `anchor_for()` (raises for an unregistered dataset instead of defaulting), made `align_day()` require the anchor explicitly, and added `window_offset()`/`shift_date()` as the path for real-date sources. `validate.py` now takes the dataset's anchor as a parameter and skips the anchor check (with a note) for real-date sources. Re-verified on fixtures (71 checks, 0 failures) and on the real dataset (identical output: 402 s, same counts). |
