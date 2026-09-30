-- =============================================================================
-- CDP — Canonical Database Schema (PostgreSQL)
-- =============================================================================
-- Generated from : docs/notes/schema-notes/final-schema.md
-- Generated on   : 2026-09-30
-- Tables         : 23
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
--     (final-schema.md Open Decision #6), so no constraint is guessed.
--   - No triggers, no functions, no scheduled jobs are declared. The
--     customer_features refresh mechanism is an OPEN DECISION (#13) and the
--     `customer_features` refresh requirement from final-schema-gaps.md item 6
--     is explicitly NOT implemented here.
--
-- CHANGES FROM THE PREVIOUS GENERATION (2026-09-28, 19 tables)
--   Added   : segments.required_fields
--             table ml_models
--             table kpi_definitions
--             table workflows
--             table workflow_executions
--             message_sends.workflow_execution_id (+ FK, + index)
--   Removed : nothing — the previously generated `roles` table was already
--             absent, and no `segment_definitions` table is or was created.
--   19 -> 23 tables.
--
-- OPEN DECISIONS AFFECTING THIS FILE (see final-schema.md "Open Decisions")
--   MUST resolve before this schema is considered production-ready:
--     #3  orders.source_store_id / causal_data.source_store_id name (emitted as
--         `source_store_id`, matching the column list in final-schema.md; the
--         source docs name it `store_id`, which collides with the tenant FK)
--     #6  channel value sets (no constraint emitted — undecided)
--     #7  kpi_snapshots uniqueness under nullable store_id (emitted exactly as
--         documented: one plain UNIQUE constraint. Neither of the two candidate
--         fixes is implemented, because choosing between them would resolve an
--         open decision. See the note on that table.)
--     #8  orders.source_order_id nullability (emitted as documented: NOT NULL)
--     #12 duplicate customer_features columns (emitted as documented: all 6
--         retained, because both names in each pair are KPI-referenced)
--     #14 customer_features grain (emitted as documented: one row per customer)
--     #13 customer_features refresh mechanism (NOT implemented — undecided)
--     #15 tracked_links table (NOT created — undecided; out of scope here)
--     #16 table name (kpi_snapshots used, per final-schema.md)
--     #21 ml_models primary key (emitted as documented: (segment_id, version))
--     #22 ml_models "one active model per segment" (NOT enforced — undecided)
--     #24 workflows.id surrogate key (emitted — required by the FK from
--         workflow_executions)
--     #25 workflows.campaign_id nullability (emitted as documented: NULL)
--     #26 workflows.campaign_id ON DELETE (emitted as documented: SET NULL)
--     #27 workflow_executions.id surrogate key (emitted — required by the FK
--         from message_sends)
--     #28 workflow_executions.segment_id nullability (emitted as documented:
--         NULL)
--     #29 workflow_executions has no store_id (emitted as documented: none)
--
-- DEFERRED / NOT SCHEMA-BLOCKING (no DDL impact)
--     #1, #2, #4, #5, #9, #10, #11, #30, #31
--
-- IMPLEMENTATION CONVENTIONS APPLIED (not specified in final-schema.md)
--   These are pre-existing conventions of this file, retained unchanged. They
--   add no column, no constraint and no business rule.
--   1. Surrogate `id UUID` primary keys marked [P] use
--      DEFAULT gen_random_uuid(). Requires PostgreSQL 13+.
--   2. Record-creation timestamps marked [P] (created_at, updated_at,
--      assigned_at, computed_at, uploaded_at, started_at) use DEFAULT now().
--      Non-creation timestamps (sent_at, event_timestamp, order_timestamp,
--      purchased_at, completed_at, trained_at) are left without a default.
--   3. Other DEFAULTs are added ONLY where final-schema.md documents one:
--      order_items discount columns DEFAULT 0, message_sends boolean columns
--      DEFAULT false, ml_models.is_active DEFAULT false.
--
-- TABLE CREATION ORDER
--   Dependency-safe: every referenced table is created before the table that
--   references it. This does NOT follow the section numbers in
--   final-schema.md, because `workflow_executions` (section 19) must be
--   created BEFORE `message_sends` (section 15) — message_sends now carries a
--   FK to it. The final-schema.md section number is noted on each table.
--
--     stores                                        (§1)
--     store_users                                   (§2)
--     ingestion_uploads                             (§3)
--     ingestion_column_mappings                     (§4)
--     customers, products                           (§5, §6)
--     orders, order_items, events                   (§7, §8, §9)
--     customer_features                             (§10)
--     segments, ml_models, customer_segments        (§11, §12, §13)
--     marketing_campaigns                           (§14)
--     workflows, workflow_executions                (§18, §19)
--     message_sends                                 (§15)  <- needs §19
--     coupon_campaigns, coupon_redemptions          (§16, §17)
--     holidays, causal_data                         (§20, §21)
--     kpi_definitions, kpi_snapshots                (§22, §23)
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Group 1: Platform Core
-- -----------------------------------------------------------------------------

