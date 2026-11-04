# Shared transformation contract (v2)

The three notebooks implement one logical Customerly demo store with six distinct source populations. They reuse completed files under `data_prep/raw/<dataset-slug>/`; KaggleHub runs only when a required file is absent. No download-in-progress detection is introduced. Raw sources are never edited.

## Identity and ownership

`common.DEMO_STORE_ID` and `DEMO_STORE_NAME` are shared. Identical store records may be exported by several datasets: the loader must upsert that single store primary key rather than insert six stores. Every other identity is UUID5 of tenant, table and a dataset-qualified source identity. Dataset prefixes are `dnb`, `evb`, `ret`, `cpn`, `chr`, `msg`. Source identifiers are read as strings; leading zeros survive. Native source_store_id is VARCHAR(255) in orders and causal_data, separate from tenant ownership. Similar identifiers across populations are not evidence of a shared person or product.

Events and source transaction lines without business IDs use the original file/row position, before filtering. Event row counters restart per file and gzip/plain variants use the same logical filename. Repeated lines, redemptions and promotions are retained. Row-based identity is stable for an unchanged source version, not for a reordered or replaced file; manifests record SHA-256 checksums and KaggleHub version markers.

Messaging campaign IDs are not globally unique in the source: seven IDs occur as both bulk and transactional campaigns. The source campaign key is `(campaign_type, id)`; the message reference is `(message_type, campaign_id)`. This is an explicit source relationship, not fuzzy matching. Message identifiers remain subject to global uniqueness validation. Unresolved references block publication rather than receiving invented campaign attributes.

## Dates and source semantics

- Real source dates are retained. There is no 2024 alignment or per-file shift.
- Dunnhumby day numbers use `2016-01-01 + DAY - 1`. This is a synthetic display calendar, not evidence of actual 2016 activity. Native DAY and WEEK_NO survive.
- Source-local timestamps remain timezone-naive in `orders`, `order_items.source_timestamp`, marketing campaigns and messages. Behavior explicitly carries UTC and retains it. Platform creation/update metadata uses UTC and is not source activity.
- `order_items.source_timestamp` retains individual line timestamps. A basket/invoice header receives a timestamp only if its source lines agree; otherwise that header field is NULL. Customer, date and source-store/region conflicts cannot be resolved by choosing the first line.
- Churn is a snapshot with no verified observation date or tenure unit. Its source features are retained; first_purchase_date stays NULL. It cannot acquire artificial purchase history.
- Historical feature evaluation must declare a source observation date. The transformation itself computes no recency, customer features, predictions or ML training data.

## Financial and schema decisions

The revised `backend/schema.sql` is the executable target contract. Use a fresh demo database when loading new UUIDs; applying these files does not migrate or modify an existing database.

- Ingested amounts use `NUMERIC` without forced two-decimal rounding. Quantities remain integer and fractional quantities fail validation rather than truncating.
- `order_items.unit_price` is nullable. Dunnhumby/coupon per-unit prices are derived from source line amount divided by quantity when possible; zero quantity does not imply zero price. The original line amount is authoritative. Derived division can be repeating; do not reconstruct original amounts by multiplying its finite decimal representation.
- `sales_value` is the canonical column name. Dunnhumby preserves SALES_VALUE; Retail derives quantity × native Price using decimal arithmetic; coupon preserves selling_price verbatim. No generic second discount subtraction occurs.
- Coupon coupon_discount maps to coupon_disc with its original sign. It is not assigned Dunnhumby's manufacturer-reimbursement semantics. Coupon gross/net interpretation remains unverified.
- Source currencies/units are not proven comparable. Store-wide monetary totals across populations are unsupported until that evidence exists. Do not convert, normalize or rescale amounts during transformation.
- Causal promotion rows use occurrence UUIDs; the old unique product/source-store/week constraint is removed. Use EXISTS or a distinct eligibility-key projection downstream, never a join that multiplies transaction lines. The raw display/mailer codes remain intact; their KPI predicate needs verified semantics.
- Retail Country maps to region. Catalog price and contact information stay NULL where absent. Product names absent from the source use a documented identifier/description label, not fabricated product information.
- Coupon source lines lack basket IDs. Each line gets a synthetic order container with matching customer and product foreign keys. True basket counts, basket size and AOV are unavailable for that population.

## Preservation, exceptions and publication

### Behavior-data sampling exception

