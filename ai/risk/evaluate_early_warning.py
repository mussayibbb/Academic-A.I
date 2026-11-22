import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline

from ai.risk.features import FEATURES, TARGET

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / 'data' / 'processed' / 'DSstudents_processed.csv'
DEFAULT_MODEL_CARD = ROOT / 'models' / 'risk' / 'early_warning_model_card.md'


def _fit_platt_scaler(raw_probability: np.ndarray, labels: np.ndarray) -> LogisticRegression:
    clipped = np.clip(raw_probability, 1e-6, 1 - 1e-6)
    logits = np.log(clipped / (1 - clipped)).reshape(-1, 1)
    scaler = LogisticRegression(max_iter=1000)
    scaler.fit(logits, labels)
    return scaler


def evaluate_time_split(
    input_path: Path,
    date_column: str,
    *,
    model_card_path: Path = DEFAULT_MODEL_CARD,
    positive_label: str = 'HIGH',
    danger_threshold: float = 0.75,
) -> dict[str, Any]:
    data = pd.read_csv(input_path)
    required = set(FEATURES + [TARGET, date_column])
    missing = sorted(required - set(data.columns))
    if missing:
        raise ValueError(f'Time-based evaluation requires columns that are missing: {", ".join(missing)}')
    if data.get('data_origin', pd.Series(dtype=str)).eq('DEMO DATA').any():
        raise ValueError('Refusing to evaluate DEMO DATA as institutional risk outcomes.')

    data = data.copy()
    data[date_column] = pd.to_datetime(data[date_column], errors='coerce', utc=True)
    if data[date_column].isna().any():
        raise ValueError(f'Column {date_column!r} contains missing or invalid timestamps.')
    data[TARGET] = data[TARGET].astype(str).str.upper()
    positive_label = positive_label.upper()
    if positive_label not in set(data[TARGET]):
        raise ValueError(f'Positive class {positive_label!r} does not occur in {TARGET!r}.')

    unique_dates = sorted(data[date_column].unique())
    if len(unique_dates) < 10:
        raise ValueError('Time-based evaluation needs at least 10 distinct dates for train, calibration, and test periods.')
    train_end = unique_dates[max(1, int(len(unique_dates) * 0.70))]
    calibration_end = unique_dates[max(2, int(len(unique_dates) * 0.85))]
    train = data[data[date_column] < train_end]
    calibration = data[(data[date_column] >= train_end) & (data[date_column] < calibration_end)]
    test = data[data[date_column] >= calibration_end]
    if min(len(train), len(calibration), len(test)) == 0:
        raise ValueError('Time split produced an empty train, calibration, or test period.')

    feature_frame = data[FEATURES].apply(pd.to_numeric, errors='coerce')
    x_train = feature_frame.loc[train.index]
    x_calibration = feature_frame.loc[calibration.index]
    x_test = feature_frame.loc[test.index]
    train_positive = train[TARGET].eq(positive_label).astype(int).to_numpy()
    calibration_positive = calibration[TARGET].eq(positive_label).astype(int).to_numpy()
    test_positive = test[TARGET].eq(positive_label).astype(int).to_numpy()
    if len(np.unique(train_positive)) < 2 or len(np.unique(calibration_positive)) < 2:
        raise ValueError('Train and calibration periods must each contain both at-risk and other-class outcomes.')
    if len(np.unique(test_positive)) < 2:
        raise ValueError('Test period must contain both at-risk and other-class outcomes for ROC-AUC.')

    model = Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('classifier', LogisticRegression(max_iter=2000, class_weight='balanced', random_state=42)),
    ])
    model.fit(x_train, train[TARGET])
    classes = list(model.named_steps['classifier'].classes_)
    positive_index = classes.index(positive_label)
    raw_calibration_probability = model.predict_proba(x_calibration)[:, positive_index]
    calibrator = _fit_platt_scaler(raw_calibration_probability, calibration_positive)
    raw_test_probability = model.predict_proba(x_test)[:, positive_index]
    test_logits = np.log(
        np.clip(raw_test_probability, 1e-6, 1 - 1e-6)
        / (1 - np.clip(raw_test_probability, 1e-6, 1 - 1e-6))
    ).reshape(-1, 1)
    calibrated_probability = calibrator.predict_proba(test_logits)[:, 1]
    predicted_at_risk = calibrated_probability >= danger_threshold
    auc = float(roc_auc_score(test_positive, calibrated_probability))
    precision = float(precision_score(test_positive, predicted_at_risk, zero_division=0))
    recall = float(recall_score(test_positive, predicted_at_risk, zero_division=0))
    observed, predicted = calibration_curve(
        test_positive,
        calibrated_probability,
        n_bins=min(10, max(2, len(test) // 5)),
        strategy='quantile',
    )
    result: dict[str, Any] = {
        'dataset': input_path.name,
        'target_column': TARGET,
        'positive_class': positive_label,
        'date_column': date_column,
        'calibration_method': 'Platt scaling fitted on a later calibration period',
        'danger_threshold': danger_threshold,
        'split': {
            'train_rows': int(len(train)),
            'calibration_rows': int(len(calibration)),
            'test_rows': int(len(test)),
            'train_before': pd.Timestamp(train_end).isoformat(),
            'calibration_before': pd.Timestamp(calibration_end).isoformat(),
            'test_from': pd.Timestamp(calibration_end).isoformat(),
        },
        'metrics': {
            'roc_auc_at_risk': auc,
            'precision_at_risk': precision,
            'recall_at_risk': recall,
        },
        'calibration_curve': [
            {
                'mean_predicted_probability': float(mean_probability),
                'observed_at_risk_fraction': float(observed_fraction),
            }
            for mean_probability, observed_fraction in zip(predicted, observed)
        ],
    }
    model_card_path.parent.mkdir(parents=True, exist_ok=True)
    model_card_path.write_text(
        '# Early Warning Model Card\n\n'
        'Evaluation uses chronological train, calibration, and held-out test periods. '
        'Platt scaling is fitted only on the calibration period. At-risk precision and recall '
        f'use the configured {danger_threshold:.0%} threshold.\n\n'
        '```json\n'
        f'{json.dumps(result, indent=2)}\n'
        '```\n',
        encoding='utf-8',
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description='Evaluate calibrated early-warning risk on a time-based split.')
    parser.add_argument('--input', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--date-column', required=True, help='Dataset column with point-in-time observation dates.')
    parser.add_argument('--model-card', type=Path, default=DEFAULT_MODEL_CARD)
    parser.add_argument('--positive-label', default='HIGH')
    parser.add_argument('--danger-threshold', type=float, default=0.75)
    args = parser.parse_args()
    result = evaluate_time_split(
        args.input,
        args.date_column,
        model_card_path=args.model_card,
        positive_label=args.positive_label,
        danger_threshold=args.danger_threshold,
    )
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