-- §1 stores — Tenant store accounts. Every other tenant-owned table references
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

-- §2 store_users — Team members belonging to a store. Auth and role-based access.
--    NOTE: `role` is a fixed platform-level vocabulary (owner / admin / member)
--    defined by kpi-schema-mapping.md card 5. There is no `roles` table and no
--    permissions table: role permissions are application logic, not data.
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

-- §3 ingestion_uploads — Tracks every file upload for audit and traceability.
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

-- §4 ingestion_column_mappings — LLM-powered mapping of source columns to
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

-- §5 customers — Normalized customer records. Central entity for segmentation,
--    CLV and customer KPIs.
--    NOTE: `churn` is a LABEL column (prediction target for the Churn-Risk
--    segment) and must never be supplied as a model input feature. See
--    final-schema.md Open Decision #1.
CREATE TABLE customers (
    id                          UUID          NOT NULL DEFAULT gen_random_uuid(),
    store_id                    UUID          NOT NULL,
    source_customer_id          VARCHAR(255)  NOT NULL,
    first_purchase_date         DATE,
    tenure                      INTEGER,
    warehouse_to_home           NUMERIC(10,2),
    number_of_devices_registered INTEGER,
    satisfaction_score          SMALLINT,
    complain                    BOOLEAN,
    days_since_last_order       INTEGER,
    cashback_amount             NUMERIC(12,2),
    preferred_order_category    VARCHAR(255),
    churn                       BOOLEAN,
    created_at                  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ   NOT NULL DEFAULT now(),
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

-- §6 products — Normalized product catalog. Referenced by order_items, events
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

-- §7 orders — Order header records. Groups order_items and links to customers.
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

-- §8 order_items — Line items per order. All financial values and discounts
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

-- §9 events — Behavioural events (view, cart, purchase, remove_from_cart).
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

-- §10 customer_features — Computed ML features, one row per customer.
--     Populated by scheduled platform jobs. Feeds all 9 ML segments.
--     KNOWN ISSUE (Decision Required #12): three column pairs are duplicates
--     under two names — (cart_removal_count / remove_from_cart_count),
--     (cart_removal_rate / remove_from_cart_rate) and
--     (unique_products_bought / product_loyalty). All 6 are retained because
--     both names in each pair are referenced by kpi-schema-mapping.md.
--     KNOWN ISSUE (Decision Required #14): days_since_last_purchase,
--     purchase_frequency and avg_days_between_purchases are documented at
--     customer x product grain but stored here at customer grain.
--     NO REFRESH TRIGGER IS DEFINED. The requirement to recompute on new rows in
--     orders / order_items / message_sends / events (final-schema-gaps.md item 6)
--     is recorded, but the mechanism — trigger vs scheduled job vs queue — is
--     Open Decision #13 and is deliberately not implemented.
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

-- §11 segments — Segment definitions. Platform-level, not store-specific.
--     `required_fields` is the config-driven gate: a segment only becomes
--     available to a store if their ingested data satisfies this schema.
--     There is no `segment_definitions` table — this one holds name,
--     segment_type, required_fields and description.
CREATE TABLE segments (
    id              UUID         NOT NULL DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,
    description     TEXT,
    segment_type    VARCHAR(50)  NOT NULL,
    required_fields JSONB,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_segments PRIMARY KEY (id),
    CONSTRAINT uq_segments_name UNIQUE (name)
);

-- §12 ml_models — Version history for trained ML models: which version is live
--     for each segment. Added per final-schema-gaps.md item 1.
--     The gap file specifies no primary key; (segment_id, version) is applied as
--     the minimal non-inventing completion (Decision Required #21).
--     "At most one active model per segment" is NOT enforced — not requested by
--     the gap file and left open (Decision Required #22).
CREATE TABLE ml_models (
    segment_id UUID         NOT NULL,
    version    VARCHAR(50)  NOT NULL,
    model_path VARCHAR(500) NOT NULL,
    trained_at TIMESTAMPTZ  NOT NULL,
    metrics    JSONB,
    is_active  BOOLEAN      NOT NULL DEFAULT false,
    CONSTRAINT pk_ml_models PRIMARY KEY (segment_id, version),
    CONSTRAINT fk_ml_models_segment
        FOREIGN KEY (segment_id) REFERENCES segments (id) ON DELETE CASCADE
);

CREATE INDEX idx_ml_models_segment_id ON ml_models (segment_id);
CREATE INDEX idx_ml_models_is_active  ON ml_models (is_active);
CREATE INDEX idx_ml_models_trained_at  ON ml_models (trained_at);

-- §13 customer_segments — Customer-to-segment assignments with ML scores.
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
-- Campaigns and workflow builder
-- -----------------------------------------------------------------------------

-- §14 marketing_campaigns — Marketing campaign definitions. Referenced by
--     message_sends for attribution, and by workflows.campaign_id.
CREATE TABLE marketing_campaigns (
    id                 UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id           UUID         NOT NULL,
    source_campaign_id VARCHAR(255) NOT NULL,
    campaign_type      VARCHAR(100) NOT NULL,
    channel            VARCHAR(50)  NOT NULL,
    topic              VARCHAR(255),
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    total_count        INTEGER,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_marketing_campaigns PRIMARY KEY (id),
    CONSTRAINT fk_marketing_campaigns_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT uq_marketing_campaigns_store_source
        UNIQUE (store_id, source_campaign_id)
);

CREATE INDEX idx_marketing_campaigns_store_id      ON marketing_campaigns (store_id);
CREATE INDEX idx_marketing_campaigns_channel       ON marketing_campaigns (channel);
CREATE INDEX idx_marketing_campaigns_campaign_type ON marketing_campaigns (campaign_type);

-- §18 workflows — Drag-and-drop workflow definitions from the campaign builder.
--     Added per final-schema-gaps.md item 3, following the n8n pattern: the
--     whole definition (nodes + connections) is JSONB in one row. There is no
--     workflow_nodes table.
--     `id` is a surrogate key, the only column here not in the gap file; it is
--     required because workflow_executions.workflow_id is a UUID FK to this
--     table (Decision Required #24).
--     `campaign_id` is nullable (a draft workflow may have no campaign yet —
--     Decision Required #25) and uses ON DELETE SET NULL (#26).
CREATE TABLE workflows (
    id          UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id    UUID         NOT NULL,
    name        VARCHAR(255) NOT NULL,
    status      VARCHAR(50)  NOT NULL,
    nodes       JSONB,
    connections JSONB,
    campaign_id UUID,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_workflows PRIMARY KEY (id),
    CONSTRAINT fk_workflows_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_workflows_campaign
        FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns (id) ON DELETE SET NULL
);

CREATE INDEX idx_workflows_store_id    ON workflows (store_id);
CREATE INDEX idx_workflows_campaign_id ON workflows (campaign_id);
CREATE INDEX idx_workflows_status      ON workflows (status);

-- §19 workflow_executions — Which customer is at which step of a running
--     workflow. Added per final-schema-gaps.md item 4.
--     MUST be created before message_sends, which references it.
--     `id` is a surrogate key, the only column here not in the gap file,
--     required because message_sends.workflow_execution_id is a UUID FK to this
--     table (Decision Required #27). A composite (workflow_id, customer_id)
--     key was rejected because it would forbid one customer re-entering the
--     same workflow twice.
--     `segment_id` is nullable (Decision Required #28) and uses ON DELETE SET
--     NULL, so the execution survives deletion of its triggering assignment.
--     No store_id: the gap file does not list one (Decision Required #29).
CREATE TABLE workflow_executions (
    id                 UUID         NOT NULL DEFAULT gen_random_uuid(),
    workflow_id        UUID         NOT NULL,
    customer_id        UUID         NOT NULL,
    segment_id         UUID,
    current_node_index INTEGER,
    status             VARCHAR(50)  NOT NULL,
    started_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ,
    CONSTRAINT pk_workflow_executions PRIMARY KEY (id),
    CONSTRAINT fk_workflow_executions_workflow
        FOREIGN KEY (workflow_id) REFERENCES workflows (id) ON DELETE CASCADE,
    CONSTRAINT fk_workflow_executions_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE,
    CONSTRAINT fk_workflow_executions_segment
        FOREIGN KEY (segment_id) REFERENCES customer_segments (id) ON DELETE SET NULL
);

CREATE INDEX idx_workflow_executions_workflow_id ON workflow_executions (workflow_id);
CREATE INDEX idx_workflow_executions_customer_id ON workflow_executions (customer_id);
CREATE INDEX idx_workflow_executions_segment_id  ON workflow_executions (segment_id);
CREATE INDEX idx_workflow_executions_status      ON workflow_executions (status);

-- §15 message_sends — Individual message delivery and engagement tracking.
--     `channel_norm` normalises web_push / mobile_push to push (Segment 5).
--     No value-set constraint is emitted: the documented normalisation table
--     has no row for `whatsapp` (Decision Required #6).
--     `workflow_execution_id` is the workflow-attribution FK added per
--     final-schema-gaps.md item 5. It is NULLABLE — historically ingested
--     messages have no workflow execution — and uses ON DELETE SET NULL so the
--     message and its engagement data survive (Decision Required #23).
CREATE TABLE message_sends (
    id                    UUID         NOT NULL DEFAULT gen_random_uuid(),
    store_id              UUID         NOT NULL,
    source_message_id     VARCHAR(255) NOT NULL,
    campaign_id           UUID         NOT NULL,
    customer_id           UUID         NOT NULL,
    message_type          VARCHAR(50)  NOT NULL,
    channel               VARCHAR(50)  NOT NULL,
    channel_norm          VARCHAR(50)  NOT NULL,
    sent_at               TIMESTAMPTZ  NOT NULL,
    is_opened             BOOLEAN      NOT NULL DEFAULT false,
    is_clicked            BOOLEAN      NOT NULL DEFAULT false,
    is_unsubscribed       BOOLEAN      NOT NULL DEFAULT false,
    is_hard_bounced       BOOLEAN      NOT NULL DEFAULT false,
    is_soft_bounced       BOOLEAN      NOT NULL DEFAULT false,
    is_complained         BOOLEAN      NOT NULL DEFAULT false,
    is_blocked            BOOLEAN      NOT NULL DEFAULT false,
    is_purchased          BOOLEAN      NOT NULL DEFAULT false,
    purchased_at          TIMESTAMPTZ,
    workflow_execution_id UUID,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_message_sends PRIMARY KEY (id),
    CONSTRAINT fk_message_sends_store
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_sends_campaign
        FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_sends_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_sends_workflow_execution
        FOREIGN KEY (workflow_execution_id) REFERENCES workflow_executions (id) ON DELETE SET NULL,
    CONSTRAINT uq_message_sends_store_source
        UNIQUE (store_id, source_message_id)
);

CREATE INDEX idx_message_sends_store_id              ON message_sends (store_id);
CREATE INDEX idx_message_sends_campaign_id           ON message_sends (campaign_id);
CREATE INDEX idx_message_sends_customer_id           ON message_sends (customer_id);
CREATE INDEX idx_message_sends_channel               ON message_sends (channel);
CREATE INDEX idx_message_sends_channel_norm          ON message_sends (channel_norm);
CREATE INDEX idx_message_sends_sent_at               ON message_sends (sent_at);
CREATE INDEX idx_message_sends_is_opened             ON message_sends (is_opened);
CREATE INDEX idx_message_sends_is_clicked            ON message_sends (is_clicked);
CREATE INDEX idx_message_sends_is_purchased          ON message_sends (is_purchased);
CREATE INDEX idx_message_sends_workflow_execution_id ON message_sends (workflow_execution_id);

-- §16 coupon_campaigns — Coupon campaign definitions.
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

-- §17 coupon_redemptions — Individual coupon redemption records.
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

-- §20 holidays — Shared holiday calendar. Not tenant-specific, so no store_id.
CREATE TABLE holidays (
    id      UUID         NOT NULL DEFAULT gen_random_uuid(),
    date    DATE         NOT NULL,
    holiday VARCHAR(255) NOT NULL,
    CONSTRAINT pk_holidays PRIMARY KEY (id),
    CONSTRAINT uq_holidays_date UNIQUE (date)
);

CREATE INDEX idx_holidays_date ON holidays (date);

-- §21 causal_data — Product x source-store x week promotion flags (Dunnhumby).
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

CREATE INDEX idx_causal_data_store_id        ON causal_data (store_id);
CREATE INDEX idx_causal_data_product_id      ON causal_data (product_id);
CREATE INDEX idx_causal_data_source_store_id ON causal_data (source_store_id);
CREATE INDEX idx_causal_data_week_no         ON causal_data (week_no);

-- -----------------------------------------------------------------------------
-- Group 6: Analytics
-- -----------------------------------------------------------------------------

-- §22 kpi_definitions — Metadata for all 115 KPI cards, so KPIs are data in
--     the database rather than only a documentation file. Added per
--     final-schema-gaps.md item 2. Platform-level reference table: no store_id.
--     The gap file specifies no primary key and no column referencing this
--     table, so `name` is used as the key — no surrogate id was invented, and
--     kpi_snapshots.kpi_name is deliberately NOT wired to it as a foreign key
--     because the gap file does not ask for that relationship.
--     `formula` is NULL because kpi-schema-mapping.md does not yet define
--     formulas. This table defines no new KPI cards; the total remains 115.
CREATE TABLE kpi_definitions (
    name             VARCHAR(100) NOT NULL,
    category         VARCHAR(50)  NOT NULL,
    formula          TEXT,
    refresh_schedule VARCHAR(20),
    description      TEXT,
    CONSTRAINT pk_kpi_definitions PRIMARY KEY (name)
);

CREATE INDEX idx_kpi_definitions_category ON kpi_definitions (category);

-- §23 kpi_snapshots — Pre-computed KPI values for fast dashboard loading.
--     Populated by scheduled jobs.
--
--     store_id is NULLABLE: the 5 Platform Growth KPI cards are platform-level
--     and have no store; every other card is always store-scoped.
--
--     UNIQUENESS (Open Decision #7 — UNRESOLVED, emitted as documented).
--     final-schema.md declares one UNIQUE constraint over
--       (store_id, kpi_name, period_type, period_start, dimension, dimension_value)
--     and records in Open Decision #7 that a plain UNIQUE is insufficient,
--     because PostgreSQL treats NULLs as distinct, so platform-level rows
--     (store_id IS NULL) are not deduplicated. final-schema.md names two
--     candidate fixes — a partial unique index, or NULLS NOT DISTINCT
--     (PostgreSQL 15+) — and leaves the choice open.
--
--     NEITHER FIX IS IMPLEMENTED HERE. Emitting either one would resolve Open
--     Decision #7 by judgement, which this file does not do. The constraint
--     below is exactly what final-schema.md documents, and the limitation is
--     real: duplicate rows are possible for platform-level snapshots. Resolve
--     Decision #7 before this schema goes to production.
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
        FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE,
    CONSTRAINT uq_kpi_snapshots_scope
        UNIQUE (store_id, kpi_name, period_type, period_start, dimension, dimension_value)
);

CREATE INDEX idx_kpi_snapshots_store_id     ON kpi_snapshots (store_id);
CREATE INDEX idx_kpi_snapshots_kpi_name     ON kpi_snapshots (kpi_name);
CREATE INDEX idx_kpi_snapshots_kpi_category ON kpi_snapshots (kpi_category);
CREATE INDEX idx_kpi_snapshots_computed_at  ON kpi_snapshots (computed_at);

COMMIT;

-- =============================================================================
-- End of schema
-- =============================================================================