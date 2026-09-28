-- =============================================================================
-- CDP — Canonical Database Schema (PostgreSQL)
-- =============================================================================
-- Generated from : docs/notes/schema-notes/final-schema.md
-- Generated on   : 2026-09-28
-- Tables         : 19
--
-- SOURCE OF TRUTH
--   This file is a mechanical translation of final-schema.md. Column names,
--   data types, nullability, primary keys, foreign keys (incl. ON DELETE
--   behaviour), unique constraints and index names are reproduced exactly as
--   documented there. Nothing is invented.
--
-- SCOPE / EXCLUSIONS
--   - kpi-schema-mapping.md is treated as a fixed requirements contract, so no
--     column it references has been renamed, retyped or dropped.
--   - docs/decisions/data-transformation-plan.md is EXCLUDED (marked "Do not
--     consider this file's context anywhere for now"). Nothing here derives
--     from it.
--   - No CHECK constraints, ENUM types or domain value sets are declared. The
--     documented value vocabularies (stores.plan, store_users.role,
--     event_type, channel, channel_norm, segment_type, period_type) are
--     conflicting or incomplete in the source documentation
--     (final-schema.md Open Decisions #6), so no constraint is guessed.
--   - No triggers are declared. updated_at columns are set by the application.
--
-- OPEN DECISIONS AFFECTING THIS FILE (see final-schema.md "Open Decisions")
--   MUST resolve before this schema is considered production-ready:
--     #3  orders.source_store_id / causal_data.source_store_id name (applied as
--         `source_store_id` per the documented source_* convention, but the
--         source docs name it `store_id`; this collides with the tenant FK)
--     #6  channel value sets (no constraint emitted — undecided)
--     #7  kpi_snapshots uniqueness (implemented as two partial unique indexes,
--         the mechanism named in final-schema.md; see note on that table)
--     #8  orders.source_order_id nullability (emitted as documented: NOT NULL)
--     #12 duplicate customer_features columns (emitted as documented: all 6
--         retained, because both names in each pair are KPI-referenced)
--     #14 customer_features grain (emitted as documented: one row per customer)
--     #15 tracked_links table (NOT created — undecided; out of scope here)
--     #16 table name (kpi_snapshots used, per final-schema.md)
--
-- DEFERRED / NOT SCHEMA-BLOCKING (no DDL impact)
--     #1, #2, #4, #5, #9, #10, #11, #13
--
-- IMPLEMENTATION CONVENTIONS APPLIED (not specified in final-schema.md)
--   1. UUID columns marked [P] (platform-generated) use
--      DEFAULT gen_random_uuid(). Requires PostgreSQL 13+.
--   2. Timestamp columns marked [P] for record creation use DEFAULT now().
--   3. Defaults are added ONLY where final-schema.md documents one
--      (order_items discount columns DEFAULT 0, message_sends booleans
--      DEFAULT false).
--
-- TABLE CREATION ORDER
--   Dependency-safe: referenced tables are always created first.
--     stores, segments, holidays              (no FKs)
--     store_users, customers, products        (FK -> stores)
--     ingestion_uploads                       (FK -> stores, store_users)
--     ingestion_column_mappings               (FK -> ingestion_uploads)
--     orders                                  (FK -> stores, customers)
--     order_items                             (FK -> orders, products)
--     events                                  (FK -> stores, customers, products)
--     customer_features                       (FK -> customers, stores)
--     customer_segments                       (FK -> stores, customers, segments)
--     marketing_campaigns                     (FK -> stores)
--     message_sends                           (FK -> stores, marketing_campaigns, customers)
--     coupon_campaigns                        (FK -> stores)
--     coupon_redemptions                      (FK -> stores, coupon_campaigns, customers)
--     causal_data                             (FK -> stores, products)
--     kpi_snapshots                           (FK -> stores)
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Group 1: Platform Core
-- -----------------------------------------------------------------------------

-- 1. stores — Tenant store accounts. Every other tenant-owned table references
--    this table for isolation.
CREATE TABLE stores (
    id         UUID         NOT NULL DEFAULT gen_random_uuid(),
    name       VARCHAR(255) NOT NULL,
    plan       VARCHAR(50)  NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_stores PRIMARY KEY (id)
);

