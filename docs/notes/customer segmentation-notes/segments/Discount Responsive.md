# Discount Responsive

## Dataset

Predicting Coupon Redemption

https://www.kaggle.com/datasets/vasudeva009/predicting-coupon-redemption

## Description

XYZ Credit Card company helps merchant ABC (Brick & Mortar retailer) predict coupon redemption using machine learning. Contains user demographics, campaign/coupon details, product info, and previous transactions from 18 campaigns. Task: predict redemption probability for 10 test campaigns. Data collected across email, notifications, and other channels.

## Files & Attributes

train.csv

- id — Unique id for coupon customer impression
- campaign_id — Unique id for a discount campaign
- coupon_id — Unique id for a discount coupon
- customer_id — Unique id for a customer
- redemption_status — (target) (0 - Coupon not redeemed, 1 - Coupon redeemed)

campaign_data.csv

- campaign_id — Unique id for a discount campaign
- campaign_type — Anonymised Campaign Type (X/Y)
- start_date — Campaign Start Date
- end_date — Campaign End Date

coupon_item_mapping.csv

- coupon_id — Unique id for a discount coupon (no order)
- item_id — Unique id for items for which given coupon is valid (no order)

customer_demographics.csv

- customer_id — Unique id for a customer
- age_range — Age range of customer family in years
- marital_status — Married/Single
- rented — 0 - not rented accommodation, 1 - rented accommodation
- family_size — Number of family members
- no_of_children — Number of children in the family
- income_bracket — Label Encoded Income Bracket (Higher income corresponds to higher number)

customer_transaction_data.csv

- date — Date of Transaction
- customer_id — Unique id for a customer
- item_id — Unique id for item
- quantity — Quantity of item bought
- selling_price — Sales value of the transaction
- other_discount — Discount from other sources such as manufacturer coupon/loyalty card
- coupon_discount — Discount availed from retailer coupon

item_data.csv

- item_id — Unique id for item
- brand — Unique id for item brand
- brand_type — Brand Type (local/Established)
- category — Item Category

test.csv

- id — Unique id for coupon customer impression
- campaign_id — Unique id for a discount campaign
- coupon_id — Unique id for a discount coupon
- customer_id — Unique id for a customer
