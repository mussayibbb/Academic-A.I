from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import and_, or_

from ai.risk.features import FEATURES
from ai.risk.predict import DEFAULT_ARTIFACT, load_artifact, predict_many
from backend.app.early_warning_config import priority_for_probability
from backend.app.main import SIMULATION_REQUESTS, app
from backend.app.models import Course, RiskSnapshot, Student, Teacher, User
from backend.app.main import (
    predict_with_active_model,
    predict_with_demo_preview,
    selected_model_artifact,
    student_features,
    student_risk_views,
)


def _teacher_scope():
    with app.state.test_session_factory() as db:
        user = db.query(User).filter(User.email == 'teacher@example.com').one()
        teacher = Teacher(user_id=user.id, department='Science')
        db.add(teacher)
        db.flush()
        course = Course(name='Biology', code='BIO-EW', section='A', teacher_id=teacher.id)
        db.add(course)
        student = db.query(Student).filter(Student.student_id == 'T-001').one()
        student.course_id = course.id
        db.commit()
        student_id = student.id
        other_id = db.query(Student).filter(Student.student_id == 'T-002').one().id
    return student_id, other_id


def _mock_model(monkeypatch):
    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: False)
    features = {
        'attendance': 60.0,
        'assessment': 50.0,
        'previous_gpa': 2.0,
        'current_gpa': 1.8,
        'assignments_completed': 2.0,
        'activity_per_week': 1.0,
        'missing_submissions': 2.0,
        'attendance_change': -10.0,
        'assessment_change': -5.0,
        'momentum_score': -1.0,
    }

    def predict(values, _db):
        high = max(0.0, min(1.0, 0.8 - (values['attendance'] - 60.0) / 125.0))
        return {
            'risk_level': 'HIGH' if high >= 0.5 else 'LOW',
            'risk_probability': high if high >= 0.5 else 1 - high,
            'class_probabilities': {'HIGH': high, 'MEDIUM': 0.1, 'LOW': 0.9 - high},
            'model_version': 'test-model',
        }

    monkeypatch.setattr('backend.app.main.student_features', lambda _student, _db: features.copy())
    monkeypatch.setattr('backend.app.main.predict_with_active_model', predict)
    return features


def _create_dataset_scope(monkeypatch):
    _, other_id = _teacher_scope()
    dataset_path = Path(__file__).resolve().parents[1] / 'data' / 'processed' / 'DSstudents_processed.csv'
    dataset_features = pd.read_csv(dataset_path)[FEATURES].to_dict(orient='records')
    features_by_student: dict[int, dict[str, float]] = {}
    names_by_student: dict[int, str] = {}
    expected_by_student: dict[int, dict] = {}

    with app.state.test_session_factory() as db:
        teacher = db.query(Teacher).join(User).filter(User.email == 'teacher@example.com').one()
        course = teacher.courses[0]
        assigned_filter = or_(
            Student.course_id == course.id,
            and_(Student.course_id.is_(None), Student.section == course.section),
        )
        existing_students = db.query(Student).filter(assigned_filter).count()
        for index in range(len(dataset_features) - existing_students):
            db.add(Student(
                student_id=f'EW-{index:03d}',
                full_name=f'Dataset Student {index:03d}',
                section=course.section,
                department='Science',
                course_id=course.id,
                attendance=80,
                assignments_completed=4,
                data_origin='DEMO DATA',
            ))
        db.flush()

        students = db.query(Student).filter(assigned_filter).order_by(Student.full_name, Student.id).all()
        assert len(students) == len(dataset_features)
        for student, row in zip(students, dataset_features):
            features_by_student[student.id] = {name: float(row[name]) for name in FEATURES}
            names_by_student[student.id] = student.full_name

        artifact_path = Path(__file__).resolve().parents[1] / 'models' / 'candidates' / 'risk_model_candidate.joblib'
        predictions = predict_many(
            [features_by_student[student.id] for student in students],
            artifact_path,
        )
        expected_by_student = {
            student.id: prediction for student, prediction in zip(students, predictions)
        }
        db.commit()

    def dataset_feature_batch(students, _db):
        missing_ids = [student.id for student in students if student.id not in features_by_student]
        assert not missing_ids, f'Missing dataset features for student ids: {missing_ids}'
        return (
            {student.id: features_by_student[student.id] for student in students},
            {student.id: {} for student in students},
        )

    monkeypatch.setattr('backend.app.main.student_feature_batch', dataset_feature_batch)
    monkeypatch.setattr('backend.app.main.explain_with_active_model', lambda *_args, **_kwargs: {
        'contributions': {},
    })
    monkeypatch.setattr('backend.app.main.explain_with_demo_preview', lambda *_args, **_kwargs: {
        'contributions': {},
    })
    return other_id, names_by_student, expected_by_student