CREATE INDEX idx_stores_plan      ON stores (plan);
CREATE INDEX idx_stores_created_at ON stores (created_at);

-- 2. store_users — Team members belonging to a store. Auth and role-based access.
CREATE TABLE store_users (
    id         UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id   UUID         NOT NULL,
    email      VARCHAR(255) NOT NULL,
    role       VARCHAR(50)  NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_store_users PRIMARY KEY (id),
    CONSTRAINT fk_store_users_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT uq_store_users_store_email UNIQUE (store_id, email)
);

CREATE INDEX idx_store_users_store_id ON store_users (store_id);
CREATE INDEX idx_store_users_role     ON store_users (role);

-- -----------------------------------------------------------------------------
-- Group 2: Data Ingestion
-- -----------------------------------------------------------------------------

-- 3. ingestion_uploads — Tracks every file upload for audit and traceability.
CREATE TABLE ingestion_uploads (
    id            UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id      UUID         NOT NULL,
    uploaded_by   UUID         NOT NULL,
    file_name     VARCHAR(500) NOT NULL,
    file_type     VARCHAR(50)  NOT NULL,
    row_count     INTEGER,
    status        VARCHAR(50)  NOT NULL,
    error_message TEXT,
    uploaded_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_ingestion_uploads PRIMARY KEY (id),
    CONSTRAINT fk_ingestion_uploads_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_ingestion_uploads_user
        FOREIGN KEY (uploaded_by) REFERENCES store_users (id) ON DELETE RESTRICT
);

CREATE INDEX idx_ingestion_uploads_store_id ON ingestion_uploads (store_id);
CREATE INDEX idx_ingestion_uploads_status   ON ingestion_uploads (status);

-- 4. ingestion_column_mappings — LLM-powered mapping of source columns to
--    canonical schema columns, per upload.
CREATE TABLE ingestion_column_mappings (
    id            UUID         NOT NULL DEFAULT gen_random_uuid(),
    upload_id     UUID         NOT NULL,
    source_column VARCHAR(255) NOT NULL,
    target_table  VARCHAR(100) NOT NULL,
    target_column VARCHAR(100) NOT NULL,
    confidence    NUMERIC(3,2),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_ingestion_column_mappings PRIMARY KEY (id),
    CONSTRAINT fk_ingestion_column_mappings_upload
        FOREIGN KEY (upload_id) REFERENCES ingestion_uploads (id) ON DELETE CASCADE,
    CONSTRAINT uq_ingestion_column_mappings_upload_source
        UNIQUE (upload_id, source_column)
);

CREATE INDEX idx_ingestion_column_mappings_upload_id
    ON ingestion_column_mappings (upload_id);

-- -----------------------------------------------------------------------------
-- Group 3: Canonical Customer Data
-- -----------------------------------------------------------------------------

-- 5. customers — Normalized customer records. Central entity for segmentation,
--    CLV and customer KPIs.
--    NOTE: `churn` is a LABEL column (prediction target for the Churn-Risk
--    segment) and must never be supplied as a model input feature. See
--    final-schema.md Open Decisions #1.
CREATE TABLE customers (
    id                        UUID          NOT NULL DEFAULT gen_random_uuid(),
    store_id                  UUID          NOT NULL,
    source_customer_id        VARCHAR(255)  NOT NULL,
    first_purchase_date       DATE,
    tenure                    INTEGER,
    warehouse_to_home         NUMERIC(10,2),
    number_of_devices_registered INTEGER,
    satisfaction_score        SMALLINT,
    complain                  BOOLEAN,
    days_since_last_order     INTEGER,
    cashback_amount           NUMERIC(12,2),
    preferred_order_category  VARCHAR(255),
    churn                     BOOLEAN,
    created_at                TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_customers PRIMARY KEY (id),
    CONSTRAINT fk_customers_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT uq_customers_store_source
        UNIQUE (store_id, source_customer_id)
);

CREATE INDEX idx_customers_store_id              ON customers (store_id);
CREATE INDEX idx_customers_first_purchase_date   ON customers (first_purchase_date);
CREATE INDEX idx_customers_days_since_last_order ON customers (days_since_last_order);
CREATE INDEX idx_customers_satisfaction_score    ON customers (satisfaction_score);
CREATE INDEX idx_customers_complain              ON customers (complain);

