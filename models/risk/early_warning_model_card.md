# Early Warning Model Card

## Status

Production evaluation and calibration are blocked. The checked-in processed dataset has 248 rows and every row is marked `DEMO DATA`. It contains no date or period column, so a leakage-safe time split and independent at-risk outcome evaluation cannot be calculated from it. The model's existing 100% holdout accuracy/precision/recall are on generated labels and are not evidence of institutional performance.

## Target

The dataset target column is `risk_label`, with `HIGH`, `MEDIUM`, and `LOW` classes. No label changes were made. The application defines the danger-zone probability thresholds as High >= 0.75, Watch >= 0.40, and Normal < 0.40; those thresholds are not independently validated by the demo labels.

## Current score diagnostic (DEMO DATA only)

The raw, uncalibrated model's `P(HIGH)` distribution over the 248 demo rows was:

| Statistic | `P(HIGH)` |
| --- | ---: |
| Minimum | 0.000000 |
| 25th percentile | 0.000000 |
| Median | 0.000027 |
| 75th percentile | 0.000950 |
| Maximum | 0.999999 |
| Rows at or above 0.75 | 52 |
| Rows that display as 100% when rounded | 51 |

The model is a plain logistic-regression classifier that exposes uncalibrated `predict_proba` values. Extreme values are present despite the absence of the target column from the feature list. The perfect evaluation results and high-confidence scores are consistent with a small model learning the generated `risk_label` rules; the available evidence cannot establish whether a real, independent dataset would exhibit leakage or overfitting. A post-calibration distribution is intentionally not reported because calibration against these generated labels would be misleading.

## Reproducible evaluation

`python -m ai.risk.evaluate_early_warning --date-column <dated-outcome-column>` performs chronological train, calibration, and test splits, fits Platt scaling on the calibration window, and writes ROC-AUC, at-risk precision/recall, and a calibration curve here. It refuses demo data and missing/invalid dates. The evaluation remains pending until dated, independently adjudicated outcomes are supplied.

| Measure | Result |
| --- | --- |
| Time-based ROC-AUC | Not available |
| At-risk precision / recall | Not available |
| Calibration curve | Not available |
| Post-calibration score distribution | Not available |
