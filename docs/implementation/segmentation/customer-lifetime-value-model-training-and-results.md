# Customer Lifetime Value Segment — Training Implementation and Results

## Description and evidence

This document records the implemented workflow and saved results in [clv_prediction.ipynb](../../../data_prep/training/clv_prediction.ipynb). Its ten code cells include four saved chart figures, several with multiple panels. Each figure was visually inspected for this write-up. Numbers below refer to the current saved outputs, not a newly executed training run.

The artifact is `backend/ml_models/clv_model.pkl`. Its saved status is **`experimental_baseline_not_beaten`**. The selected Random Forest improves validation MAE but performs worse than the historical-rate baseline on the final test period. It must not be described as a production-approved model.

## Goal and scope

Estimate each eligible existing customer's **positive purchase revenue over the next 90 days, in GBP**, using historical Online Retail II transactions. This supports the Future High-Value / CLV segment's intended VIP and customer-value analysis.

The implemented target is a fixed-horizon revenue estimate before returns, not unlimited lifetime value, discounted profit, purchase probability, or guaranteed future revenue. It includes retained positive postage/carriage charges and excludes identified accounting/manual/test codes. It does not estimate causal campaign uplift.

Training uses the raw public historical Retail dataset. Its transformed copy is used only to test feature/target/prediction compatibility with the platform representation. Shared store ownership does not imply that independent source populations are the same customers or currencies.

## Strategy and technology stack

The implemented sequence is raw EDA, explicit purchase filtering, invoice aggregation, historical customer-feature construction, chronological examples, baseline/model comparison, validation permutation importance, held-out evaluation, raw/processed compatibility checks, final refit, artifact reload verification, and historical current scoring.

| Technology | Actual use |
|---|---|
| Python and Jupyter | Notebook and saved outputs |
| pandas and NumPy | Transaction preparation, windows, aggregates, comparisons |
| Decimal | Parse source prices and multiply quantity by price before float conversion |
| Matplotlib and Seaborn | Distributions, correlations, importance, prediction diagnostics |
| scikit-learn | Pipelines, imputation, scaling, three regression families, importance and metrics |
| joblib | Serialize and reload the dictionary artifact |
| pathlib, hashlib, json | Portable paths, raw-file checksum, processed manifest |

Saved execution environment: **Python 3.13.3, NumPy 2.5.4, pandas 3.0.6, scikit-learn 1.9.1, joblib 1.6.0**. Use compatible recorded versions when reproducing/loading the artifact. No extra modelling framework or deep-learning package is used.

## Step 1 — Load and profile raw Online Retail II

Input: `data_prep/raw/online-retail-ii-uci/online_retail_II.csv`. The notebook locates the project by searching the current directory and parents for that file. It reads source fields as strings, retaining source-ID spellings and avoiding automatic missing-value interpretation.

| Raw observation | Saved value |
|---|---:|
| Rows | 1,067,371 |
| Columns | 8 |
| Identified customers | 5,942 |
| Distinct invoices, including cancellation/other source records | 53,628 |
| Distinct stock codes | 5,305 |
| Missing customer IDs | 243,007 |
| Blank descriptions | 4,382 |
| Exact repeated lines beyond first | 34,335 |
| Cancellation-prefixed lines | 19,494 |
| Negative quantities | 22,950 |
| Zero prices | 6,202 |
| Negative prices | 5 |

Source timestamps range from **2009-12-01 07:45:00** to **2011-12-09 12:50:00**. These are source-local historical values, not shifted modern demo dates.

Printed largest line amounts and stock-code descriptions expose both genuine large purchases and non-merchandise/accounting records. Examples include a £168,469.60 merchandise line, `M`/Manual entries, `AMAZONFEE`, and `B`/Adjust bad debt. This inspection motivated exact-code exclusions rather than treating every positive line as a customer purchase or rejecting every alphabetic stock code.

## Step 2 — Apply the purchase policy

`prepare_purchases()` is reused for raw and reconstructed processed transactions. It:

1. Trims customer, invoice and product identifiers, preserving meaningful source strings such as a customer ID's `.0` suffix.
2. Parses source timestamps, quantities, and decimal prices.
3. Flags missing identifiers, invalid dates/numbers, fractional quantities, nonpositive quantities/prices, cancellation invoices, and explicit accounting/manual/test codes.
4. Excludes the union of flagged rows, reporting distinct exclusions separately from overlapping reason counts.
5. Calculates line revenue using decimal quantity-price multiplication before converting to floating point for aggregation/modelling.
6. Normalizes dates to local calendar days and rejects invoices whose retained lines disagree on customer or date.

