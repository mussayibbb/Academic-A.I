import pandas as pd
import pytest
from pathlib import Path

from ai.risk.features import FEATURE_CONTRACT, FEATURES, load_training_data
from ai.risk.predict import predict
from backend.app.services.early_warning_csv import DEFAULT_EARLY_WARNING_DATA, load_early_warning_csv


def test_loads_the_complete_demo_csv_with_the_model_feature_contract():
    students = load_early_warning_csv()

    assert len(students) == 150
    assert students[0].student_id == 'STU001'
    assert students[-1].student_id == 'STU150'
    assert all(list(student.features) == FEATURES for student in students)
    assert all(student.assignment_submission_rate is not None for student in students)


def test_rejects_non_demo_rows(tmp_path):
    frame = pd.read_csv(DEFAULT_EARLY_WARNING_DATA).head(1)
    frame.loc[frame.index[0], 'data_origin'] = 'INSTITUTIONAL IMPORT'
    path = tmp_path / 'mixed.csv'
    frame.to_csv(path, index=False)

    with pytest.raises(ValueError, match='only rows explicitly labelled DEMO DATA'):
        load_early_warning_csv(path)


def test_rejects_missing_model_features(tmp_path):
    frame = pd.read_csv(DEFAULT_EARLY_WARNING_DATA).head(1).drop(columns=['attendance'])
    path = tmp_path / 'missing-feature.csv'
    frame.to_csv(path, index=False)

    with pytest.raises(ValueError, match='attendance'):
        load_early_warning_csv(path)


def test_feature_contract_excludes_marks_gpa_and_identifiers():
    assert set(FEATURES) == {
        'attendance',
        'assignments_completed',
        'activity_per_week',
        'missing_submissions',
        'attendance_change',
        'assignment_submission_rate',
    }
    assert 'student_id' not in FEATURES
    assert all(FEATURE_CONTRACT[name]['model_input'] for name in FEATURES)
    for excluded in (
        'fa1', 'fa2', 'previous_gpa', 'current_gpa',
        'assessment', 'assessment_change', 'momentum_score',
    ):
        assert excluded not in FEATURES
        assert FEATURE_CONTRACT[excluded]['model_input'] is False


def test_rule_generated_target_has_all_three_classes():
    features, labels = load_training_data(DEFAULT_EARLY_WARNING_DATA)
    assert set(labels) == {'HIGH', 'MEDIUM', 'LOW'}
    assert len(features) == 150


@pytest.mark.parametrize('excluded_field', [
    'fa1', 'fa2', 'previous_gpa', 'current_gpa',
    'assessment', 'assessment_change', 'momentum_score',
])
def test_candidate_prediction_rejects_marks_and_gpa_fields(tmp_path, excluded_field):
    candidate = Path(__file__).resolve().parents[1] / 'models' / 'candidates' / 'risk_model_candidate.joblib'
    values = {name: 50.0 if name == 'attendance' else 10.0 if name == 'activity_per_week' else 1.0 for name in FEATURES}
    values[excluded_field] = 100.0
    with pytest.raises(ValueError, match='excluded|Marks'):
        predict(values, candidate)
