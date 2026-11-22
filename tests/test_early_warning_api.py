from datetime import datetime, timedelta

from backend.app.main import SIMULATION_REQUESTS, app
from backend.app.models import Course, RiskSnapshot, Student, Teacher, User


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
    assert response.json() == {'riskProbability': 0.7, 'riskClass': 'Watch', 'delta': -10.0}


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
        'attendance', 'assessment', 'missing_submissions',
    ]
    assert data['factors'][0]['detail'] == 'Attendance 71.0%, down 13.3% across recent observations'
    assert data['factors'][1]['detail'] == 'Assessment average 48.0%, down 14.2% across recent observations'
    assert all('P(HIGH)' not in factor['detail'] and 'SHAP' not in factor['detail'] for factor in data['factors'])
    assert data['precautions'] == [
        {
            'factorKey': 'attendance',
            'text': 'Agree an 85% attendance target for three weeks and review progress every Friday.',
        },
        {
            'factorKey': 'assessment',
            'text': 'Set a short catch-up quiz on the weakest topic and provide written feedback within 48 hours.',
        },
        {
            'factorKey': 'missing_submissions',
            'text': 'Set a clear deadline for each missing item and offer one supervised resubmission.',
        },
    ]


def test_demo_model_returns_an_honest_empty_list_and_blocks_details(client, teacher_token):
    student_id, _ = _teacher_scope()
    headers = {'Authorization': f'Bearer {teacher_token}'}

    warnings = client.get('/api/v1/early-warnings', headers=headers)
    detail = client.get(f'/api/v1/early-warnings/{student_id}', headers=headers)

    assert warnings.status_code == 200
    assert warnings.json()['dataStatus'] == 'unvalidated-demo-model'
    assert warnings.json()['items'] == []
    assert warnings.json()['summary']['highPriority'] == 0
    assert detail.status_code == 503
    assert 'DEMO DATA' in detail.json()['detail']