Excluded stock codes, compared case-insensitively, are `B`, `ADJUST`, `ADJUST2`, `BANK CHARGES`, `AMAZONFEE`, `CRUK`, `D`, `M`, `TEST001`, and `TEST002`. Manual amounts are unverified rather than proven fraudulent. Postage/carriage and genuine alphanumeric merchandise codes remain. Repeated lines and genuine large purchases remain; the notebook does not automatically delete them as outliers.

The raw run reports **1,857 accounting/manual/test-code flags**, **262,613 distinct excluded rows**, and **804,758 retained purchase lines**. Reason counts overlap and must not simply be added.

Returns remain preserved in canonical processed data; excluding them here is an explicit model-target policy, not a transformation change. Revenue therefore must not be interpreted as profit or net revenue after refunds.

## Step 3 — Build complete purchase invoices and inspect distributions

Line revenue is `Quantity × Price`. Retained lines are grouped by customer, invoice and date to obtain complete retained-purchase invoice totals. Consequently, order-value features are based on invoice totals rather than a single product line's amount.

| Prepared-data result | Saved value |
|---|---:|
| Purchase invoices | 36,711 |
| Purchasing customers | 5,854 |
| Median invoice value | £305.40 |
| Mean invoice value | £479.03 |
| 99th-percentile invoice value | £3,670.30 |
| Maximum invoice value | £168,469.60 |
| Median customer spending | £899.215 |
| Maximum customer spending | £608,821.65 |
| Revenue share of highest-spending approximately 1% of customers | 32.02% |

### Figure 1: Three EDA panels — notebook cell 7

**Monthly purchase revenue:** revenue rises strongly toward late 2010 and late 2011, with visible November peaks. This supports considering seasonality when interpreting different 90-day outcome windows. The final December point covers an incomplete month, so its decline is not evidence of a business collapse. Monthly EDA uses the prepared source period; evaluation separately omits the final potentially partial day.

**Complete invoice values:** the histogram displays `log(1 + GBP)`, making typical invoices visible despite a long high-value tail. Most invoices cluster in the central log range, while a small number are much larger. The log scale is a visualization choice; the fitted target is not log-transformed and reported errors remain in GBP.

**Orders per customer:** most customers have comparatively few invoices, with a long tail extending to hundreds. This helps explain why interval features are undefined for many customers and why large repeat buyers can dominate revenue. The broad first histogram bin is not an exact one-time-buyer count.

## Step 4 — Construct historical features and labels

The notebook reserves five separate 90-day outcome windows: three for training, one for validation, and one for testing. Given source coverage, its formula selects a **288-day history window**. This is a coverage-driven choice, not a validated optimal history length.

For cutoff `c`, history is `[c − 288 days, c)` and the target is purchase revenue in `[c, c + 90 days)`. The start is included and the end excluded. The historical feature set never uses that example's future outcomes. Customers with no future purchase receive zero only when the entire outcome window is observed.

The exclusive endpoint is **2011-12-09 00:00:00**, conservatively omitting the final source day because complete-day coverage is not established. Recency uses historical cutoffs, not the machine's current date.

| Partition | Cutoff | History starts | Outcome ends, exclusive | Customers | Zero targets |
|---|---|---|---|---:|---:|
| Training 1 | 2010-09-15 | 2009-12-01 | 2010-12-14 | 3,377 | 42.5% |
| Training 2 | 2010-12-14 | 2010-03-01 | 2011-03-14 | 4,024 | 68.4% |
| Training 3 | 2011-03-14 | 2010-05-30 | 2011-06-12 | 3,930 | 60.9% |
| Validation | 2011-06-12 | 2010-08-28 | 2011-09-10 | 3,982 | 62.0% |
| Test | 2011-09-10 | 2010-11-26 | 2011-12-09 | 3,437 | 43.7% |

Training contains **11,331 customer snapshots representing 4,585 unique customers**. Known customers can recur across chronological snapshots. This matches existing-customer prediction but does not measure performance on brand-new customers. History windows overlap; future outcome windows are separate and training labels finish before validation begins.

### Engineered feature definitions and final order

All eleven candidates are ultimately retained:

