# Segments → Schema Mapping

Purpose: For each of the 9 segments, document what it is, whether it needs ML, and exactly which attributes (and target tables/columns) are required.

---

## Segment 1: Predicted Purchase Intent

**What it is:** Flags customers most likely to buy soon based on what they've been browsing. Looks at their viewed products and categories to predict their next move.

**Type:** ML (prediction problem — will this customer buy soon?)

**Dataset:** eCommerce behavior data from multi category store (2019-Oct.csv, 2019-Nov.csv)
**Kaggle URL:** https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store

**Dataset Files & Attributes:**

| File | Attributes |
|---|---|
| 2019-Oct.csv | event_time, event_type, product_id, category_id, category_code, brand, price, user_id, user_session |
| 2019-Nov.csv | event_time, event_type, product_id, category_id, category_code, brand, price, user_id, user_session |

**Attributes Needed:**

| Attribute | Why | Target Table | Target Column |
|---|---|---|---|
| event_type | Core signal: view / cart / purchase | events | event_type |
| product_id | What they're looking at | events | product_id |
| category_id | Category-level interest | events | category_id |
| category_code | Category grouping | events | category_code |
| brand | Brand preference | events | brand |
| price | Price range they browse | events | price |
| user_id | Which customer | events | customer_id |
| user_session | Groups events into sessions | events | session_id |
| event_time | Recency of browsing | events | event_timestamp |

**All 9 attributes needed. No removals.**

### Computed Features (derived → stored in `customer_features`)

| Computed Feature | Formula | Attributes Used | How Computed |
|---|---|---|---|
| view_count | `COUNT(*) WHERE event_type='view'` | events.event_type | SQL GROUP BY customer_id, filter event_type |
| cart_count | `COUNT(*) WHERE event_type='cart'` | events.event_type | SQL GROUP BY customer_id, filter event_type |
| purchase_count | `COUNT(*) WHERE event_type='purchase'` | events.event_type | SQL GROUP BY customer_id, filter event_type |
| view_to_cart_rate | `cart_count / view_count` | derived from cart_count, view_count | Division of two computed features |
| cart_to_purchase_rate | `purchase_count / cart_count` | derived from purchase_count, cart_count | Division of two computed features |
| last_view_days | `CURRENT_DATE - MAX(event_timestamp) WHERE event_type='view'` | events.event_timestamp, events.event_type | SQL date diff, filter event_type |
| session_count | `COUNT(DISTINCT session_id)` | events.session_id | SQL GROUP BY customer_id |
| avg_session_value | `AVG(price) WHERE event_type='view'` | events.price, events.event_type | SQL AVG, filter event_type |

**8 computed features for Predicted Purchase Intent. All computed via SQL GROUP BY customer_id on the events table.**

**Removed:** `category_diversity`, `brand_diversity` — these measure breadth of browsing, not purchase intent.

---

## Segment 2: Future High-Value / CLV

**What it is:** Predicts how much value each customer will generate over their lifetime. Identifies customers most worth investing in.

