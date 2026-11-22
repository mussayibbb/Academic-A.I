from datetime import date, timedelta

import pandas as pd
import pytest

from ai.risk.evaluate_early_warning import evaluate_time_split
from ai.risk.features import FEATURES


def _time_series_data(rows=100):
    records = []
    for index in range(rows):
        at_risk = index % 2 == 0
        record = {name: float(index % 10) for name in FEATURES}
        record['attendance'] = 45.0 if at_risk else 92.0
        record['risk_label'] = 'HIGH' if at_risk else 'LOW'
        record['observed_at'] = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        records.append(record)
    return pd.DataFrame(records)


def test_time_split_evaluation_reports_metrics_and_writes_model_card(tmp_path):
    dataset_path = tmp_path / 'dated.csv'
    model_card_path = tmp_path / 'model-card.md'
    _time_series_data().to_csv(dataset_path, index=False)

    result = evaluate_time_split(dataset_path, 'observed_at', model_card_path=model_card_path)

    assert result['split']['train_rows'] > result['split']['calibration_rows'] > 0
    assert result['split']['test_rows'] > 0
    assert set(result['metrics']) == {'roc_auc_at_risk', 'precision_at_risk', 'recall_at_risk'}
    assert result['calibration_curve']
    assert model_card_path.exists()
    assert 'Platt scaling' in model_card_path.read_text(encoding='utf-8')


def test_time_split_evaluation_requires_a_real_date_column(tmp_path):
    dataset_path = tmp_path / 'undated.csv'
    _time_series_data().drop(columns='observed_at').to_csv(dataset_path, index=False)

    with pytest.raises(ValueError, match='columns that are missing: observed_at'):
        evaluate_time_split(dataset_path, 'observed_at', model_card_path=tmp_path / 'model-card.md')