| Feature | Calculation within history |
|---|---|
| `recency` | Days from cutoff to latest purchase date |
| `frequency` | Distinct retained purchase invoices |
| `monetary` | Sum of retained purchase revenue in GBP |
| `avg_order_value` | Monetary divided by frequency |
| `customer_tenure` | Days from cutoff to earliest observed purchase within this history |
| `total_items_bought` | Sum of retained positive quantity |
| `unique_products_bought` | Distinct retained source stock codes, including valid charge codes |
| `max_single_order_value` | Maximum complete retained-purchase invoice total |
| `min_single_order_value` | Minimum complete retained-purchase invoice total |
| `avg_days_between_orders` | Mean consecutive invoice date gap |
| `purchase_regularity` | Sample standard deviation of invoice date gaps, `ddof=1` |

Separate same-day invoices produce zero-day gaps. One invoice has no interval; two invoices have one interval and no sample standard deviation. Three or more invoices can legitimately yield zero regularity if gaps are equal. Missingness and zero therefore retain different meanings.

Training has **4,214 missing mean-interval values** and **6,457 missing regularity values**; the other feature columns have no missing values. Customer IDs and invoice IDs are grouping keys, not numeric predictors. Country and description text are not evaluated as model inputs; the experiment does not prove them irrelevant.

### Figure 2: Training feature correlations — cell 9

The heatmap shows strong positive relationships among monetary value, item quantity, purchase frequency, and some order-value features. Average and maximum order value are closely related. Average purchase interval and its variation are also positively related; frequency is negatively associated with interval measures. These relationships indicate overlapping information, not an automatic reason to drop columns. The chart covers training snapshots only and does not include the future target.

## Step 5 — Preprocess and compare candidates

Each trained candidate uses `SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)`. It learns medians on training only. Missingness indicators allow the estimator to distinguish unavailable interval information from observed zero intervals. Ridge additionally uses `StandardScaler`; tree models do not.

The historical-rate baseline is `monetary × 90 / 288`. It projects spending averaged over the entire fixed history window, not only the days since a customer's first observed purchase.

| Candidate | Explicit settings varied or fixed |
|---|---|
| Ridge Regression | `alpha=10` or `100`; training-only imputation and scaling |
| Random Forest Regressor | 150 trees; `min_samples_leaf=5` or `15`; all features eligible at splits; seed 42; two worker jobs |
| Histogram Gradient Boosting | 150 iterations; `max_leaf_nodes=7` or `15`; minimum leaf size 20; L2 regularization 1; seed 42 |

Boosting early stopping is disabled to avoid automatic random internal validation. Model predictions are clipped at zero consistently for selection, evaluation and scoring. No log-target transformation, arbitrary production-acceptance percentage, or extensive hyperparameter search is implemented.

### Validation results

Selection uses the **lowest validation MAE**, not classification accuracy or the highest R²:

| Candidate | MAE, GBP | RMSE, GBP | R² |
|---|---:|---:|---:|
| Random Forest, leaf 5 | 354.998463 | 1,257.625811 | 0.681271 |
| Ridge, alpha 10 | 356.064989 | 1,214.693900 | 0.702661 |
| Ridge, alpha 100 | 357.082314 | 1,217.838910 | 0.701119 |
| Histogram Gradient Boosting, 7 leaves | 364.837688 | 1,198.512199 | 0.710530 |
| Random Forest, leaf 15 | 367.035847 | 1,356.176905 | 0.629361 |
| Histogram Gradient Boosting, 15 leaves | 370.762029 | 1,219.164559 | 0.700468 |
| Historical-rate baseline | 380.033416 | 1,233.834381 | 0.693216 |

Random Forest with minimum leaf size 5 wins the chosen MAE criterion, improving validation MAE by **£25.03**. Other candidates have better validation RMSE/R², so the winner is specific to the declared selection criterion.

## Step 6 — Validation-based feature assessment

The notebook computes permutation importance for the selected trained candidate using three repeats, seed 42, and negative MAE of clipped predictions. The reported value is how much validation MAE increases when a feature is shuffled, in GBP, rather than an impurity-importance percentage.

### Figure 3: Validation permutation importance — cell 13

| Feature | Mean MAE increase, GBP |
|---|---:|
| monetary | 382.886930 |
| total_items_bought | 60.346903 |
| max_single_order_value | 30.904624 |
| unique_products_bought | 29.712640 |
| avg_order_value | 15.152004 |
| customer_tenure | 14.572236 |
| recency | 13.895851 |
| min_single_order_value | 12.147362 |
| avg_days_between_orders | 5.432680 |
| purchase_regularity | 2.139017 |
| frequency | 1.580020 |

