# Churn-Risk Prediction — Training Implementation and Results

## Description and evidence

This document records the actual workflow in [churn_prediction.ipynb](../../../data_prep/training/churn_prediction.ipynb), including its saved text outputs and six saved chart figures. It describes what the notebook currently implements, rather than a proposed improved pipeline. The notebook and model were not modified or retrained when these notes were written.

The trained artifact is `backend/ml_models/churn_model.pkl`. Training is complete as a notebook experiment; backend prediction endpoints, dashboard integration, and production readiness are not established by these results.

## Goal and project role

Predict the source dataset's binary `Churn` label: `0` for non-churn and `1` for churn. The intended product use is identifying customers for retention and win-back actions. The notebook predicts a supplied snapshot label; it does not define a future time horizon or establish when a customer will leave.

Training uses the raw E-commerce Customer Churn dataset, not canonical processed platform exports. Its customer attributes are already summarized in the source, so this workflow primarily selects and prepares existing features rather than deriving transaction-level features.

## Strategy and technology stack

The implemented sequence is raw-data inspection, visual EDA, column selection, median imputation, duplicate removal, scaling, stratified splitting, two-model comparison, evaluation, and serialization of the selected classifier.

| Technology | Actual use |
|---|---|
| Python and Jupyter | Notebook workflow and saved results |
| pandas and NumPy | CSV inspection, column selection, numeric preparation |
| Matplotlib and Seaborn | Distribution plot, heatmaps, boxplots, ROC curves, importance chart |
| scikit-learn | Imputation, scaling, splitting, Random Forest, Logistic Regression, metrics |
| joblib and os | Create the model directory if needed and serialize the classifier |

The notebook imports `LabelEncoder` and contains categorical-imputation logic. However, the categorical columns are dropped before this logic, and the saved run's final inputs are all numeric. These generic branches should not be described as substantive categorical feature engineering in this experiment. Warnings are globally suppressed. Package versions are not recorded in the saved artifact by this notebook.

## Step 1 — Load and inspect the raw data

Input: `data_prep/raw/e-commerce-customer-churn/data_ecommerce_customer_churn.csv`. The notebook uses a hard-coded `d:/CDP -FYP/` path.

The saved inspection reports **3,941 rows and 11 columns**:

`Tenure`, `WarehouseToHome`, `NumberOfDeviceRegistered`, `PreferedOrderCat`, `SatisfactionScore`, `MaritalStatus`, `NumberOfAddress`, `Complain`, `DaySinceLastOrder`, `CashbackAmount`, and `Churn`.

The initial checks use `head()`, `info()`, `describe(include='all')`, missing-value counts, exact duplicate counts, and label proportions.

| Initial observation | Saved result |
|---|---:|
| Missing `Tenure` | 194 |
| Missing `WarehouseToHome` | 169 |
| Missing `DaySinceLastOrder` | 213 |
| Missing values in other columns | 0 |
| Exact duplicate rows beyond the first occurrence | 671 |
| Non-churn proportion | 82.90% |
| Churn proportion | 17.10% |

These are initial raw-data values, not necessarily the final training-population proportions after cleaning.

## Step 2 — EDA and interpretation of every saved visualization

### Figure 1: Churn Distribution — notebook cell 9

The count plot has a much taller bar for class 0 than class 1, confirming the approximately 83%/17% imbalance. Accuracy alone can hide poor identification of churners in this setting. Both fitted classifiers use `class_weight='balanced'`, and the notebook also reports class-specific precision, recall, F1, and ROC-AUC. The chart does not demonstrate that class weighting is optimal; there is no unweighted-model comparison.

### Figure 2: Feature Correlation Heatmap — cell 10

The numeric correlation heatmap includes the label for exploratory analysis. Rounded values visible in the saved chart include:

| Pair | Correlation shown |
|---|---:|
| Tenure and Churn | -0.36 |
| Complain and Churn | 0.26 |
| CashbackAmount and Churn | -0.16 |
| DaySinceLastOrder and Churn | -0.16 |
| NumberOfDeviceRegistered and Churn | 0.11 |
| SatisfactionScore and Churn | 0.11 |
| WarehouseToHome and Churn | 0.07 |
| NumberOfAddress and Churn | 0.04 |
| Tenure and CashbackAmount | 0.46 |

Longer source tenure is associated with lower churn, and complaints with higher churn. Most individual linear relationships are modest. This is descriptive association, not causation or proof that a feature is unnecessary. The source label is included in this plot but is excluded from the fitted input matrix.

### Figure 3: Eight feature-by-churn boxplots — cell 11

Each subplot compares the raw distributions for classes 0 and 1:

| Subplot | What the saved chart shows |
|---|---|
| Tenure | Churners have markedly shorter typical tenure; the non-churn median is around 10 source units, while the churn median is around 1. Both groups contain outliers. |
| WarehouseToHome | Churners have a somewhat higher median distance, but the distributions overlap substantially. A large non-churn outlier is visible near 127. |
| NumberOfDeviceRegistered | Churners tend toward a higher device count, with considerable overlap. A larger count is not automatically protective in this dataset. |
| SatisfactionScore | The churn group is shifted toward higher recorded scores, contrary to a simple expectation that higher satisfaction always means lower churn. The recorded association needs source/label interpretation; the chart must not be rewritten to fit that expectation. |
| DaySinceLastOrder | Typical recorded values are lower for churners, with outliers in both groups. This does not independently prove target leakage or explain the dataset's label definition. |
| CashbackAmount | Non-churners have a higher median cashback value; the groups overlap, with high-value outliers. |
| Complain | The binary complaint distribution differs visibly: the non-churn box is concentrated at 0, whereas churners have more complaint-positive values. A binary-variable boxplot does not directly provide conditional churn probabilities. |
| NumberOfAddress | Similar central distributions and considerable overlap are visible. This alone is insufficient to prove the column has no predictive value. |

These plots support inspecting feature relationships and extremes. The notebook does not implement automatic outlier removal or a formal feature-ablation experiment based on the plots.

## Step 3 — Column selection and preprocessing

The notebook executes the following operations in this order:

1. Drop `MaritalStatus`, `NumberOfAddress`, and `PreferedOrderCat`. It would also drop `CustomerID` if present; that field is absent from this raw file.
2. Fit `SimpleImputer(strategy='median')` on numeric features, excluding the target, and replace missing numeric values. The saved output reports **zero missing values** afterwards.
3. Remove exact duplicates from the reduced, imputed frame. The saved output reports **926 removed rows**, leaving **3,015**. This differs from the initial 671 because the frame and its values have changed before this second duplicate check.
4. Execute categorical encoding if any categorical columns remain; none are used in the final saved model.
5. Drop `DaySinceLastOrder` before constructing the model inputs.
6. Fit `StandardScaler` to all remaining numeric input columns and transform them. Do not scale `Churn`.

The final six inputs, in notebook order, are:

| Position | Input |
|---:|---|
| 1 | `Tenure` |
| 2 | `WarehouseToHome` |
| 3 | `NumberOfDeviceRegistered` |
| 4 | `SatisfactionScore` |
| 5 | `Complain` |
| 6 | `CashbackAmount` |

The column exclusions are explicit code choices. The saved notebook does not establish their optimality through validation-based feature selection. Project notes raise leakage concerns about `DaySinceLastOrder`, but the notebook does not empirically establish the source label definition or demonstrate leakage. Its exclusion is observable; a proven causal rationale is not.

## Step 4 — Split the data

The notebook uses `train_test_split(test_size=0.2, random_state=42, stratify=y)` after preprocessing.

| Partition | Rows | Input columns |
|---|---:|---:|
| Training | 2,412 | 6 |
| Test/comparison | 603 | 6 |

Both printed churn rates round to 0.16. The evaluated partition contains **505 non-churners and 98 churners**. This is a random stratified snapshot split, not a chronological evaluation. No separate validation set is created.

## Step 5 — Fit and select the classifiers

| Candidate | Explicit configuration |
|---|---|
| Random Forest classifier | `n_estimators=100`, `random_state=42`, `class_weight='balanced'` |
| Logistic Regression | `random_state=42`, `class_weight='balanced'`, `max_iter=1000` |

Both models receive the same scaled six-column matrix. Predictions use `predict()`; positive-class scores use `predict_proba(X_test)[:, 1]`. The notebook selects the classifier with the larger ROC-AUC on the same test/comparison partition, selecting Random Forest on a tie.

No threshold search, hyperparameter search, probability calibration, cross-validation, or final refitting on all rows is implemented.

## Step 6 — Saved evaluation results

The following values reproduce the notebook's rounded classification reports:

| Model | Accuracy | Churn precision | Churn recall | Churn F1 | ROC-AUC |
|---|---:|---:|---:|---:|---:|
| Random Forest | 0.88 | 0.61 | 0.68 | 0.65 | 0.9050 |
| Logistic Regression | 0.75 | 0.37 | 0.77 | 0.50 | 0.8468 |