**Type:** ML (prediction problem — what is this customer's lifetime value?)

**Dataset:** Online Retail II (online_retail_II.csv)
**Kaggle URL:** https://www.kaggle.com/datasets/mashlyn/online-retail-ii-uci

**Dataset Files & Attributes:**

| File | Attributes |
|---|---|
| online_retail_II.csv | Invoice, StockCode, Description, Quantity, InvoiceDate, Price, Customer ID, Country |

### Raw Attributes (from dataset → our schema)

| Raw Attribute | Target Table | Target Column |
|---|---|---|
| Customer ID | customers | source_customer_id |
| Invoice | orders | source_order_id |
| InvoiceDate | orders | order_date |
| StockCode | products | source_product_id |
| Description | products | name |
| Quantity | order_items | quantity |
| Price | order_items | unit_price |

### Computed Attributes (derived → stored in `customer_features`)

| Computed Feature | Formula | Attributes Used | How Computed |
|---|---|---|---|
| recency | `CURRENT_DATE - MAX(order_date)` | orders.order_date | SQL MAX per customer |
| frequency | `COUNT(DISTINCT source_order_id)` | orders.source_order_id | SQL COUNT DISTINCT per customer |
| monetary | `SUM(quantity * unit_price)` | order_items.quantity, order_items.unit_price | SQL SUM per customer |
| avg_order_value | `SUM(quantity * unit_price) / COUNT(DISTINCT source_order_id)` | order_items.quantity, order_items.unit_price, orders.source_order_id | SQL SUM / COUNT per customer |
| customer_tenure | `CURRENT_DATE - MIN(order_date)` | orders.order_date | SQL MIN per customer |
| total_items_bought | `SUM(quantity)` | order_items.quantity | SQL SUM per customer |
| unique_products_bought | `COUNT(DISTINCT product_id)` | order_items.product_id | SQL COUNT DISTINCT per customer |
| max_single_order_value | `MAX(sales_value)` | order_items.sales_value | SQL MAX per customer |
| min_single_order_value | `MIN(sales_value)` | order_items.sales_value | SQL MIN per customer |
| purchase_regularity | `STDDEV(days between consecutive orders)` | orders.order_date | SQL window function (LAG) per customer |

**7 raw attributes + 10 computed features. Removed: `Country` (no segment/KPI uses geo).**

---

## Segment 3: Discount Responsive

**What it is:** Finds customers whose buying behavior is driven by discounts. Goal is to spend discount budget where it changes behavior, instead of giving coupons to people who would buy at full price anyway.

**Type:** ML (prediction problem — will this customer redeem a coupon?)

**Dataset:** Predicting Coupon Redemption
**Kaggle URL:** https://www.kaggle.com/datasets/vasudeva009/predicting-coupon-redemption

**Dataset Files & Attributes:**

| File | Attributes |
|---|---|
| train.csv | id, campaign_id, coupon_id, customer_id, redemption_status |
| campaign_data.csv | campaign_id, campaign_type, start_date, end_date |
| coupon_item_mapping.csv | coupon_id, item_id |
| customer_demographics.csv | customer_id, age_range, marital_status, rented, family_size, no_of_children, income_bracket |
| customer_transaction_data.csv | date, customer_id, item_id, quantity, selling_price, other_discount, coupon_discount |
| item_data.csv | item_id, brand, brand_type, category |
| test.csv | id, campaign_id, coupon_id, customer_id |

### Raw Attributes Needed (No Demographics)

| Raw Attribute | Target Table | Target Column |
|---|---|---|
| customer_id (train) | customers | source_customer_id |
| campaign_id | coupon_campaigns | source_campaign_id |
| coupon_id | coupon_redemptions | coupon_id |
| redemption_status | coupon_redemptions | redemption_status |
| campaign_type | coupon_campaigns | campaign_type |
| start_date | coupon_campaigns | start_date |
| end_date | coupon_campaigns | end_date |
| date | orders | order_date |
| item_id (transaction) | products | source_product_id |
| quantity | order_items | quantity |
| selling_price | order_items | selling_price |
| other_discount | order_items | other_discount |
| coupon_discount | order_items | coupon_discount |
| brand | products | brand |
| category | products | category |

### Computed Features (Behavioral Only)

| Computed Feature | Formula | Attributes Used | How Computed |
|---|---|---|---|
| total_discount_received | `SUM(other_discount + coupon_discount)` | order_items.other_discount, order_items.coupon_discount | SQL SUM per customer |
| discount_dependency_ratio | `SUM(other_discount + coupon_discount) / SUM(selling_price)` | order_items.other_discount, order_items.coupon_discount, order_items.selling_price | SQL SUM / SUM per customer |
| coupon_redemption_rate | `COUNT(redemption_status=1) / COUNT(*)` | coupon_redemptions.redemption_status | SQL COUNT per customer |
| avg_discount_per_order | `AVG(other_discount + coupon_discount)` | order_items.other_discount, order_items.coupon_discount | SQL AVG per customer |
| discount_order_frequency | `COUNT(orders with discount > 0) / COUNT(total orders)` | order_items.other_discount, order_items.coupon_discount | SQL COUNT per customer |

**Removed:** All demographics (age_range, marital_status, rented, family_size, no_of_children, income_bracket, brand_type)

**Customers table stays at 12 columns. No changes needed.**

---

## Segment 4: Churn-Risk

**What it is:** Identifies customers showing signs of slowing down or stopping orders entirely. Reach them while there's still time to win them back.

**Type:** ML (prediction problem — will this customer churn?)

**Dataset:** E-commerce Customer Churn (data_ecommerce_customer_churn.csv)
**Kaggle URL:** https://www.kaggle.com/datasets/samuelsemaya/e-commerce-customer-churn

**Dataset Files & Attributes:**

| File | Attributes |
|---|---|
| data_ecommerce_customer_churn.csv | Tenure, WarehouseToHome, NumberOfDeviceRegistered, PreferedOrderCat, SatisfactionScore, MaritalStatus, NumberOfAddress, Complain, DaySinceLastOrder, CashbackAmount, Churn |

### Raw Attributes Needed

| Raw Attribute | Target Table | Target Column | Strength | Why |
|---|---|---|---|---|
| Tenure | customers | tenure | STRONG | Longer tenure = more loyal = less likely to churn |
| WarehouseToHome | customers | warehouse_to_home | MODERATE | Delivery distance affects experience |
| NumberOfDeviceRegistered | customers | number_of_devices_registered | MODERATE | More devices = more engaged |
| SatisfactionScore | customers | satisfaction_score | STRONG | Direct satisfaction signal |
| Complain | customers | complain | STRONG | Complaints = direct churn signal |
| DaySinceLastOrder | customers | days_since_last_order | STRONG | Recency = churn signal |
| CashbackAmount | customers | cashback_amount | MODERATE | Cashback dependency |
| Churn | customers | churn | LABEL | What we're predicting |

### Computed Features

None needed. These are pre-computed features from the dataset, fed directly into the ML model.

**Removed from dataset (not needed):** `MaritalStatus`, `NumberOfAddress`, `PreferedOrderCat` — no direct link to churn behavior.

**Customers table: no changes needed.** All 8 attributes already exist.

---

## Segment 5: Channel Preference

**What it is:** Predicts which communication channel each customer is most likely to actually open and respond to. Route campaigns through their best channel instead of blasting everywhere.

**Type:** ML (prediction problem — which channel will this customer respond to?)

**Dataset:** E-commerce multichannel direct messaging
**Kaggle URL:** https://www.kaggle.com/datasets/mkechinov/direct-messaging

**Dataset Files & Attributes:**

| File | Attributes |
|---|---|
| campaigns.csv | id, campaign_type, channel, topic, started_at, finished_at, total_count, ab_test, warmup_mode, hour_limit, subject_length, subject_with_personalization, subject_with_deadline, subject_with_emoji, subject_with_bonuses, subject_with_discount, subject_with_saleout, is_test, position |
| messages-demo.csv | id, message_id, campaign_id, message_type, client_id, channel, category, platform, email_provider, stream, date, sent_at, is_opened, opened_first_time_at, opened_last_time_at, is_clicked, clicked_first_time_at, clicked_last_time_at, is_unsubscribed, unsubscribed_at, is_hard_bounced, hard_bounced_at, is_soft_bounced, soft_bounced_at, is_complained, complained_at, is_blocked, blocked_at, is_purchased, purchased_at, created_at, updated_at |
| client_first_purchase_date.csv | client_id, first_purchase_date |
| holidays.csv | date, holiday |

### Raw Attributes Needed

| Raw Attribute | Target Table | Target Column | Used For |
|---|---|---|---|
| client_id | customers | source_customer_id | Customer identifier |
| campaign_id | marketing_campaigns | source_campaign_id | Which campaign |
| campaign_type | marketing_campaigns | campaign_type | Campaign type |
| channel (campaign) | marketing_campaigns | channel | Campaign channel |
| topic | marketing_campaigns | topic | Campaign topic |
| started_at | marketing_campaigns | started_at | Campaign timing |
| finished_at | marketing_campaigns | finished_at | Campaign timing |
| total_count | marketing_campaigns | total_count | Campaign size |
| message_id | message_sends | source_message_id | Message identifier |
| client_id (message) | message_sends | customer_id | Which customer |
| message_type | message_sends | message_type | Type of message |
| channel (message) | message_sends | channel | Email / whatsapp / sms / push |
| sent_at | message_sends | sent_at | When message was sent |
| is_opened | message_sends | is_opened | Engagement signal |
| is_clicked | message_sends | is_clicked | Engagement signal |
| is_unsubscribed | message_sends | is_unsubscribed | Negative signal |
| is_hard_bounced | message_sends | is_hard_bounced | Negative signal |
| is_soft_bounced | message_sends | is_soft_bounced | Negative signal |
| is_complained | message_sends | is_complained | Negative signal |
| is_blocked | message_sends | is_blocked | Negative signal |
| is_purchased | message_sends | is_purchased | Conversion |
| first_purchase_date | customers | first_purchase_date | Already in customers table |
| date | holidays | date | Holiday lookup |
| holiday | holidays | holiday | Holiday name |

### Computed Features (stored in `customer_features`)

| Computed Feature | Formula | Attributes Used | How Computed |
|---|---|---|---|
| email_open_rate | `COUNT(is_opened=1 WHERE channel='email') / COUNT(WHERE channel='email')` | message_sends.is_opened, message_sends.channel | SQL per customer |
| email_click_rate | `COUNT(is_clicked=1 WHERE channel='email') / COUNT(WHERE channel='email')` | message_sends.is_clicked, message_sends.channel | SQL per customer |
| whatsapp_open_rate | `COUNT(is_opened=1 WHERE channel='whatsapp') / COUNT(WHERE channel='whatsapp')` | message_sends.is_opened, message_sends.channel | SQL per customer |
| sms_open_rate | `COUNT(is_opened=1 WHERE channel='sms') / COUNT(WHERE channel='sms')` | message_sends.is_opened, message_sends.channel | SQL per customer |
| unsubscribe_rate | `COUNT(is_unsubscribed=1) / COUNT(*)` | message_sends.is_unsubscribed | SQL per customer |
| bounce_rate | `COUNT(is_hard_bounced=1 OR is_soft_bounced=1) / COUNT(*)` | message_sends.is_hard_bounced, message_sends.is_soft_bounced | SQL per customer |
| purchase_rate | `COUNT(is_purchased=1) / COUNT(*)` | message_sends.is_purchased | SQL per customer |

**Removed from campaigns.csv:** `ab_test`, `warmup_mode`, `hour_limit`, `subject_length`, `subject_with_personalization`, `subject_with_deadline`, `subject_with_emoji`, `subject_with_bonuses`, `subject_with_discount`, `subject_with_saleout`, `is_test`, `position` — A/B testing config, not needed.

**Removed from messages-demo.csv:** `category`, `platform`, `email_provider`, `stream` — technical metadata, not needed.

**New tables needed:** `marketing_campaigns`, `message_sends`, `holidays`

---

## Segment 6: Replenishment-Ready

**What it is:** Predicts when a customer is likely to need the same product again — think consumables, refills, anything with a natural repurchase cycle.

**Type:** ML (prediction problem — when will this customer need to reorder?)

**Dataset:** Dunnhumby: The Complete Journey
**Kaggle URL:** https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey

### Raw Attributes Needed (11)

| Raw Attribute | Target Table | Target Column | Strength | Justification |
|---|---|---|---|---|
| household_key | customers | source_customer_id | ESSENTIAL | Customer identifier |
| BASKET_ID | orders | source_order_id | ESSENTIAL | Order identifier |
| DAY | orders | order_date | ESSENTIAL | When they bought |
| PRODUCT_ID | products | source_product_id | ESSENTIAL | Which product |
| QUANTITY | order_items | quantity | STRONG | Consumption rate |
| SALES_VALUE | order_items | sales_value | STRONG | Monetary value |
| WEEK_NO | orders | week_no | MODERATE | Seasonal pattern |
| STORE_ID | orders | region | MODERATE | Regional pattern |
| DEPARTMENT | products | department | MODERATE | Category analysis |
| BRAND | products | brand | MODERATE | Brand loyalty |
| COMMODITY_DESC | products | commodity_desc | MODERATE | Product grouping |

### Computed Features (5)

| Computed Feature | Formula | Attributes Used | How Computed |
|---|---|---|---|
| days_since_last_purchase | `CURRENT_DATE - MAX(order_date)` per customer per product | orders.order_date, order_items.product_id | SQL per customer+product |
| purchase_frequency | `COUNT(DISTINCT source_order_id)` per customer per product | orders.source_order_id, order_items.product_id | SQL per customer+product |
| avg_days_between_purchases | `AVG(days between consecutive purchases)` per customer per product | orders.order_date, order_items.product_id | SQL window function per customer+product |
| product_loyalty | `COUNT(DISTINCT product_id)` per customer | order_items.product_id | SQL COUNT DISTINCT per customer |
| replenishment_cycle | `STDDEV(days between purchases)` per customer per product | orders.order_date, order_items.product_id | SQL window function per customer+product |

### Removed Attributes

| Attribute | Reason |
|---|---|
| All demographics | Not included per user request |
| display, mailer | Promotion flags — not related to replenishment |
| TRANS_TIME | Time of day — not useful |
| MANUFACTURER | Not needed |
| SUB_COMMODITY_DESC | Too granular |
| CURR_SIZE_OF_PRODUCT | Not needed |
| coupon.csv, coupon_redempt.csv, campaign_table.csv, campaign_desc.csv | Coupon data — not needed (Discount Responsive segment) |
| RETAIL_DISC, COUPON_DISC, COUPON_MATCH_DISC | Discount behavior is Discount Responsive's job, not Replenishment-Ready |

### Schema Changes Needed

| Table | Change |
|---|---|
| `orders` | Add `week_no` (INT) — for seasonal/replenishment patterns |

**Customers table: no changes needed.** All 11 attributes already exist in existing tables.

---

## Segment 7: Cross-Sell Opportunity

**What it is:** Predicts which complementary product a customer is likely to buy next, based on what they've already purchased. Turns a single purchase into the start of a basket.

**Type:** ML (prediction problem — what complementary product will this customer buy next?)

**Dataset:** Dunnhumby: The Complete Journey
**Kaggle URL:** https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey

### Raw Attributes Needed (7)

| Raw Attribute | Target Table | Target Column | Strength | Justification |
|---|---|---|---|---|
| household_key | customers | source_customer_id | ESSENTIAL | Customer identifier |
| BASKET_ID | orders | source_order_id | ESSENTIAL | Groups items bought together |
| DAY | orders | order_date | ESSENTIAL | When they bought |
| PRODUCT_ID | products | source_product_id | ESSENTIAL | Which product |
| DEPARTMENT | products | department | STRONG | Category for finding complementary products |
| BRAND | products | brand | MODERATE | Brand affinity |
| COMMODITY_DESC | products | commodity_desc | MODERATE | Product grouping |

### Computed Features (stored in `customer_features`)

| Computed Feature | Formula | Attributes Used | How Computed |
|---|---|---|---|
| basket_size | `COUNT(DISTINCT product_id)` per order | order_items.product_id | SQL COUNT DISTINCT per order |
| category_diversity | `COUNT(DISTINCT department)` per customer | products.department | SQL COUNT DISTINCT per customer |
| cross_category_purchase | `COUNT(DISTINCT department) / COUNT(DISTINCT product_id)` | products.department, order_items.product_id | SQL ratio per customer |
| complementary_affinity | `COUNT(DISTINCT product_id WHERE department != primary_dept)` | products.department, order_items.product_id | SQL per customer |
| brand_concentration | `COUNT(DISTINCT brand) / COUNT(DISTINCT product_id)` | products.brand, order_items.product_id | SQL ratio per customer |

### Removed Attributes

| Attribute | Reason |
|---|---|
| All demographics | Not included per user request |
| QUANTITY, SALES_VALUE | Already covered in CLV (Segment 2) and Replenishment-Ready (Segment 6) |
| STORE_ID | Not directly related to cross-sell |
| display, mailer | Promotion flags — not needed |
| TRANS_TIME | Time of day — not useful |
| MANUFACTURER | Not needed |
| SUB_COMMODITY_DESC | Too granular |
| CURR_SIZE_OF_PRODUCT | Not needed |
| RETAIL_DISC, COUPON_DISC, COUPON_MATCH_DISC | Discount data — Discount Responsive's job |
| WEEK_NO | Already in orders table |
| coupon.csv, coupon_redempt.csv, campaign_table.csv, campaign_desc.csv | Coupon data — not needed |

### Schema Changes Needed

**None.** All attributes already exist in existing tables.

---

## Segment 8: Seasonal Purchase

**What it is:** Predicts customers who tend to buy during an upcoming seasonal period or event — holidays, weather shifts, yearly sales. Lets the store get in front of the season instead of reacting to it.

**Type:** ML (prediction problem — will this customer buy in the upcoming season?)

**Dataset:** Dunnhumby: The Complete Journey
**Kaggle URL:** https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey

**Dataset Files & Attributes:**

| File | Attributes |
|---|---|
| transaction_data.csv | household_key, BASKET_ID, DAY, PRODUCT_ID, QUANTITY, SALES_VALUE, STORE_ID, RETAIL_DISC, TRANS_TIME, WEEK_NO, COUPON_DISC, COUPON_MATCH_DISC |
| hh_demographic.csv | AGE_DESC, MARITAL_STATUS_CODE, INCOME_DESC, HOMEOWNER_DESC, HH_COMP_DESC, HOUSEHOLD_SIZE_DESC, KID_CATEGORY_DESC, household_key |
| product.csv | PRODUCT_ID, MANUFACTURER, DEPARTMENT, BRAND, COMMODITY_DESC, SUB_COMMODITY_DESC, CURR_SIZE_OF_PRODUCT |
| causal_data.csv | PRODUCT_ID, STORE_ID, WEEK_NO, display, mailer |
| coupon.csv | COUPON_UPC, PRODUCT_ID, CAMPAIGN |
| coupon_redempt.csv | household_key, DAY, COUPON_UPC, CAMPAIGN |
| campaign_table.csv | DESCRIPTION, household_key, CAMPAIGN |
| campaign_desc.csv | DESCRIPTION, CAMPAIGN, START_DAY, END_DAY |
| holidays.csv | date, holiday |

### Raw Attributes Needed (12)

| Raw Attribute | Target Table | Target Column | Strength | Justification |
|---|---|---|---|---|
| household_key | customers | source_customer_id | ESSENTIAL | Customer identifier |
| BASKET_ID | orders | source_order_id | ESSENTIAL | Order identifier |
| DAY | orders | order_date | ESSENTIAL | When they bought |
| PRODUCT_ID | products | source_product_id | ESSENTIAL | Which product |
| WEEK_NO | orders | week_no | STRONG | Seasonal pattern |
| DEPARTMENT | products | department | STRONG | Category seasonal analysis |
| BRAND | products | brand | MODERATE | Brand seasonal affinity |
| COMMODITY_DESC | products | commodity_desc | MODERATE | Product grouping |
| display | causal_data | display | MODERATE | Promotion flag for seasonal response |
| mailer | causal_data | mailer | MODERATE | Promotion flag for seasonal response |
| date | holidays | date | STRONG | Holiday lookup |
| holiday | holidays | holiday | STRONG | Holiday name |

### Computed Features (stored in `customer_features`)

| Computed Feature | Formula | Attributes Used | How Computed |
|---|---|---|---|
| purchase_by_week | `COUNT(*)` per customer per week | orders.week_no | SQL GROUP BY customer, week |
| seasonal_concentration | `MAX(weekly_purchase_count) / AVG(weekly_purchase_count)` | orders.week_no | SQL per customer |
| seasonal_category | `MODE(department WHERE week_no IN holiday_weeks)` | products.department, orders.week_no | SQL per customer |
| days_until_holiday | `MIN(holiday_date) - CURRENT_DATE` | holidays.date | SQL per customer |
| seasonal_purchase_ratio | `purchases in holiday weeks / total purchases` | orders.week_no | SQL per customer |
| promo_response_rate | `purchases when display/mailer = 1 / total purchases` | causal_data.display, causal_data.mailer | SQL per customer |

### Removed Attributes

| Attribute | Reason |
|---|---|
| All demographics | Not included per user request |
| QUANTITY, SALES_VALUE | Already covered in CLV (Segment 2) and Replenishment-Ready (Segment 6) |
| STORE_ID | Not directly related to seasonal purchase |
| TRANS_TIME | Time of day — not useful |
| MANUFACTURER | Not needed |
| SUB_COMMODITY_DESC | Too granular |
| CURR_SIZE_OF_PRODUCT | Not needed |
| RETAIL_DISC, COUPON_DISC, COUPON_MATCH_DISC | Discount data — Discount Responsive's job |
| coupon.csv, coupon_redempt.csv, campaign_table.csv, campaign_desc.csv | Coupon data — not needed |

### Schema Changes Needed

| Table | Change |
|---|---|
| `causal_data` (new) | Add `display` (VARCHAR), `mailer` (VARCHAR) — promotion flags |
| `holidays` (new) | Add `date` (DATE), `holiday` (VARCHAR) — holiday lookup |

**New tables needed:** `causal_data`, `holidays`

---

## Segment 9: Cart Abandoners

**What it is:** Predicts customers who are likely to leave without completing checkout. The value is in the likelihood — it lets the store decide how hard (and how expensive) the recovery push should be.

**Type:** ML (prediction problem — will this customer abandon their cart?)

**Dataset:** eCommerce behavior data from multi category store (same as Segment 1)
**Kaggle URL:** https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store

**Dataset Files & Attributes:**

| File | Attributes |
|---|---|
| 2019-Oct.csv | event_time, event_type, product_id, category_id, category_code, brand, price, user_id, user_session |
| 2019-Nov.csv | event_time, event_type, product_id, category_id, category_code, brand, price, user_id, user_session |

### Raw Attributes Needed (9)

| Raw Attribute | Target Table | Target Column | Strength | Justification |
|---|---|---|---|---|
| event_type | events | event_type | ESSENTIAL | Core signal: cart vs purchase |
| product_id | events | product_id | STRONG | What product they abandoned |
| category_id | events | category_id | MODERATE | What category |
| category_code | events | category_code | MODERATE | Category grouping |
| brand | events | brand | MODERATE | Brand preference |
| price | events | price | STRONG | Price affects abandonment (expensive = more likely to abandon) |
| user_id | events | customer_id | ESSENTIAL | Which customer |
| user_session | events | session_id | ESSENTIAL | Groups events into sessions |
| event_time | events | event_timestamp | ESSENTIAL | When they added to cart |

### Computed Features (stored in `customer_features`)

| Computed Feature | Formula | Attributes Used | How Computed |
|---|---|---|---|
| cart_count | `COUNT(*) WHERE event_type='cart'` | events.event_type | SQL GROUP BY customer_id |
| purchase_count | `COUNT(*) WHERE event_type='purchase'` | events.event_type | SQL GROUP BY customer_id |
| cart_abandonment_rate | `1 - (purchase_count / cart_count)` | derived from purchase_count, cart_count | Division of two computed features |
| last_cart_days | `CURRENT_DATE - MAX(event_timestamp) WHERE event_type='cart'` | events.event_timestamp, events.event_type | SQL date diff, filter event_type |
| session_count | `COUNT(DISTINCT session_id)` | events.session_id | SQL GROUP BY customer_id |
| avg_cart_value | `AVG(price) WHERE event_type='cart'` | events.price, events.event_type | SQL AVG, filter event_type |
| view_to_cart_rate | `cart_count / view_count` | derived from cart_count, view_count | Division of two computed features |

### Key Difference from Segment 1

| | Segment 1: Purchase Intent | Segment 9: Cart Abandoners |
|---|---|---|
| **Question** | Will they buy soon? | Will they leave without checkout? |
| **Focus** | Browsing → Cart → Purchase | Cart → Purchase (or no purchase) |
| **Key Metric** | view_to_cart_rate, cart_to_purchase_rate | cart_abandonment_rate |
| **Marketing Action** | Product recommendations, reminders | Cart recovery emails, incentives |

**Same dataset, same raw attributes, but different computed features and different predictions.**

### Schema Changes Needed

**None.** All attributes already exist in `events` table.