def test_early_warnings_returns_all_150_model_predictions_with_filters_and_pagination(
    client, teacher_token, student_token, monkeypatch,
):
    other_id, names_by_student, expected_by_student = _create_dataset_scope(monkeypatch)
    headers = {'Authorization': f'Bearer {teacher_token}'}
    priority_by_student = {
        student_id: priority_for_probability(float(prediction['class_probabilities']['HIGH']))
        for student_id, prediction in expected_by_student.items()
    }
    expected_counts = Counter(priority_by_student.values())
    priority_names = ('High', 'Medium', 'Low')
    actual_student_risk_views = student_risk_views
    cached_result = {}

    def cached_student_risk_views(students, db):
        if not cached_result:
            views, features, momentum = actual_student_risk_views(students, db)
            cached_result.update({
                'views': {student.id: view for student, view in zip(students, views)},
                'features': features,
                'momentum': momentum,
            })
        return (
            [(student, cached_result['views'][student.id][1]) for student in students],
            {student.id: cached_result['features'][student.id] for student in students},
            {student.id: cached_result['momentum'][student.id] for student in students},
        )

    monkeypatch.setattr('backend.app.main.student_risk_views', cached_student_risk_views)

    first_page = client.get('/api/v1/early-warnings', params={'page': 1, 'pageSize': 50}, headers=headers)
    assert first_page.status_code == 200, first_page.text
    data = first_page.json()
    assert data['dataStatus'] == 'unvalidated-demo-model'
    assert 'DEMO DATA' in data['message']
    assert 'unvalidated' in data['message']
    assert data['total'] == 150
    assert len(data['items']) == 50
    assert data['nextPage'] == 2
    assert data['summary']['signals'] == 150
    assert data['summary']['highPriority'] == expected_counts['High']
    assert data['summary']['watchList'] == expected_counts['Medium']
    for item in data['items']:
        expected = expected_by_student[item['studentId']]
        assert item['priority'] == priority_by_student[item['studentId']]
        assert item['riskProbability'] == round(expected['class_probabilities']['HIGH'], 6)

    all_items = list(data['items'])
    page_number = data['nextPage']
    while page_number is not None:
        page = client.get(
            '/api/v1/early-warnings',
            params={'page': page_number, 'pageSize': 50},
            headers=headers,
        )
        assert page.status_code == 200, page.text
        page_data = page.json()
        assert page_data['total'] == 150
        all_items.extend(page_data['items'])
        page_number = page_data['nextPage']
    assert len(all_items) == 150
    assert len({item['studentId'] for item in all_items}) == 150
    assert {item['studentId'] for item in all_items} == set(expected_by_student)
    assert other_id not in {item['studentId'] for item in all_items}

    for priority in priority_names:
        response = client.get(
            '/api/v1/early-warnings',
            params={'severity': priority, 'page': 1, 'pageSize': 50},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        category_data = response.json()
        assert category_data['total'] == expected_counts[priority]
        assert category_data['summary']['signals'] == expected_counts[priority]
        assert category_data['summary']['highPriority'] == (expected_counts['High'] if priority == 'High' else 0)
        assert category_data['summary']['watchList'] == (expected_counts['Medium'] if priority == 'Medium' else 0)
        assert category_data['summary']['lowPriority'] == (expected_counts['Low'] if priority == 'Low' else 0)
        assert all(item['priority'] == priority for item in category_data['items'])
        category_items = list(category_data['items'])
        page_number = category_data['nextPage']
        while page_number is not None:
            category_page = client.get(
                '/api/v1/early-warnings',
                params={'severity': priority, 'page': page_number, 'pageSize': 50},
                headers=headers,
            )
            assert category_page.status_code == 200, category_page.text
            category_page_data = category_page.json()
            assert category_page_data['total'] == expected_counts[priority]
            category_items.extend(category_page_data['items'])
            page_number = category_page_data['nextPage']
        assert len(category_items) == expected_counts[priority]
        assert all(item['priority'] == priority for item in category_items)

        if expected_counts[priority]:
            student_id = next(student_id for student_id, item_priority in priority_by_student.items() if item_priority == priority)
            search = client.get(
                '/api/v1/early-warnings',
                params={'q': names_by_student[student_id]},
                headers=headers,
            )
            assert search.status_code == 200, search.text
            search_data = search.json()
            assert search_data['total'] == 1
            assert search_data['items'][0]['studentId'] == student_id
            assert search_data['items'][0]['priority'] == priority

    denied = client.get(
        '/api/v1/early-warnings',
        headers={'Authorization': f'Bearer {student_token}'},
    )
    assert denied.status_code == 403


def test_early_warning_probability_and_priority_both_use_high_class_probability(
    client, teacher_token, monkeypatch,
):
    student_id, _ = _teacher_scope()
    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: False)
    features = {
        'attendance': 80.0,
        'assignments_completed': 1.0,
        'activity_per_week': 1.0,
        'missing_submissions': 0.0,
        'attendance_change': 0.0,
        'assignment_submission_rate': 1.0,
    }
    prediction = {
        'risk_level': 'LOW',
        'risk_probability': 0.99,
        'class_probabilities': {'HIGH': 0.01, 'MEDIUM': 0.0, 'LOW': 0.99},
    }
    monkeypatch.setattr('backend.app.main.student_risk_views', lambda students, _db: (
        [(student, prediction) for student in students],
        {student.id: features for student in students},
        {student.id: {} for student in students},
    ))
    monkeypatch.setattr('backend.app.main.explain_with_active_model', lambda *_args, **_kwargs: {
        'contributions': {},
    })

    response = client.get(
        '/api/v1/early-warnings',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 200, response.text
    item = next(item for item in response.json()['items'] if item['studentId'] == student_id)
    assert item['riskProbability'] == 0.01
    assert item['priority'] == 'Low'
    assert item['riskClass'] == 'Normal'


@pytest.mark.parametrize('with_course', [False, True])
def test_early_warnings_empty_scope_includes_demo_status_and_zero_total(
    client, teacher_token, with_course,
):
    with app.state.test_session_factory() as db:
        user = db.query(User).filter(User.email == 'teacher@example.com').one()
        teacher = Teacher(user_id=user.id, department='Science')
        db.add(teacher)
        db.flush()
        if with_course:
            db.add(Course(name='Empty', code='EMPTY-EW', section='Z', teacher_id=teacher.id))
        db.commit()

    response = client.get(
        '/api/v1/early-warnings',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data['items'] == []
    assert data['total'] == 0
    assert data['nextPage'] is None
    assert data['summary']['signals'] == 0
    assert data['dataStatus'] == 'unvalidated-demo-model'
    assert 'DEMO DATA' in data['message']


def test_simulate_uses_recovery_fraction_and_returns_delta(client, teacher_token, monkeypatch):
    student_id, _ = _teacher_scope()
    _mock_model(monkeypatch)
    SIMULATION_REQUESTS.clear()

    response = client.post(
        f'/api/v1/early-warnings/{student_id}/simulate',
        headers={'Authorization': f'Bearer {teacher_token}'},
        json={'recoveries': {'attendance': 0.5}},
    )

    assert response.status_code == 200
    data = response.json()
    assert data['riskProbability'] == 0.7
    assert data['riskClass'] == 'Watch'
    assert data['delta'] == 10.0
    assert data['before']['riskProbability'] == 0.8
    assert data['after']['riskProbability'] == 0.7


def test_simulate_rejects_out_of_range_recovery(client, teacher_token, monkeypatch):
    student_id, _ = _teacher_scope()
    _mock_model(monkeypatch)
    SIMULATION_REQUESTS.clear()

    response = client.post(
        f'/api/v1/early-warnings/{student_id}/simulate',
        headers={'Authorization': f'Bearer {teacher_token}'},
        json={'recoveries': {'attendance': 1.01}},
    )

    assert response.status_code == 422


def test_simulate_rejects_student_outside_teacher_scope(client, teacher_token, monkeypatch):
    _, other_id = _teacher_scope()
    _mock_model(monkeypatch)
    SIMULATION_REQUESTS.clear()

    response = client.post(
        f'/api/v1/early-warnings/{other_id}/simulate',
        headers={'Authorization': f'Bearer {teacher_token}'},
        json={'recoveries': {'attendance': 0.5}},
    )

    assert response.status_code == 403


def test_early_warnings_uses_raw_decision_score_to_break_calibrated_probability_ties(
    client, teacher_token, monkeypatch,
):
    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: False)
    first_id, _ = _teacher_scope()
    with app.state.test_session_factory() as db:
        teacher = db.query(Teacher).join(User).filter(User.email == 'teacher@example.com').one()
        second = db.query(Student).filter(Student.student_id == 'T-002').one()
        second.course_id = teacher.courses[0].id
        db.commit()
        second_id = second.id

    features = {
        first_id: {
            'attendance': 60.0, 'assessment': 50.0, 'previous_gpa': 2.0, 'current_gpa': 1.8,
            'assignments_completed': 2.0, 'activity_per_week': 1.0, 'missing_submissions': 2.0,
            'attendance_change': -10.0, 'assessment_change': -5.0, 'momentum_score': -1.0,
        },
        second_id: {
            'attendance': 60.0, 'assessment': 50.0, 'previous_gpa': 2.0, 'current_gpa': 1.8,
            'assignments_completed': 2.0, 'activity_per_week': 1.0, 'missing_submissions': 2.0,
            'attendance_change': -10.0, 'assessment_change': -5.0, 'momentum_score': -1.0,
        },
    }

    def risk_views(students, _db):
        prediction_by_id = {
            first_id: {
                'risk_level': 'HIGH', 'risk_probability': 0.8,
                'class_probabilities': {'HIGH': 0.8, 'MEDIUM': 0.1, 'LOW': 0.1},
                'raw_high_decision_score': 1.1,
            },
            second_id: {
                'risk_level': 'HIGH', 'risk_probability': 0.8,
                'class_probabilities': {'HIGH': 0.8, 'MEDIUM': 0.1, 'LOW': 0.1},
                'raw_high_decision_score': 2.2,
            },
        }
        return (
            [(student, prediction_by_id[student.id]) for student in students],
            features,
            {student.id: {} for student in students},
        )

    monkeypatch.setattr('backend.app.main.student_risk_views', risk_views)
    monkeypatch.setattr('backend.app.main.explain_with_active_model', lambda *_args, **_kwargs: {
        'contributions': {'attendance': 1.0},
    })

    response = client.get(
        '/api/v1/early-warnings',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 200, response.text
    assert [item['studentId'] for item in response.json()['items']] == [second_id, first_id]


def test_summary_counts_share_list_thresholds_and_recovery_uses_probability(client, teacher_token, monkeypatch):
    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: False)
    student_id, _ = _teacher_scope()
    now = datetime.utcnow()
    with app.state.test_session_factory() as db:
        student = db.query(Student).filter(Student.id == student_id).one()
        student.attendance = 60.0
        student.assessment = 50.0
        student.previous_gpa = 2.0
        student.current_gpa = 1.8
        db.add_all([
            RiskSnapshot(
                student_id=student_id,
                model_version='early-warning:test-model',
                risk_level='LOW',
                probability=0.82,
                recorded_at=now - timedelta(days=14),
            ),
            RiskSnapshot(
                student_id=student_id,
                model_version='early-warning:test-model',
                risk_level='HIGH',
                probability=0.4,
                recorded_at=now - timedelta(days=7),
            ),
        ])
        db.commit()

    features = {
        'attendance': 60.0, 'assessment': 50.0, 'previous_gpa': 2.0, 'current_gpa': 1.8,
        'assignments_completed': 2.0, 'activity_per_week': 1.0, 'missing_submissions': 2.0,
        'attendance_change': -10.0, 'assessment_change': -5.0, 'momentum_score': -1.0,
    }
    monkeypatch.setattr('backend.app.main.student_risk_views', lambda students, _db: (
        [(student, {
            'risk_level': 'MEDIUM',
            'risk_probability': 0.6,
            'class_probabilities': {'HIGH': 0.4, 'MEDIUM': 0.4, 'LOW': 0.2},
        }) for student in students],
        {student.id: features for student in students},
        {student.id: {} for student in students},
    ))
    monkeypatch.setattr('backend.app.main.explain_with_active_model', lambda *_args, **_kwargs: {
        'contributions': {'attendance': 1.0},
    })

    response = client.get(
        '/api/v1/early-warnings',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 200, response.text
    assert response.json()['summary']['highPriority'] == 0
    assert response.json()['summary']['watchList'] == 1
    assert response.json()['summary']['recoveredThisMonth'] == 1


def test_detail_merges_factors_and_returns_plain_language_precautions(client, teacher_token, monkeypatch):
    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: False)
    student_id, _ = _teacher_scope()
    features = {
        'attendance': 71.0,
        'assessment': 48.0,
        'previous_gpa': 2.2,
        'current_gpa': 1.8,
        'assignments_completed': 4.0,
        'activity_per_week': 2.0,
        'missing_submissions': 2.0,
        'attendance_change': -13.3,
        'assessment_change': -14.2,
        'momentum_score': -0.5,
    }
    prediction = {
        'risk_level': 'HIGH',
        'risk_probability': 0.8,
        'class_probabilities': {'HIGH': 0.8, 'MEDIUM': 0.15, 'LOW': 0.05},
        'model_version': 'test-model',
    }
    monkeypatch.setattr('backend.app.main.student_features', lambda *_args: features)
    monkeypatch.setattr('backend.app.main.predict_with_active_model', lambda *_args: prediction)
    monkeypatch.setattr('backend.app.main.explain_with_active_model', lambda *_args, **_kwargs: {
        'contributions': {
            'attendance': 0.2,
            'attendance_change': 0.3,
            'assessment': 0.2,
            'assessment_change': 0.1,
            'missing_submissions': 0.2,
            'current_gpa': 0.0,
        },
    })

    response = client.get(
        f'/api/v1/early-warnings/{student_id}',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert [factor['key'] for factor in data['factors']] == [
        'attendance', 'missing_submissions',
    ]
    assert data['factors'][0]['detail'] == 'Attendance 71.0%, down 13.3% across recent observations'
    assert data['factors'][1]['detail'] == '2 outstanding assignments'
    assert all('P(HIGH)' not in factor['detail'] and 'SHAP' not in factor['detail'] for factor in data['factors'])
    assert data['precautions'] == [
        {
            'factorKey': 'attendance',
            'text': 'Agree an 85% attendance target for three weeks and review progress every Friday.',
        },
        {
            'factorKey': 'missing_submissions',
            'text': 'Set a clear deadline for each missing item and offer one supervised resubmission.',
        },
    ]


def test_demo_model_lists_details_and_simulation_from_candidate(
    client, teacher_token, monkeypatch,
):
    student_id, _ = _teacher_scope()
    headers = {'Authorization': f'Bearer {teacher_token}'}
    monkeypatch.setattr('backend.app.main.explain_with_active_model', lambda *_args, **_kwargs: {
        'contributions': {},
    })
    with app.state.test_session_factory() as db:
        student = db.query(Student).filter(Student.id == student_id).one()
        features = student_features(student, db)
        expected = predict_with_demo_preview(features)

    warnings = client.get('/api/v1/early-warnings', headers=headers)
    detail = client.get(f'/api/v1/early-warnings/{student_id}', headers=headers)
    simulation = client.post(
        f'/api/v1/early-warnings/{student_id}/simulate',
        headers=headers,
        json={'recoveries': {'attendance': 0.5}},
    )

    assert warnings.status_code == 200
    data = warnings.json()
    assert data['dataStatus'] == 'unvalidated-demo-model'
    assert 'DEMO DATA' in data['message']
    assert 'unvalidated' in data['message']
    assert data['total'] == 1
    assert len(data['items']) == 1
    item = data['items'][0]
    assert item['priority'] == priority_for_probability(float(expected['class_probabilities']['HIGH']))
    assert item['riskProbability'] == round(expected['class_probabilities']['HIGH'], 6)
    assert detail.status_code == 200, detail.text
    detail_data = detail.json()
    assert detail_data['dataStatus'] == 'unvalidated-demo-model'
    assert 'DEMO / RULE-GENERATED' in detail_data['message']
    assert all(factor['key'] in FEATURES for factor in detail_data['factors'])
    assert simulation.status_code == 200, simulation.text
    simulation_data = simulation.json()
    assert simulation_data['demoMode'] is True
    assert simulation_data['label'] == 'DEMO / RULE-GENERATED TRAINING DATA'
    assert simulation_data['before']['riskLevel'] in {'HIGH', 'MEDIUM', 'LOW'}
    assert simulation_data['after']['riskLevel'] in {'HIGH', 'MEDIUM', 'LOW'}


def test_demo_preview_is_separate_from_active_production_artifact():
    production = load_artifact(DEFAULT_ARTIFACT)
    candidate_path = (
        Path(__file__).resolve().parents[1]
        / 'models'
        / 'candidates'
        / 'risk_model_candidate.joblib'
    )
    candidate = load_artifact(candidate_path)

    assert DEFAULT_ARTIFACT.resolve() != candidate_path.resolve()

    assert production['metadata']['features'] == FEATURES
    assert candidate['metadata']['features'] == FEATURES

    assert (
        candidate['metadata']['target_label_provenance']
        == 'transparent_rule_generated_demo_label'
    )
    assert candidate['metadata']['demo_data'] is True


def test_feature_importance_api_uses_candidate_and_only_canonical_features(
    client, teacher_token, monkeypatch,
):
    import numpy as np
    from types import SimpleNamespace

    import ai.explainability.service as explainability

    candidate_path = Path(__file__).resolve().parents[1] / 'models' / 'candidates' / 'risk_model_candidate.joblib'
    observed = {}
    actual_global_importance = explainability.global_importance

    def tracked_global_importance(rows, artifact_path):
        observed['feature_keys'] = [set(row) for row in rows]
        observed['artifact_path'] = artifact_path.resolve()
        return actual_global_importance(rows, artifact_path)

    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: True)
    monkeypatch.setattr('backend.app.main.global_importance', tracked_global_importance)
    monkeypatch.setattr(
        'ai.explainability.service._explain_class_probability',
        lambda _classifier, _background, rows, class_index: SimpleNamespace(
            values=np.full((len(rows), len(FEATURES)), 0.01 * (class_index + 1)),
        ),
    )

    response = client.get(
        '/api/v1/risk/feature-importance',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert observed['artifact_path'] == candidate_path.resolve()
    assert all(keys == set(FEATURES) for keys in observed['feature_keys'])
    assert {feature['name'] for feature in data['features']} == set(FEATURES)
    assert all(np.isfinite(feature['importance']) and feature['importance'] >= 0 for feature in data['features'])
    assert data['model_version'] == load_artifact(candidate_path)['metadata']['model_version']
    assert data['dataStatus'] == 'unvalidated-demo-model'


def test_feature_importance_api_returns_controlled_error_for_incompatible_model(
    client, teacher_token, monkeypatch,
):
    monkeypatch.setattr('backend.app.main.early_warning_uses_demo_model', lambda _db: False)
    monkeypatch.setattr(
        'backend.app.main.global_importance',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError('incompatible feature contract')),
    )

    response = client.get(
        '/api/v1/risk/feature-importance',
        headers={'Authorization': f'Bearer {teacher_token}'},
    )

    assert response.status_code == 503
    assert 'does not support the marks-free explanation contract' in response.json()['detail']
