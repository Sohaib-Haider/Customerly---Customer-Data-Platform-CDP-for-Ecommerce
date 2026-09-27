# Replenishment-Ready

## Dataset

Dunnhumby: The Complete Journey

https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey

## Files & Attributes

transaction_data.csv

- household_key
- BASKET_ID
- DAY
- PRODUCT_ID
- QUANTITY
- SALES_VALUE
- STORE_ID
- RETAIL_DISC
- TRANS_TIME
- WEEK_NO
- COUPON_DISC
- COUPON_MATCH_DISC

hh_demographic.csv

- AGE_DESC
- MARITAL_STATUS_CODE
- INCOME_DESC
- HOMEOWNER_DESC
- HH_COMP_DESC
- HOUSEHOLD_SIZE_DESC
- KID_CATEGORY_DESC
- household_key

product.csv

- PRODUCT_ID
- MANUFACTURER
- DEPARTMENT
- BRAND
- COMMODITY_DESC
- SUB_COMMODITY_DESC
- CURR_SIZE_OF_PRODUCT

causal_data.csv

- PRODUCT_ID
- STORE_ID
- WEEK_NO
- display
- mailer

coupon.csv

- COUPON_UPC
- PRODUCT_ID
- CAMPAIGN

coupon_redempt.csv

- household_key
- DAY
- COUPON_UPC
- CAMPAIGN

campaign_table.csv

- DESCRIPTION
- household_key
- CAMPAIGN

campaign_desc.csv

- DESCRIPTION
- CAMPAIGN
- START_DAY
- END_DAY