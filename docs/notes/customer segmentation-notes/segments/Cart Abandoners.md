# Cart Abandoners

## Dataset

eCommerce behavior data from multi category store

https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store

## Description

7 months (Oct 2019 - Apr 2020) of behavior data from a large multi-category online store. Each row is an event (view, cart, remove_from_cart, purchase) related to products and users. Data collected by REES46 Open CDP.

## Files & Attributes

2019-Oct.csv

- event_time — Time when event happened at (in UTC).
- event_type — Only one kind of event: purchase.
- product_id — ID of a product
- category_id — Product's category ID
- category_code — Product's category taxonomy (code name) if it was possible to make it. Usually present for meaningful categories and skipped for different kinds of accessories.
- brand — Downcased string of brand name. Can be missed.
- price — Float price of a product. Present.
- user_id — Permanent user ID.
- user_session — Temporary user's session ID. Same for each user's session. Is changed every time user come back to online store from a long pause.

2019-Nov.csv

- event_time — Time when event happened at (in UTC).
- event_type — Only one kind of event: purchase.
- product_id — ID of a product
- category_id — Product's category ID
- category_code — Product's category taxonomy (code name) if it was possible to make it. Usually present for meaningful categories and skipped for different kinds of accessories.
- brand — Downcased string of brand name. Can be missed.
- price — Float price of a product. Present.
- user_id — Permanent user ID.
- user_session — Temporary user's session ID. Same for each user's session. Is changed every time user come back to online store from a long pause.