The monetary bar dominates, showing heavy dependence on past spending. Frequency contributes less conditional information in this fitted forest, which also receives correlated monetary and quantity features. This does not prove frequency is useless in general.

All eleven means are positive, so the code's conditional smaller-set branch is **not executed**. The saved run does not contain an actual reduced-feature model comparison. It retains all eleven features and prints an empty removed-feature list. Positive importance is evidence for this fitted configuration, not proof of an optimal feature set.

## Step 7 — Final test-period results

Settings and features are fixed before evaluating the saved final test period:

| Candidate | MAE, GBP | RMSE, GBP | R² |
|---|---:|---:|---:|
| Selected Random Forest | 573.221345 | 2,826.169576 | 0.509784 |
| Historical-rate baseline | 544.656419 | 2,670.479681 | 0.562307 |

The model's test MAE is **£28.56 worse** than the baseline, which also has better RMSE/R². The result does not support claiming superior final predictive performance.

| Test group | Customers | Model MAE, GBP | Baseline MAE, GBP |
|---|---:|---:|---:|
| Zero future spending | 1,502 | 234.86 | 235.05 |
| Positive future spending | 1,935 | 835.87 | 784.99 |
| Highest actual-spending group, at/above 90th percentile | 344 | 3,147.53 | 2,921.76 |

Actual revenue for the evaluated existing-customer population is **£2,662,060.94**. The model predicts **£1,731,074.59** in aggregate, versus **£1,846,859.12** for the baseline. Both underpredict this population's actual outcome total. These totals are not whole-store forecasts including newly acquired customers.

Ranking the highest predicted 20% selects **688 customers**, who generate **£1,775,725.27**, or **66.70%** of actual outcome revenue. Ranking by historical spending captures **£1,795,053.24**, or **67.43%**. Thus the selected model does not improve this saved VIP-ranking diagnostic either. No campaign was run and no uplift was measured.

### Figure 4: Held-out predictions and residuals — cell 15

**Actual-versus-predicted panel:** both axes use `log(1 + GBP)` and a diagonal denotes perfect prediction. The vertical column at actual zero shows nonzero forecasts for customers who did not return. For many larger actual values, points lie below the diagonal, consistent with underprediction of high spenders. Other points above it represent overpredictions. The plot is descriptive; log scaling does not change GBP metrics.

**Residual histogram:** residuals are `predicted − actual`, so negative values mean underprediction. A dense center coexists with a long negative tail, reaching errors around tens of thousands of pounds. The saved largest-error table includes customer `17450.0`, actual **£108,089.25**, predicted **£22,172.96**, absolute error **£85,916.29**. Large buyers explain why RMSE is much higher than MAE and why average performance alone is insufficient.

Seasonal variation and highly concentrated wholesale spending are plausible contributors to these errors, supported by EDA, but this notebook does not isolate or prove a single cause.

## Step 8 — Verify the transformed-data prediction path

The notebook reads the existing processed Retail manifest and four canonical tables under `data_prep/processed/online-retail-ii-uci/`. It does not use them for model training or change their contents.

It checks the raw CSV SHA-256 against the manifest, row counts, unique IDs, `ret:` source prefixes, and many-to-one customer/order/product relationships. Decimal line revenue must equal processed `sales_value`; source-line dates must match order dates. Joined lines must not disappear.

Processed source rows total **824,364**. Applying the same model preparation excludes **19,606 distinct rows**, leaving **804,758 purchases**, matching the raw path. Processed reason counts include 1,423 accounting/manual/test flags, 18,744 cancellations/negative-quantity flags, and 71 nonpositive-price flags; these overlap.

For all three training cutoffs, validation, test, and the latest historical scoring cutoff, the checks pass for:

- Customer-feature frames using `rtol=1e-10`, `atol=1e-8`.
- Selected evaluated-model predictions using the same tolerance.
- Historical future targets wherever full outcomes exist.

These checks demonstrate compatibility despite remapped IDs and split tables. They do not verify database import, an implemented backend endpoint, or dashboard rendering. Any compatibility assertion failure would stop the notebook before the new artifact is saved.

## Step 9 — Refit, save and reload the artifact

After evaluation, the notebook refits the selected configuration on training, validation and test labelled snapshots together: **18,750 customer snapshots**. The saved test metrics belong to the earlier train-only fitted configuration, not an independent evaluation of this final all-labelled-data refit.

`clv_model.pkl` is a dictionary containing:

| Key | Contents |
|---|---|
| `pipeline` | Final fitted imputer and Random Forest |
| `features` | Ordered list of the eleven retained inputs |
| `metadata` | Feature definitions, history/horizon, currency, exclusions, eligibility, cutoffs, checksum, versions, metrics and limitations |
| `prediction_policy` | Apply `maximum(0, prediction)` after calling the pipeline |

The saved status is **`experimental_baseline_not_beaten`**, because baseline superiority must hold on both validation and test to receive integration-candidate status. An experimental artifact is still saved for inspection; it must not be activated automatically.

Saved size is approximately **36.2 MB**. Reloaded predictions match the refitted in-memory pipeline on 20 sample rows using `rtol=1e-12`, `atol=1e-8 GBP`. This tolerance permits harmless parallel floating-point summation differences; it does not allow material revenue discrepancies.

## Step 10 — Score at the latest usable historical date

Scoring uses cutoff **2011-12-09**, with history before that date, without needing unavailable future outcomes. The result includes all **5,942 identified raw customers**:

| Status | Customers |
|---|---:|
| Scored with the experimental artifact | 4,028 |
| Insufficient purchase history | 1,914 |

Eligibility means at least one valid purchase inside the 288-day history. A customer absent from that window is marked unavailable rather than silently dropped or given a fabricated zero prediction.

Saved sample predictions include customer `17450.0` at **£62,897.90**, `14646.0` at **£61,820.27**, and `18102.0` at **£61,591.18**. These are latest historical estimates from the refitted artifact, not test errors, verified future outcomes, or predictions for today's date. Feature and prediction CSVs are not written.

## Limitations, review findings and remaining integration work

1. **Baseline comparison fails on test.** The trained model is experimental despite better validation MAE. Do not describe notebook completion or saving as production approval.
2. **Development exposure matters.** Notebook review notes disclose an earlier broader positive-line run whose test results were seen before accounting exclusions were corrected. Final metrics are transparent development evidence, not a pristine external confirmation. Future model improvements need a new untouched period or dataset for stronger confirmation.
3. **Few seasonal windows.** Three training snapshots and one validation/test window limit seasonal generalization. The 288-day history is coverage-driven, and recent-spend/seasonal alternatives are not evaluated in this version.
4. **Target and eligibility limits.** Revenue excludes returns and selected adjustments, includes retained positive charges, and covers existing customers with recent observable purchases. It does not cover cold-start customers, profit, or whole-store acquisition revenue.
5. **Unevaluated feature families.** Country and description text were not tested; the notes do not claim the data proved them irrelevant. Correlated positive permutation scores do not establish global feature optimality.
6. **No uncertainty or campaign-effect model.** Point estimates have no calibrated individual interval, and high predicted value does not demonstrate response to a discount or VIP intervention.
7. **Backend parity is a requirement, not installed integration.** The application must reproduce the exact preparation, invoice aggregation, source/date rules, feature definitions, input order and nonnegative prediction policy. Missing history must remain unavailable.
8. **Storage and environment.** A GBP amount must not be inserted into probability-style `customer_segments.score`. Store monetary predictions appropriately, preserve observation date/horizon, and use the recorded compatible dependencies. Backend endpoints, database records and dashboards were not implemented by the notebook.
9. **Historical ignore-rule note.** The notebook's final markdown still describes the older churn-only model exception. The repository ignore rule was later changed to stop ignoring `backend/ml_models/`; that remark is historical and is not a training or prediction requirement.

The notebook also records successful read-only synthetic checks for invoice totals, half-open boundaries, exclusion of future inputs, zero-spend labels, missing versus zero interval variation, and invalid/accounting/manual exclusions. Those checks were performed during implementation and summarized in notebook markdown; they are not an additional executable test cell in the current notebook.

## Related project references

- [Churn training implementation](churn-risk-model-training-and-results.md).
- [CLV source description](../../notes/customer%20segmentation-notes/segments/Future%20High-Value%20-%20CLV.md).
- [Segment/schema mapping](../../notes/customer%20segmentation-notes/segments-schema-mapping.md).
- [KPI requirements](../../notes/kpi-notes/kpi-schema-mapping.md).
- [Transformation contract](../../notes/data_transformation_notes/transformation-contract.md).
- [Retail transformation code](../../../data_prep/transform/retail.py).

Recorded on 2026-11-13 from current notebook code, saved outputs, and direct visual inspection of embedded plots. No notebook, dataset, model, or existing documentation was changed to produce these notes.