-- 6. products — Normalized product catalog. Referenced by order_items, events
--    and causal_data.
CREATE TABLE products (
    id               UUID          NOT NULL DEFAULT gen_random_uuid(),
    store_id         UUID          NOT NULL,
    source_product_id VARCHAR(255) NOT NULL,
    name             VARCHAR(500)  NOT NULL,
    brand            VARCHAR(255),
    category         VARCHAR(255),
    department       VARCHAR(255),
    commodity_desc   VARCHAR(255),
    unit_price       NUMERIC(12,2),
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_products PRIMARY KEY (id),
    CONSTRAINT fk_products_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT uq_products_store_source
        UNIQUE (store_id, source_product_id)
);

CREATE INDEX idx_products_store_id   ON products (store_id);
CREATE INDEX idx_products_brand      ON products (brand);
CREATE INDEX idx_products_category   ON products (category);
CREATE INDEX idx_products_department ON products (department);

-- 7. orders — Order header records. Groups order_items and links to customers.
--    NOTE: `source_store_id` is the source STORE_ID, distinct from the tenant
--    `store_id` above. Name is Decision Required #3.
--    NOTE: `region` has no documented source column (Decision Required #4) and
--    `source_order_id` nullability is Decision Required #8.
CREATE TABLE orders (
    id               UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id         UUID         NOT NULL,
    source_order_id  VARCHAR(255) NOT NULL,
    customer_id      UUID         NOT NULL,
    order_date       DATE         NOT NULL,
    order_timestamp  TIMESTAMPTZ,
    week_no          SMALLINT,
    day_no           SMALLINT,
    source_store_id  INT,
    region           VARCHAR(255),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_orders PRIMARY KEY (id),
    CONSTRAINT fk_orders_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE RESTRICT,
    CONSTRAINT uq_orders_store_source
        UNIQUE (store_id, source_order_id)
);

CREATE INDEX idx_orders_store_id        ON orders (store_id);
CREATE INDEX idx_orders_customer_id     ON orders (customer_id);
CREATE INDEX idx_orders_order_date      ON orders (order_date);
CREATE INDEX idx_orders_order_timestamp ON orders (order_timestamp);
CREATE INDEX idx_orders_week_no         ON orders (week_no);
CREATE INDEX idx_orders_region          ON orders (region);

-- 8. order_items — Line items per order. All financial values and discounts
--    live here (schema-blueprint principle 6).
CREATE TABLE order_items (
    id                UUID         NOT NULL DEFAULT gen_random_uuid(),
    order_id          UUID         NOT NULL,
    product_id        UUID         NOT NULL,
    quantity          INTEGER      NOT NULL,
    unit_price        NUMERIC(12,2) NOT NULL,
    sales_value       NUMERIC(12,2) NOT NULL,
    retail_disc       NUMERIC(12,2) NOT NULL DEFAULT 0,
    coupon_disc       NUMERIC(12,2) NOT NULL DEFAULT 0,
    coupon_match_disc NUMERIC(12,2) NOT NULL DEFAULT 0,
    other_discount    NUMERIC(12,2) NOT NULL DEFAULT 0,
    CONSTRAINT pk_order_items PRIMARY KEY (id),
    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE,
    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT
);

CREATE INDEX idx_order_items_order_id   ON order_items (order_id);
CREATE INDEX idx_order_items_product_id ON order_items (product_id);

-- 9. events — Behavioural events (view, cart, purchase, remove_from_cart).
--    Raw material for the Purchase Intent and Cart Abandoners segments.
CREATE TABLE events (
    id              UUID          NOT NULL DEFAULT gen_random_uuid(),
    store_id        UUID          NOT NULL,
    customer_id     UUID          NOT NULL,
    session_id      VARCHAR(255)  NOT NULL,
    event_type      VARCHAR(50)   NOT NULL,
    event_timestamp TIMESTAMPTZ   NOT NULL,
    product_id      UUID,
    category_id     VARCHAR(255),
    category_code   VARCHAR(255),
    brand           VARCHAR(255),
    price           NUMERIC(12,2),
    CONSTRAINT pk_events PRIMARY KEY (id),
    CONSTRAINT fk_events_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_events_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE,
    CONSTRAINT fk_events_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE SET NULL
);

CREATE INDEX idx_events_store_id        ON events (store_id);
CREATE INDEX idx_events_customer_id     ON events (customer_id);
CREATE INDEX idx_events_event_type      ON events (event_type);
CREATE INDEX idx_events_event_timestamp ON events (event_timestamp);
CREATE INDEX idx_events_session_id      ON events (session_id);
CREATE INDEX idx_events_product_id      ON events (product_id);

-- -----------------------------------------------------------------------------
-- Group 4: Features & ML
-- -----------------------------------------------------------------------------

-- 10. customer_features — Computed ML features, one row per customer.
--     Populated by scheduled platform jobs. Feeds all 9 ML segments.
--     KNOWN ISSUE (Decision Required #12): three column pairs are duplicates
--     under two names — (cart_removal_count / remove_from_cart_count),
--     (cart_removal_rate / remove_from_cart_rate) and
--     (unique_products_bought / product_loyalty). All 6 are retained because
--     both names in each pair are referenced by kpi-schema-mapping.md.
--     KNOWN ISSUE (Decision Required #14): days_since_last_purchase,
--     purchase_frequency and avg_days_between_purchases are documented at
--     customer x product grain but stored here at customer grain.
CREATE TABLE customer_features (
    customer_id                    UUID         NOT NULL,
    store_id                       UUID         NOT NULL,

    -- RFM / CLV (Segment 2)
    recency                        INTEGER,
    frequency                      INTEGER,
    monetary                       NUMERIC(12,2),
    avg_order_value                NUMERIC(12,2),
    customer_tenure                INTEGER,
    total_items_bought             INTEGER,
    unique_products_bought         INTEGER,
    max_single_order_value         NUMERIC(12,2),
    min_single_order_value         NUMERIC(12,2),
    purchase_regularity            NUMERIC(10,2),

    -- Behavioural funnel (Segments 1 & 9)
    view_count                     INTEGER,
    cart_count                     INTEGER,
    purchase_count                 INTEGER,
    view_to_cart_rate              NUMERIC(5,4),
    cart_to_purchase_rate          NUMERIC(5,4),
    last_view_days                 INTEGER,
    session_count                  INTEGER,
    avg_viewed_price               NUMERIC(12,2),
    cart_removal_count             INTEGER,
    cart_removal_rate              NUMERIC(5,4),
    cart_abandonment_rate          NUMERIC(5,4),
    last_cart_days                 INTEGER,
    avg_cart_value                 NUMERIC(12,2),
    remove_from_cart_count         INTEGER,
    remove_from_cart_rate          NUMERIC(5,4),

    -- Discount behaviour (Segment 3)
    total_discount_received        NUMERIC(12,2),
    discount_dependency_ratio      NUMERIC(5,4),
    coupon_redemption_rate         NUMERIC(5,4),
    avg_discount_per_order         NUMERIC(12,2),
    discount_order_frequency       NUMERIC(5,4),

    -- Channel engagement (Segment 5)
    email_open_rate                NUMERIC(5,4),
    email_click_rate               NUMERIC(5,4),
    push_open_rate                 NUMERIC(5,4),
    sms_open_rate                  NUMERIC(5,4),
    unsubscribe_rate               NUMERIC(5,4),
    bounce_rate                    NUMERIC(5,4),
    purchase_rate                  NUMERIC(5,4),

    -- Replenishment (Segment 6)
    days_since_last_purchase       INTEGER,
    purchase_frequency             NUMERIC(10,2),
    avg_days_between_purchases     NUMERIC(10,2),
    product_loyalty                INTEGER,

    -- Cross-sell (Segment 7)
    basket_size                    NUMERIC(10,2),
    category_diversity             INTEGER,
    cross_category_purchase        NUMERIC(5,4),
    primary_dept                   VARCHAR(255),
    complementary_affinity         INTEGER,
    brand_concentration            NUMERIC(5,4),

    -- Seasonal (Segment 8)
    purchase_by_week               JSONB,
    seasonal_concentration         NUMERIC(10,2),
    seasonal_category              VARCHAR(255),
    seasonal_purchase_ratio        NUMERIC(5,4),
    promo_response_rate            NUMERIC(5,4),

    computed_at                    TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_customer_features PRIMARY KEY (customer_id),
    CONSTRAINT fk_customer_features_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE,
    CONSTRAINT fk_customer_features_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE
);

CREATE INDEX idx_customer_features_store_id    ON customer_features (store_id);
CREATE INDEX idx_customer_features_computed_at ON customer_features (computed_at);

-- -----------------------------------------------------------------------------
-- Group 5: Segmentation
-- -----------------------------------------------------------------------------

-- 11. segments — Segment definitions. Platform-level, not store-specific.
CREATE TABLE segments (
    id           UUID         NOT NULL DEFAULT gen_random_uuid(),
    name         VARCHAR(100) NOT NULL,
    description  TEXT,
    segment_type VARCHAR(50)  NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_segments PRIMARY KEY (id),
    CONSTRAINT uq_segments_name UNIQUE (name)
);

-- 12. customer_segments — Customer-to-segment assignments with ML scores.
--     store_id is present because this is a tenant-owned table queried directly
--     by the Segment Size / Distribution / Multi-Segment KPI cards.
CREATE TABLE customer_segments (
    id          UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id    UUID         NOT NULL,
    customer_id UUID         NOT NULL,
    segment_id  UUID         NOT NULL,
    score       NUMERIC(5,4),
    assigned_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_customer_segments PRIMARY KEY (id),
    CONSTRAINT fk_customer_segments_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_customer_segments_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE,
    CONSTRAINT fk_customer_segments_segment
        FOREIGN KEY (segment_id) REFERENCES segments (id) ON DELETE CASCADE,
    CONSTRAINT uq_customer_segments_store_customer_segment
        UNIQUE (store_id, customer_id, segment_id)
);

CREATE INDEX idx_customer_segments_store_id    ON customer_segments (store_id);
CREATE INDEX idx_customer_segments_customer_id ON customer_segments (customer_id);
CREATE INDEX idx_customer_segments_segment_id  ON customer_segments (segment_id);
CREATE INDEX idx_customer_segments_score       ON customer_segments (score);

-- -----------------------------------------------------------------------------
-- Group 7: Campaign Management
-- -----------------------------------------------------------------------------

-- 13. marketing_campaigns — Marketing campaign definitions.
CREATE TABLE marketing_campaigns (
    id                  UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id            UUID         NOT NULL,
    source_campaign_id  VARCHAR(255) NOT NULL,
    campaign_type       VARCHAR(100) NOT NULL,
    channel             VARCHAR(50)  NOT NULL,
    topic               VARCHAR(255),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    total_count         INTEGER,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_marketing_campaigns PRIMARY KEY (id),
    CONSTRAINT fk_marketing_campaigns_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT uq_marketing_campaigns_store_source
        UNIQUE (store_id, source_campaign_id)
);

CREATE INDEX idx_marketing_campaigns_store_id       ON marketing_campaigns (store_id);
CREATE INDEX idx_marketing_campaigns_channel        ON marketing_campaigns (channel);
CREATE INDEX idx_marketing_campaigns_campaign_type  ON marketing_campaigns (campaign_type);

-- 14. message_sends — Individual message delivery and engagement tracking.
--     `channel_norm` normalises web_push / mobile_push to push (Segment 5).
--     No value-set constraint is emitted: the documented normalisation table
--     has no row for `whatsapp` (Decision Required #6).
CREATE TABLE message_sends (
    id               UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id         UUID         NOT NULL,
    source_message_id VARCHAR(255) NOT NULL,
    campaign_id      UUID         NOT NULL,
    customer_id      UUID         NOT NULL,
    message_type     VARCHAR(50)  NOT NULL,
    channel          VARCHAR(50)  NOT NULL,
    channel_norm     VARCHAR(50)  NOT NULL,
    sent_at          TIMESTAMPTZ  NOT NULL,
    is_opened        BOOLEAN      NOT NULL DEFAULT false,
    is_clicked       BOOLEAN      NOT NULL DEFAULT false,
    is_unsubscribed  BOOLEAN      NOT NULL DEFAULT false,
    is_hard_bounced  BOOLEAN      NOT NULL DEFAULT false,
    is_soft_bounced  BOOLEAN      NOT NULL DEFAULT false,
    is_complained    BOOLEAN      NOT NULL DEFAULT false,
    is_blocked       BOOLEAN      NOT NULL DEFAULT false,
    is_purchased     BOOLEAN      NOT NULL DEFAULT false,
    purchased_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_message_sends PRIMARY KEY (id),
    CONSTRAINT fk_message_sends_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_sends_campaign
        FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_sends_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE,
    CONSTRAINT uq_message_sends_store_source
        UNIQUE (store_id, source_message_id)
);

CREATE INDEX idx_message_sends_store_id     ON message_sends (store_id);
CREATE INDEX idx_message_sends_campaign_id  ON message_sends (campaign_id);
CREATE INDEX idx_message_sends_customer_id  ON message_sends (customer_id);
CREATE INDEX idx_message_sends_channel      ON message_sends (channel);
CREATE INDEX idx_message_sends_channel_norm ON message_sends (channel_norm);
CREATE INDEX idx_message_sends_sent_at      ON message_sends (sent_at);
CREATE INDEX idx_message_sends_is_opened    ON message_sends (is_opened);
CREATE INDEX idx_message_sends_is_clicked   ON message_sends (is_clicked);
CREATE INDEX idx_message_sends_is_purchased ON message_sends (is_purchased);

-- 15. coupon_campaigns — Coupon campaign definitions.
CREATE TABLE coupon_campaigns (
    id                 UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id           UUID         NOT NULL,
    source_campaign_id VARCHAR(255) NOT NULL,
    campaign_type      VARCHAR(100) NOT NULL,
    start_date         DATE         NOT NULL,
    end_date           DATE         NOT NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_coupon_campaigns PRIMARY KEY (id),
    CONSTRAINT fk_coupon_campaigns_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT uq_coupon_campaigns_store_source
        UNIQUE (store_id, source_campaign_id)
);

CREATE INDEX idx_coupon_campaigns_store_id      ON coupon_campaigns (store_id);
CREATE INDEX idx_coupon_campaigns_campaign_type ON coupon_campaigns (campaign_type);

-- 16. coupon_redemptions — Individual coupon redemption records.
CREATE TABLE coupon_redemptions (
    id                UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id          UUID         NOT NULL,
    coupon_id         VARCHAR(255) NOT NULL,
    campaign_id       UUID         NOT NULL,
    customer_id       UUID         NOT NULL,
    redemption_status BOOLEAN      NOT NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_coupon_redemptions PRIMARY KEY (id),
    CONSTRAINT fk_coupon_redemptions_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_coupon_redemptions_campaign
        FOREIGN KEY (campaign_id) REFERENCES coupon_campaigns (id) ON DELETE CASCADE,
    CONSTRAINT fk_coupon_redemptions_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE
);

CREATE INDEX idx_coupon_redemptions_store_id          ON coupon_redemptions (store_id);
CREATE INDEX idx_coupon_redemptions_campaign_id       ON coupon_redemptions (campaign_id);
CREATE INDEX idx_coupon_redemptions_customer_id       ON coupon_redemptions (customer_id);
CREATE INDEX idx_coupon_redemptions_redemption_status ON coupon_redemptions (redemption_status);
CREATE INDEX idx_coupon_redemptions_coupon_id         ON coupon_redemptions (coupon_id);

-- -----------------------------------------------------------------------------
-- Reference tables
-- -----------------------------------------------------------------------------

-- 17. holidays — Shared holiday calendar. Not tenant-specific, so no store_id.
CREATE TABLE holidays (
    id      UUID         NOT NULL DEFAULT gen_random_uuid(),
    date    DATE         NOT NULL,
    holiday VARCHAR(255) NOT NULL,
    CONSTRAINT pk_holidays PRIMARY KEY (id),
    CONSTRAINT uq_holidays_date UNIQUE (date)
);

CREATE INDEX idx_holidays_date ON holidays (date);

-- 18. causal_data — Product x source-store x week promotion flags (Dunnhumby).
--     Required to serve the "Seasonal Purchase Propensity" KPI card via
--     customer_features.promo_response_rate.
CREATE TABLE causal_data (
    id              UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id        UUID         NOT NULL,
    product_id      UUID         NOT NULL,
    source_store_id INT          NOT NULL,
    week_no         SMALLINT     NOT NULL,
    display         VARCHAR(50),
    mailer          VARCHAR(50),
    CONSTRAINT pk_causal_data PRIMARY KEY (id),
    CONSTRAINT fk_causal_data_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_causal_data_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE,
    CONSTRAINT uq_causal_data_store_source_store_product_week
        UNIQUE (store_id, source_store_id, product_id, week_no)
);

CREATE INDEX idx_causal_data_store_id         ON causal_data (store_id);
CREATE INDEX idx_causal_data_product_id       ON causal_data (product_id);
CREATE INDEX idx_causal_data_source_store_id  ON causal_data (source_store_id);
CREATE INDEX idx_causal_data_week_no          ON causal_data (week_no);

-- -----------------------------------------------------------------------------
-- Group 6: Analytics
-- -----------------------------------------------------------------------------

-- 19. kpi_snapshots — Pre-computed KPI values for fast dashboard loading.
--     Populated by scheduled jobs.
--
--     store_id is NULLABLE: the 5 Platform Growth KPI cards are platform-level
--     and have no store; every other card is always store-scoped.
--
--     UNIQUENESS (Open Decision #7). final-schema.md requires
--       (store_id, kpi_name, period_type, period_start, dimension, dimension_value)
--     to be unique, and states that a plain UNIQUE constraint is insufficient
--     because PostgreSQL treats NULLs as distinct. The mechanism named in the
--     document — a partial unique index for the store_id IS NULL case — is
--     therefore implemented below as two partial unique indexes.
--
--     RESIDUAL ISSUE (not fixed here, undecided): period_start, dimension and
--     dimension_value are themselves nullable, so the same NULL-distinctness
--     problem recurs inside both partial indexes for 'total' period_type rows
--     and for ungrouped KPI values. Resolving it requires either NOT NULL
--     columns with sentinel values, COALESCE-based expression indexes, or a
--     NULLS NOT DISTINCT declaration (PostgreSQL 15+). The documentation does
--     not determine which, so no further constraint is invented.
CREATE TABLE kpi_snapshots (
    id              UUID          NOT NULL DEFAULT gen_random_uuid(),
    store_id        UUID,
    kpi_name        VARCHAR(100)  NOT NULL,
    kpi_category    VARCHAR(50)   NOT NULL,
    period_type     VARCHAR(20)   NOT NULL,
    period_start    DATE,
    period_end      DATE,
    dimension       VARCHAR(100),
    dimension_value VARCHAR(255),
    value           NUMERIC(18,4) NOT NULL,
    computed_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_kpi_snapshots PRIMARY KEY (id),
    CONSTRAINT fk_kpi_snapshots_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE
);

-- Store-scoped snapshots: one value per KPI per period per dimension.
CREATE UNIQUE INDEX uq_kpi_snapshots_store_scope
    ON kpi_snapshots (store_id, kpi_name, period_type, period_start, dimension, dimension_value)
    WHERE store_id IS NOT NULL;

-- Platform-level snapshots (Platform Growth cards): one value per KPI per
-- period per dimension, with store_id implicitly NULL.
CREATE UNIQUE INDEX uq_kpi_snapshots_platform_scope
    ON kpi_snapshots (kpi_name, period_type, period_start, dimension, dimension_value)
    WHERE store_id IS NULL;

CREATE INDEX idx_kpi_snapshots_store_id     ON kpi_snapshots (store_id);
CREATE INDEX idx_kpi_snapshots_kpi_name     ON kpi_snapshots (kpi_name);
CREATE INDEX idx_kpi_snapshots_kpi_category ON kpi_snapshots (kpi_category);
CREATE INDEX idx_kpi_snapshots_computed_at  ON kpi_snapshots (computed_at);

COMMIT;

-- =============================================================================
-- End of schema
-- =============================================================================
