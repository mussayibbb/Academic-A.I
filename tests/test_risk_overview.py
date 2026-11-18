from datetime import datetime

from backend.app.models import (
    AcademicDataImportBatch,
    AcademicRiskOutcome,
    Course,
    Student,
    StudentSubjectAcademicRecord,
    Subject,
    Teacher,
    User,
)


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _teacher_class(client):
    with client.app.state.test_session_factory() as db:
        user = db.query(User).filter_by(email='teacher@example.com').one()
        teacher = Teacher(user_id=user.id, department='Science')
        subject = Subject(name='Biology', code='BIO-OVERVIEW', department='Science')
        db.add_all([teacher, subject])
        db.flush()
        batch = AcademicDataImportBatch(
            dataset_type='subject_records',
            source_filename='semester-source.xlsx',
            file_checksum='a' * 64,
            imported_by_user_id=user.id,
        )
        db.add(batch)
        db.flush()
        course = Course(name='Biology A', code='BIO-OVERVIEW-A', section='A', subject_id=subject.id, teacher_id=teacher.id)
        db.add(course)
        db.flush()
        assigned = db.query(Student).filter_by(id=client.app.state.test_student_id).one()
        assigned.course_id = course.id
        assigned.section = 'A'
        also_assigned = db.query(Student).filter_by(id=client.app.state.test_other_student_id).one()
        also_assigned.course_id = course.id
        also_assigned.section = 'A'
        unassigned = Student(student_id='T-UNASSIGNED', full_name='Unassigned Student', section='Z')
        db.add(unassigned)
        db.flush()
        db.add_all([
            AcademicRiskOutcome(
                student_id=assigned.id,
                academic_period='2026-S1',
                risk_level='HIGH',
                observed_at=datetime(2026, 8, 1),
                outcome_source='verified institutional register',
            ),
            StudentSubjectAcademicRecord(
                student_id=assigned.id,
                subject_id=subject.id,
                academic_period='2026-S1',
                fa1=12,
                fa1_observed_at=datetime(2026, 8, 2),
                temporal_evidence_verified=True,
                import_batch_id=batch.id,
                attendance_rate=None,
                attendance_observed_at=None,
            ),
            StudentSubjectAcademicRecord(
                student_id=also_assigned.id,
                subject_id=subject.id,
                academic_period='2026-S1',
                fa1=10,
                fa1_observed_at=None,
                temporal_evidence_verified=False,
            ),
        ])
        db.commit()
        return assigned.id, subject.id, unassigned.id


def test_risk_overview_uses_recorded_outcomes_and_keeps_missing_evidence_separate(client, teacher_token):
    student_id, subject_id, _ = _teacher_class(client)

    response = client.get('/api/v1/teacher/student-risk-overview', headers=_auth(teacher_token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['academic_period'] == '2026-S1'
    assert body['classes'] == ['A']
    assert [item['risk_category'] for item in body['items']] == ['HIGH', 'INSUFFICIENT_EVIDENCE']
    verified, insufficient = body['items']
    assert verified['id'] == student_id
    assert verified['risk_outcome']['outcome_source'] == 'verified institutional register'
    subject_record = verified['subjects'][0]
    assert subject_record == {
        'record_id': subject_record['record_id'],
        'id': subject_id,
        'code': 'BIO-OVERVIEW',
        'name': 'Biology',
        'academic_period': '2026-S1',
        'fa1': 12.0,
        'fa1_observed_at': '2026-08-02T00:00:00',
        'temporal_evidence_verified': True,
        'import_batch_id': subject_record['import_batch_id'],
        'source_filename': 'semester-source.xlsx',
        'fa2': None,
        'fa2_observed_at': None,
        'assignment_mark': None,
        'assignment_observed_at': None,
        'attendance_rate': None,
        'attendance_observed_at': None,
    }
    assert subject_record['record_id'] > 0
    assert subject_record['import_batch_id'] > 0
    assert insufficient['risk_outcome'] is None
    assert 'No independently recorded risk outcome is available for this semester.' in insufficient['indicators']
    assert 'No dated FA-1 mark is available for this semester.' in insufficient['indicators']
    assert insufficient['subjects'][0]['fa1_observed_at'] is None
    assert insufficient['subjects'][0]['temporal_evidence_verified'] is False


def test_risk_overview_filters_and_enforces_teacher_class_scope(client, teacher_token):
    student_id, _, unassigned_id = _teacher_class(client)

    high = client.get(
        '/api/v1/teacher/student-risk-overview?risk_category=HIGH&section=A&search=T-001',
        headers=_auth(teacher_token),
    )
    assert high.status_code == 200, high.text
    assert [item['id'] for item in high.json()['items']] == [student_id]

    invalid_period = client.get(
        '/api/v1/teacher/student-risk-overview?academic_period=invalid',
        headers=_auth(teacher_token),
    )
    assert invalid_period.status_code == 422

    outside_scope = client.get(
        f'/api/v1/teacher/student-risk-overview?student_id={unassigned_id}',
        headers=_auth(teacher_token),
    )
    assert outside_scope.status_code == 404