Member B's notebook and `run_member_b.py` select whole customer sessions before
preprocessing `2019-Oct.csv` and `2019-Nov.csv` (including gzip equivalents).
The fixed fraction is 0.40 and seed is 42. A hash of customer + session selects
the same sessions across chunks and months, targeting approximately 40% of rows
per file across the full period; it is not an exact daily or monthly row quota.
Raw files remain unchanged. No intermediate sample files are written.
`raw_counts` records all scanned rows; `sampled_counts` records selected input
rows before cleaning. Publication reconciles events against selected input rows;
intentional sampling exclusions are recorded in notes, not cleaning failures.
Original physical row offsets remain the event identity basis. Other datasets
are processed without this sampling. Customer histories may be partial because
sampling preserves sessions, not every session belonging to a retained customer.

Final output layout:

```text
data_prep/processed/<dataset-slug>/<canonical-table>/<canonical-table>.csv
data_prep/processed/<dataset-slug>/manifest.json
data_prep/processed/<dataset-slug>/source_preservation/<subset>.csv
```

The six slugs are `dunnhumby-the-complete-journey`, `ecommerce-behavior-data-from-multi-category-store`, `online-retail-ii-uci`, `predicting-coupon-redemption`, `e-commerce-customer-churn`, and `direct-messaging`.

Noncanonical source-preservation files have their own folder and must not be imported as canonical tables. They retain coupon-product links, Dunnhumby campaign exposure/redemption days, and coupon transaction provenance. They do not claim the canonical database is lossless for every raw demographic or technical attribute; the immutable raw files remain the complete source archive.

Publication uses a hidden staging directory beside the dataset folder. Validation covers SQL columns, required values, UUIDs, integer bounds, numeric parsing, booleans, dates, string lengths, primary/unique keys, foreign keys and tenant ownership. Event, message and causal-promotion CSVs are streamed to staging and validated in chunks with Python partition-file global uniqueness (compact 16-byte UUID keys; exact keys checked one partition at a time, without a database), regardless of file size. Source-line counts are reconciled. Source exclusions block publication and are reported with row identity and reason when the mapper can identify them; parsing/header conflicts fail before publication and leave raw evidence untouched.

Failed staging directories retain manifests and exception CSVs for review. Previous final exports are untouched by validation failure. Successful publication swaps the dataset directory; its former contents remain in a hidden `.<dataset>-previous-...` backup. Swaps are per dataset, not a six-dataset transaction. The loader must read only the six named final directories and verify each manifest.

All emitted creation/update columns receive explicit platform metadata; blank SQL DEFAULT columns are not relied upon to apply defaults. Store metadata is identical across exports. Other processing timestamps can differ between runs; source values, source relationships and UUIDs must be stable.

## Coverage and verification

ML training uses raw datasets separately. These exports test ingestion, mapping, supported platform features and dashboards. They do not produce scores, labels for newly predicted segments, product-level feature aggregates or cross-population attribution.

Unsupported data includes inferred churn purchase histories, purchase revenue attributable to messaging customers, comparable cross-currency revenue, and customer/product replenishment feature storage in the existing customer-only feature table. Missing evidence means unavailable, not zero.

Run regression checks with:

```text
python -m unittest discover -s data_prep/transform -p "test_*.py"
```

Tests use temporary synthetic inputs, never published sample CSVs. Real-source checks are separate from full notebook runs. A PostgreSQL import must still be verified in a configured database; no local PostgreSQL executable was available during implementation, so manifests explicitly record `database_import_verified: false`.

Holiday identity is `(date, holiday)`, with UNIQUE(date, holiday), rather than UNIQUE(date). The raw calendar contains both Students day and Cyber Monday Sale on 2021-01-25; both names and the original date are retained. Downstream holiday filters must use EXISTS to avoid multiplying facts.


## Member B cleaning and replacement policy

Member B permits invalid source rows to be excluded only with a complete retained
exception report. Expected outputs equal selected behavior rows (or full Retail
rows) minus distinct rejected rows. Reason counts can overlap and are not used
as the subtraction. All canonical type, relationship and uniqueness checks still
apply. The manifest records accepted and audited exclusion counts. This overrides
the earlier blanket source-exclusion gate for Member B only.

Member B uses 250,000-row chunks and session hashing without categorical
factorization. After validated publication, its previous dataset backup is removed;
failed publication preserves the previous final dataset. Other members retain
the existing strict exclusion and backup policies. No raw files are replaced.
