from backend.app.main import SIMULATION_REQUESTS, app
from backend.app.models import Course, Student, Teacher, User


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
