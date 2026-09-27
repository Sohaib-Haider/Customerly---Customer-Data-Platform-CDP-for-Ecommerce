# Segments

## Overview

This file is the reference sheet for the nine customer segments we've finalized for the platform. For each segment it tells you which source dataset (a public Kaggle e-commerce dataset) it's built on, what the segment actually means in plain words, and — most importantly — what marketing actions the store can take once we've flagged a customer as belonging to it.

In short: we look at each store's customer data, run them through the segments they qualify for, and end up with lists like "these people will probably buy soon", "these people are about to leave", or "these people love a good discount". This file is the marketing playbook for acting on those lists.

Every segment is tied to a real dataset so we can train ML models or build rules on realistic e-commerce behavior instead of guessing.

## Segments at a Glance

| Segment | Source Dataset (Kaggle) |
| --- | --- |
| Predicted Purchase Intent | [eCommerce behavior data from multi category store](https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store) |
| Future High-Value / CLV | [Online Retail II](https://www.kaggle.com/datasets/mashlyn/online-retail-ii-uci) |
| Discount Responsive | [Predicting Coupon Redemption](https://www.kaggle.com/datasets/vasudeva009/predicting-coupon-redemption) |
| Churn-Risk | [E-commerce Customer Churn](https://www.kaggle.com/datasets/samuelsemaya/e-commerce-customer-churn) |
| Channel Preference | [E-commerce multichannel direct messaging](https://www.kaggle.com/datasets/mkechinov/direct-messaging) |
| Replenishment-Ready | [Dunnhumby: The Complete Journey](https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey) |
| Cross-Sell Opportunity | [Dunnhumby: The Complete Journey](https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey) |
| Seasonal Purchase | [Dunnhumby: The Complete Journey](https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey) |
| Cart Abandoners | [eCommerce behavior data from multi category store](https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store) |

---

## 1. Predicted Purchase Intent

**Dataset:** [Kaggle — eCommerce behavior data from multi category store](https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store)

**What it does:** Flags the customers most likely to make a purchase in the near future. We look at the products and categories a customer has been viewing and use that to guess their next move.

**Marketing actions:**
- Send product and category recommendations based on what they've been browsing.
- Fire off reminders for viewed-but-not-bought items.
- Follow up on abandoned sessions with a gentle nudge.
- Retarget them with relevant offers through personalized placements.
- Channels: Email + Push + WhatsApp/SMS.

---

## 2. Future High-Value / CLV

**Dataset:** [Kaggle — Online Retail II](https://www.kaggle.com/datasets/mashlyn/online-retail-ii-uci)

**What it does:** Predicts how much value each customer is likely to generate over their lifetime. These are the customers most worth investing in — and most worth protecting from too-good-to-be-true discounting.

**Marketing actions:**
- Invite them to VIP programs and give them premium recommendations.
- Offer them early access to new products and sales.
- Reward loyalty and long-term engagement with exclusive perks.
- Avoid showering them with discounts — they'd probably buy at full price anyway.
- Channels: Email + WhatsApp + Push.

---

## 3. Discount Responsive

**Dataset:** [Kaggle — Predicting Coupon Redemption](https://www.kaggle.com/datasets/vasudeva009/predicting-coupon-redemption)

**What it does:** Finds the customers whose buying behavior is actually driven by discounts. The goal is to spend discount budget where it changes behavior, instead of giving coupons to people who would purchase at full price regardless.

**Marketing actions:**
- Send personalized coupons and limited-time discounts.
- Push bundle deals and promotional offers that play into their price sensitivity.
- Use discount-heavy campaigns with confidence for this group only.
- Channels: Email + SMS/WhatsApp + Push.

---

## 4. Churn-Risk

**Dataset:** [Kaggle — E-commerce Customer Churn](https://www.kaggle.com/datasets/samuelsemaya/e-commerce-customer-churn)

**What it does:** Identifies customers who are showing signs of slowing down or stopping orders entirely. The whole point is to reach them while there's still time to win them back.

**Marketing actions:**
- Send personalized win-back offers before they fully drop off.
- Remind them of products they used to buy.
- Run service-recovery messages if they've had a bad experience (complaints, delivery issues, etc.).
- Offer loyalty incentives to pull them back into the regular buying cycle.
- Channels: Email + WhatsApp/SMS + Push.

---

## 5. Channel Preference

**Dataset:** [Kaggle — E-commerce multichannel direct messaging](https://www.kaggle.com/datasets/mkechinov/direct-messaging)

**What it does:** Predicts which communication channel each customer is most likely to actually open and respond to. Not everybody reads email; some people only click when something lands on WhatsApp or as an SMS.

**Marketing actions:**
- Route each campaign through the customer's predicted best channel (Email, WhatsApp, SMS, or Push).
- Adjust message frequency per customer so we don't nag the light responders.
- Improve engagement by respecting the channel they prefer instead of blasting everything everywhere.
- Channels: whichever the model predicts — Email, WhatsApp, SMS, or Push.

---

## 6. Replenishment-Ready

**Dataset:** [Kaggle — Dunnhumby: The Complete Journey](https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey)

**What it does:** Predicts when a customer is likely to need the same product again — think consumables, refills, anything with a natural repurchase cycle. Timing is everything here.

**Marketing actions:**
- Send a reminder shortly before the predicted reorder date.
- Offer a one-click reorder or a subscription option to lock the habit in.
- Suggest relevant add-ons to go with the reorder.
- Channels: Email + Push + WhatsApp/SMS.

---

## 7. Cross-Sell Opportunity

**Dataset:** [Kaggle — Dunnhumby: The Complete Journey](https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey)

**What it does:** Predicts which complementary product a customer is likely to buy next, based on what they've already purchased. It turns a single purchase into the start of a basket.

**Marketing actions:**
- Recommend complementary add-ons and relevant bundles.
- Show post-purchase recommendations and cart/checkout suggestions.
- Push products that naturally go together with what they already own.
- Channels: Email + Push + WhatsApp/SMS.

---

## 8. Seasonal Purchase

**Dataset:** [Kaggle — Dunnhumby: The Complete Journey](https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey)

**What it does:** Predicts customers who tend to buy during an upcoming seasonal period or event — holidays, weather shifts, yearly sales. It lets the store get in front of the season instead of reacting to it.

**Marketing actions:**
- Send seasonal recommendations and early-access invites before the expected season.
- Remind them of the upcoming event with relevant offers.
- Time the campaign so the message lands while they're still planning, not already shopping elsewhere.
- Channels: Email + WhatsApp/SMS + Push.

---

## 9. Cart Abandoners

**Dataset:** [Kaggle — eCommerce behavior data from multi category store](https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store)

**What it does:** Predicts customers who are likely to leave without completing checkout. The value here is in the *likelihood* — it lets the store decide how hard (and how expensive) the recovery push should be.

**Marketing actions:**
- Trigger an email / WhatsApp / SMS / push reminder featuring the abandoned product.
- Add personalized recommendations or a checkout reminder to bring them back.
- For higher-risk customers who need a nudge: offer a limited-time incentive.
- For low-risk customers who just forgot: send a plain reminder — no discount needed.
- This helps the store recover carts without giving away unnecessary discounts.
- Channels: Triggered Email + WhatsApp/SMS + Push.