| Model | Non-churn precision | Non-churn recall | Non-churn F1 | Macro F1 | Weighted F1 |
|---|---:|---:|---:|---:|---:|
| Random Forest | 0.94 | 0.92 | 0.93 | 0.79 | 0.88 |
| Logistic Regression | 0.94 | 0.75 | 0.84 | 0.67 | 0.78 |

Random Forest provides better ROC-AUC, accuracy, and churn precision/F1 here. Logistic Regression finds a larger share of churners, at the cost of more false positives. Model choice therefore reflects the implemented ROC-AUC criterion, not dominance on every business metric.

### Figure 4: Random Forest Confusion Matrix — cell 23

Rows are actual labels and columns are predicted labels:

| Actual / Predicted | Non-churn: 0 | Churn: 1 |
|---|---:|---:|
| Non-churn: 0 | 463 | 42 |
| Churn: 1 | 31 | 67 |

The classifier correctly identifies 67 of 98 churners and misses 31. It flags 42 non-churners as churners. Those counts explain the rounded churn recall of 0.68 and precision of 0.61. In a campaign, false positives could mean unnecessary outreach, while false negatives represent missed retention opportunities. The notebook does not quantify campaign cost or benefit.

### Figure 5: ROC Curve — cell 24

The Random Forest curve generally lies above Logistic Regression, and both lie above the random-ranking diagonal. The legend rounds AUC to approximately 0.91 and 0.85, while the text outputs retain 0.9050 and 0.8468. This supports Random Forest's stronger ranking performance on this partition. ROC-AUC is not the percentage of correctly classified customers and does not establish calibrated probabilities or an optimal campaign threshold.

### Figure 6: Random Forest Feature Importances — cell 25

The title says “Top 10,” but only six model features exist, so all six are shown. These are the forest's impurity-based importances, not validation permutation scores.

| Feature | Saved importance |
|---|---:|
| Tenure | 0.365779 |
| CashbackAmount | 0.268275 |
| WarehouseToHome | 0.143722 |
| Complain | 0.079462 |
| SatisfactionScore | 0.079430 |
| NumberOfDeviceRegistered | 0.063332 |

Tenure and cashback dominate this fitted forest's split-based ranking. Importance does not show whether increasing a feature increases or decreases churn, does not imply causation, and can be affected by correlated features and available split points. The notebook does not remove features using this chart.

## Step 7 — Save the selected model

The saved output states: **Selected model: Random Forest (ROC-AUC = 0.9050)**. The final cell creates `backend/ml_models/` if necessary and executes `joblib.dump(best_model, .../churn_model.pkl)`.

Only the trained classifier is serialized. The fitted median imputer and scaler, feature-contract metadata, and library versions are not bundled. There is no saved reload/prediction-equivalence check in this notebook. The saved classifier is the one fitted on the training partition, not a later all-data refit.

## Limitations and application implications

1. **Preprocessing leakage:** the imputer and scaler are fitted before the train/test split. Their statistics use the evaluated rows as well as the training rows. A future revision should fit them within a training-only pipeline; the present metrics should retain this qualification.
2. **Selection uses the test partition:** both candidates are compared and the winner chosen on the same partition. There is no untouched final test set after selection.
3. **Feature selection is not validated:** dropped columns are not tested through controlled feature comparisons. Removing `DaySinceLastOrder` happens after duplicate removal, and the notebook does not check whether the final six-feature representation has additional repeated profiles across partitions.
4. **Artifact lacks preprocessing:** production inference must reproduce the learned transformations. Refitting a scaler on each incoming batch would change the input interpretation. The current bare classifier alone is insufficient to guarantee consistent raw-input predictions.
5. **Snapshot semantics:** the source's tenure unit and label observation date are not verified by this notebook. Do not invent transaction histories, date conversions, or a forecast horizon from these attributes.
6. **Probability and campaign decisions:** class weighting and ROC-AUC do not establish calibration. No business threshold, intervention uplift, or cost-sensitive campaign policy was evaluated.
7. **Integration remains separate:** this notebook does not prove processed-churn feature parity, write predictions into platform tables, expose an API, or display results on localhost dashboards.

## Related implementation and project references

- [CLV training implementation](customer-lifetime-value-model-training-and-results.md) — separate regression workflow with chronological evaluation and compatibility checks.
- [Churn segment source description](../../notes/customer%20segmentation-notes/segments/Churn-Risk.md).
- [Segment/schema mapping](../../notes/customer%20segmentation-notes/segments-schema-mapping.md).
- [Transformation contract](../../notes/data_transformation_notes/transformation-contract.md) — source identity and snapshot limitations.

Recorded on 2026-11-13 from current notebook code, saved outputs, and direct visual inspection of embedded plots. No training was rerun to create this document.
