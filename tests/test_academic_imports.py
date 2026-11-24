from io import BytesIO

import pytest
from openpyxl import Workbook
from ai.risk.features import FEATURES
from ai.risk.predict import DEFAULT_ARTIFACT, load_artifact, predict as predict_risk
from ai.risk.preprocess import build_rows
from backend.app.database import get_db
from backend.app.main import app, student_features
from backend.app.models import InternalAssessmentMark
from backend.app.services.student_cohort_imports import import_student_cohort
from backend.app.models import (
    Assignment,
    AssignmentStudent,
    AcademicDataImportBatch,
    AcademicDataImportRow,
    AcademicMomentum,
    AcademicRiskOutcome,
    Assessment,
    AuditLog,
    Attendance,
    Course,
    LearningActivity,
    Mark,
    Student,
    StudentSubjectAcademicRecord,
    StudentSubjectAcademicRecordHistory,
    Submission,
    Subject,
    Teacher,
    User,
)
from backend.app.services.academic_imports import import_academic_data


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def csv_file(rows: str, filename: str = 'academic.csv'):
    return {'file': (filename, rows.encode(), 'text/csv')}


def make_course(client):
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        student = db.query(Student).filter(Student.student_id == 'T-001').one()
        course = Course(name='Foundations', code='AF-101', students=[student])
        db.add(course)
        db.commit()
        return course.id


def test_valid_csv_import_is_tracked_and_updates_canonical_features(client, teacher_token):
    make_course(client)
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'assessments'},
        files=csv_file('student_id,assessment,score,maximum_marks,official_result,recorded_at,course_code\nT-001,Quiz 1,18,20,FAIL,2026-09-01,AF-101\n'),
        headers=auth(teacher_token),
    )
    assert response.status_code == 200
    result = response.json()
    assert result['imported_rows'] == 1
    assert result['rejected_rows'] == result['duplicate_rows'] == result['unmatched_students'] == 0
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        student = db.query(Student).filter(Student.student_id == 'T-001').one()
        assert db.query(Mark).count() == 1
        assert db.query(Assessment).one().title == 'Quiz 1'
        assert db.query(Assessment).one().maximum_marks == 20
        assert db.query(Assessment).one().maximum_marks_verified is False
        assert db.query(Mark).one().import_batch_id == result['batch_id']
        assert db.query(Mark).one().official_result == 'FAIL'
        assert db.query(Assessment).one().import_batch_id == result['batch_id']
        observation = db.query(AcademicMomentum).filter(AcademicMomentum.import_batch_id == result['batch_id']).one()
        assert observation.value == 90
        assert 'assessment' not in student_features(student, db)
        assert student.assessment == 0


def test_valid_xlsx_import(client, teacher_token):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['student_id', 'attendance_rate', 'observed_at', 'attended'])
    sheet.append(['T-001', 82.5, '2026-09-02', 'yes'])
    content = BytesIO()
    workbook.save(content)
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'attendance'},
        files={'file': ('attendance.xlsx', content.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
        headers=auth(teacher_token),
    )
    assert response.status_code == 200
    assert response.json()['imported_rows'] == 1
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        record = db.query(Attendance).one()
        assert record.attended is True
        assert record.attendance_rate == 82.5
        assert record.import_batch_id == response.json()['batch_id']
        assert db.query(AcademicMomentum).one().value == 82.5
        student = db.query(Student).filter(Student.student_id == 'T-001').one()
        assert student_features(student, db)['attendance'] == 82.5


@pytest.mark.parametrize('filename,content', [
    ('academic.xls', b'not supported'),
    ('academic.xlsx', b'not an xlsx workbook'),
    ('academic.csv', b'\xff\xfe\x00'),
])
def test_malformed_and_unsupported_files_return_validation_errors(client, teacher_token, filename, content):
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'attendance'},
        files={'file': (filename, content)},
        headers=auth(teacher_token),
    )
    assert response.status_code == 422
    assert response.json()['detail']['message']


def test_missing_columns_return_structured_errors(client, teacher_token):
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'attendance'},
        files=csv_file('student_id,attendance_rate\nT-001,75\n'),
        headers=auth(teacher_token),
    )
    assert response.status_code == 422
    errors = response.json()['detail']['validation_errors']
    assert {item['errors'][0] for item in errors} == {
        'Missing required column: observed_at.',
    }


def test_malformed_csv_row_shape_is_rejected(client, teacher_token):
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'attendance'},
        files=csv_file('student_id,attendance_rate,observed_at\nT-001,75,2026-09-02,unexpected\n'),
        headers=auth(teacher_token),
    )
    assert response.status_code == 422
    assert 'more values than the header row' in response.json()['detail']['message']


def test_marks_and_learning_activity_imports_are_provenance_linked(client, teacher_token):
    make_course(client)
    marks = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'marks'},
        files=csv_file('student_id,assessment,score,recorded_at,course_code\nT-001,Internal 1,16,2026-09-03,AF-101\n'),
        headers=auth(teacher_token),
    )
    activity = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'learning_activities'},
        files=csv_file('student_id,activity_type,quantity,occurred_at\nT-001,practice_sessions,4,2026-09-03\n'),
        headers=auth(teacher_token),
    )
    assert marks.status_code == activity.status_code == 200
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        assert db.query(Mark).one().import_batch_id == marks.json()['batch_id']
        assert db.query(Assessment).one().maximum_marks_verified is False
        activity_row = db.query(LearningActivity).one()
        assert activity_row.import_batch_id == activity.json()['batch_id']
        assert activity_row.quantity == 4
        assert student_features(db.query(Student).filter(Student.student_id == 'T-001').one(), db)['activity_per_week'] == 4


def test_marks_import_preserves_existing_verification_and_rejects_conflicting_maximum(client, teacher_token):
    course_id = make_course(client)
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        db.add(Assessment(
            course_id=course_id,
            title='Verified FA-1',
            maximum_marks=25,
            maximum_marks_verified=True,
        ))
        db.commit()

    matching = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'marks'},
        files=csv_file(
            'student_id,assessment,score,maximum_marks,recorded_at,course_code\n'
            'T-001,Verified FA-1,11,25,2026-09-03,AF-101\n',
            filename='matching-maximum.csv',
        ),
        headers=auth(teacher_token),
    )
    assert matching.status_code == 200, matching.text
    with next(override()) as db:
        assert db.query(Assessment).filter_by(title='Verified FA-1').one().maximum_marks_verified is True
        assert db.query(Mark).count() == 1

    conflicting = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'assessments'},
        files=csv_file(
            'student_id,assessment,score,maximum_marks,recorded_at,course_code\n'
            'T-001,Verified FA-1,11,50,2026-09-04,AF-101\n',
            filename='conflicting-maximum.csv',
        ),
        headers=auth(teacher_token),
    )
    assert conflicting.status_code == 200, conflicting.text
    assert conflicting.json()['imported_rows'] == 0
    assert conflicting.json()['rejected_rows'] == 1
    assert 'does not match the existing assessment' in conflicting.json()['validation_errors'][0]['errors'][0]
    with next(override()) as db:
        assessment = db.query(Assessment).filter_by(title='Verified FA-1').one()
        assert assessment.maximum_marks == 25
        assert assessment.maximum_marks_verified is True
        assert db.query(Mark).count() == 1


def test_imported_records_match_training_features_and_reach_risk_analytics(client, teacher_token):
    make_course(client)
    student_id = client.app.state.test_student_id
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        student = db.query(Student).filter(Student.id == student_id).one()
        student.previous_gpa = 2.8
        student.current_gpa = 3.0
        student.attendance = 55
        student.assessment = 50
        active_assignment = Assignment(title='Current work', subject='Foundations', section='A', status='active')
        archived_assignment = Assignment(title='Archived work', subject='Foundations', section='A', status='archived')
        db.add_all([active_assignment, archived_assignment])
        db.flush()
        db.add_all([
            AssignmentStudent(assignment_id=active_assignment.id, student_id=student.id),
            AssignmentStudent(assignment_id=archived_assignment.id, student_id=student.id),
            Submission(student_id=student.id, assignment_id=active_assignment.id, status='graded'),
        ])
        db.commit()

    headers = auth(teacher_token)
    attendance = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'attendance'},
        files=csv_file(
            'student_id,attendance_rate,observed_at\n'
            'T-001,90,2026-08-01\nT-001,88,2026-08-08\nT-001,86,2026-08-15\nT-001,84,2026-08-22\n',
            'attendance.csv',
        ),
        headers=headers,
    )
    assessments = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'assessments'},
        files=csv_file(
            'student_id,assessment,score,maximum_marks,recorded_at,course_code\n'
            'T-001,Quiz 1,18,20,2026-08-01,AF-101\n'
            'T-001,Quiz 2,17,20,2026-08-08,AF-101\n'
            'T-001,Quiz 3,16,20,2026-08-15,AF-101\n'
            'T-001,Quiz 4,15,20,2026-08-22,AF-101\n',
            'assessments.csv',
        ),
        headers=headers,
    )
    activities = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'learning_activities'},
        files=csv_file(
            'student_id,activity_type,quantity,occurred_at\n'
            'T-001,practice,1,2026-08-01\nT-001,practice,3,2026-08-08\n'
            'T-001,practice,5,2026-08-15\nT-001,practice,7,2026-08-22\n'
            'T-001,practice,9,2026-08-29\n',
            'activities.csv',
        ),
        headers=headers,
    )
    assert [response.status_code for response in (attendance, assessments, activities)] == [200, 200, 200]
    assert [response.json()['imported_rows'] for response in (attendance, assessments, activities)] == [4, 4, 5]

    with next(override()) as db:
        student = db.query(Student).filter(Student.id == student_id).one()
        imported_features = student_features(student, db)
        processed_student = next(row for row in build_rows(db) if row['student_id'] == 'T-001')
        for feature in FEATURES:
            assert imported_features[feature] == pytest.approx(processed_student[feature])
        assert imported_features['activity_per_week'] == pytest.approx(6.0)
        assert imported_features['missing_submissions'] == 1.0
        batch_ids = {response.json()['batch_id'] for response in (attendance, assessments, activities)}
        assert db.query(AcademicDataImportBatch).filter(AcademicDataImportBatch.id.in_(batch_ids)).count() == 3
        assert db.query(AcademicDataImportRow).filter(AcademicDataImportRow.batch_id.in_(batch_ids), AcademicDataImportRow.status == 'imported').count() == 13
        assert db.query(Attendance).filter(Attendance.import_batch_id == attendance.json()['batch_id']).count() == 4
        assert db.query(Mark).filter(Mark.import_batch_id == assessments.json()['batch_id']).count() == 4
        assert db.query(LearningActivity).filter(LearningActivity.import_batch_id == activities.json()['batch_id']).count() == 5

    explanation_response = client.get(f'/api/v1/students/{student_id}/explanation', headers=headers)
    assert explanation_response.status_code == 200
    explanation_data = explanation_response.json()
    model_prediction = predict_risk(imported_features)
    prediction = explanation_data['prediction']
    assert explanation_data['features'] == imported_features
    assert prediction['model_version'] == load_artifact(DEFAULT_ARTIFACT)['metadata']['model_version']
    assert prediction['risk_level'] == model_prediction['risk_level']
    assert prediction['risk_level'] == max(prediction['class_probabilities'], key=prediction['class_probabilities'].get)
    assert prediction['risk_probability'] == prediction['class_probabilities'][prediction['risk_level']]
    assert prediction['risk_probability'] == pytest.approx(model_prediction['risk_probability'])
    assert explanation_data['explanation']['target_class'] == prediction['risk_level']
    assert explanation_data['explanation']['target'] == f"P({prediction['risk_level']})"

    directory = client.get('/api/v1/students?search=T-001&limit=10', headers=headers)
    assert directory.status_code == 200
    directory_student = directory.json()['items'][0]
    assert directory_student['attendance'] == imported_features['attendance']
    assert directory_student['assessment'] == 50
    assert directory_student['risk_level'] == prediction['risk_level']
    monitoring = client.get('/api/v1/risk/monitoring', headers=headers)
    dashboard = client.get('/api/v1/dashboard/teacher', headers=headers)
    assert monitoring.status_code == dashboard.status_code == 200
    assert sum(monitoring.json()['distribution'].values()) == 2
    assert monitoring.json()['distribution'][prediction['risk_level']] >= 1
    assert sum(dashboard.json()['summary'][f'{level.lower()}_risk'] for level in ('HIGH', 'MEDIUM', 'LOW')) == 2


def test_unknown_student_is_reported_and_not_imported(client, teacher_token):
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'attendance'},
        files=csv_file('student_id,attendance_rate,observed_at\nUNKNOWN,75,2026-09-02\n'),
        headers=auth(teacher_token),
    )
    assert response.status_code == 200
    assert response.json()['unmatched_students'] == 1
    assert response.json()['unmatched_student_ids'] == ['UNKNOWN']
    assert response.json()['rejected_rows'] == 1
    assert 'Unknown student_id' in response.json()['validation_errors'][0]['errors'][0]


@pytest.mark.parametrize('source', [
    'model prediction', 'model-prediction', 'SHAP output', 'inference output',
    'synthetic prediction', 'synthetic', 'AI-generated label', 'classifier output',
])
def test_model_derived_risk_outcomes_are_rejected_with_source_row_provenance(client, teacher_token, source):
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'risk_outcomes'},
        files=csv_file(
            f'student_id,academic_period,risk_target,observed_at,outcome_source\nT-001,2026-S1,HIGH,2026-09-01,{source}\n',
            f'prohibited-{source.replace(" ", "-")}.csv',
        ),
        headers=auth(teacher_token),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['imported_rows'] == 0
    assert payload['rejected_rows'] == 1
    error = payload['validation_errors'][0]
    assert error['row'] == 2
    assert error['student_id'] == 'T-001'
    assert error['academic_period'] == '2026-S1'
    assert 'risk outcomes cannot be sourced' in error['errors'][0]
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        assert db.query(AcademicRiskOutcome).count() == 0
        rejected = db.query(AcademicDataImportRow).filter_by(dataset_type='risk_outcomes', status='rejected').one()
        batch = db.query(AcademicDataImportBatch).filter_by(id=rejected.batch_id).one()
        assert batch.source_filename == f'prohibited-{source.replace(" ", "-")}.csv'


def test_risk_outcome_import_requires_timezone_and_normalizes_offset_to_utc(client, teacher_token):
    accepted = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'risk_outcomes'},
        files=csv_file(
            'student_id,academic_period,risk_target,observed_at,outcome_source\n'
            'T-001,2026-S1,HIGH,2026-09-01T09:00:00-05:00,faculty review\n',
            'dated-outcomes.csv',
        ),
        headers=auth(teacher_token),
    )
    assert accepted.status_code == 200
    assert accepted.json()['imported_rows'] == 1
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        outcome = db.query(AcademicRiskOutcome).filter_by(academic_period='2026-S1').one()
        assert outcome.observed_at.isoformat() == '2026-09-01T14:00:00'
        assert outcome.temporal_evidence_verified is True

    rejected = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'risk_outcomes'},
        files=csv_file(
            'student_id,academic_period,risk_target,observed_at,outcome_source\n'
            'T-002,2026-S1,HIGH,2026-09-01T09:00:00,faculty review\n',
            'naive-outcomes.csv',
        ),
        headers=auth(teacher_token),
    )
    assert rejected.status_code == 200
    assert rejected.json()['imported_rows'] == 0
    assert rejected.json()['rejected_rows'] == 1
    assert 'timezone offset' in rejected.json()['validation_errors'][0]['errors'][0]
    with next(override()) as db:
        assert db.query(AcademicRiskOutcome).filter_by(academic_period='2026-S1').count() == 1


def test_subject_record_import_normalizes_offsets_and_rejects_naive_evidence(client, teacher_token):
    with client.app.state.test_session_factory() as db:
        db.add(Subject(code='CN', name='Computer Networks'))
        db.commit()
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        accepted = import_academic_data(
            db,
            user_id=teacher.id,
            filename='subject-observations.csv',
            content=(
                'student_id,subject_code,academic_period,fa1,fa1_observed_at,fa2,fa2_observed_at,'
                'assignment_mark,assignment_observed_at,attendance_rate,attendance_observed_at\n'
                'T-001,CN,2026-S1,12,2026-09-01T09:00:00-05:00,11,2026-09-01T16:00:00+02:00,'
                '15,2026-09-01T14:00:00Z,80,2026-09-01T14:00:00Z\n'
            ).encode(),
            dataset_type='subject_records',
        )
        record = db.query(StudentSubjectAcademicRecord).filter_by(academic_period='2026-S1').one()
        assert accepted['imported_rows'] == 1
        assert record.temporal_evidence_verified is True
        assert record.fa1_observed_at.isoformat() == '2026-09-01T14:00:00'
        assert record.fa2_observed_at.isoformat() == '2026-09-01T14:00:00'

        rejected = import_academic_data(
            db,
            user_id=teacher.id,
            filename='naive-subject-observations.csv',
            content=(
                'student_id,subject_code,academic_period,fa1,fa1_observed_at\n'
                'T-002,CN,2026-S1,12,2026-09-01T09:00:00\n'
            ).encode(),
            dataset_type='subject_records',
        )
        assert rejected['imported_rows'] == 0
        assert rejected['rejected_rows'] == 1
        assert 'timezone offset' in rejected['validation_errors'][0]['errors'][0]
        other_student = db.query(Student).filter_by(student_id='T-002').one()
        assert db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=other_student.id,
            academic_period='2026-S1',
        ).count() == 0


def test_fa1_maximum_import_remains_unverified_until_audited_subject_review(client, teacher_token):
    student_id = client.app.state.test_student_id
    period = '2026-S1'
    with client.app.state.test_session_factory() as db:
        student = db.query(Student).filter_by(id=student_id).one()
        teacher_user = db.query(User).filter_by(email='teacher@example.com').one()
        teacher = Teacher(user_id=teacher_user.id, department='Engineering')
        db.add(teacher)
        db.flush()
        subject = db.query(Subject).filter_by(code='CN').first()
        if subject is None:
            subject = Subject(code='CN', name='Computer Networks', department='Engineering')
            db.add(subject)
            db.flush()
        course = Course(
            name='Computer Networks',
            code='CN-A',
            section='A',
            subject_id=subject.id,
            teacher_id=teacher.id,
        )
        db.add(course)
        db.flush()
        student.course_id = course.id
        db.commit()

    imported = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'subject_records'},
        files=csv_file(
            'student_id,subject_code,academic_period,fa1,fa1_observed_at,fa1_maximum_marks\n'
            'T-001,CN,2026-S1,22,2026-09-01T09:00:00Z,50\n',
            'cn-fa1-source.csv',
        ),
        headers=auth(teacher_token),
    )
    assert imported.status_code == 200
    assert imported.json()['imported_rows'] == 1

    with client.app.state.test_session_factory() as db:
        record = db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student_id,
            academic_period=period,
        ).one()
        observed_at = record.fa1_observed_at
        import_batch_id = record.import_batch_id
        assert record.fa1 == 22
        assert record.fa1_maximum_marks == 50
        assert record.fa1_maximum_verified is False
        assert record.temporal_evidence_verified is True

    review_url = f'/api/v1/academic-register/{student_id}/CN/fa1-maximum-verification?academic_period={period}'
    review_payload = {'source_reference': 'Official FA-1 assessment register, page 3, cell F8'}
    with client.app.state.test_session_factory() as db:
        record = db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student_id,
            academic_period=period,
        ).one()
        record.fa1_observed_at = None
        record.temporal_evidence_verified = False
        db.commit()
    missing_date = client.post(review_url, json=review_payload, headers=auth(teacher_token))
    assert missing_date.status_code == 409
    with client.app.state.test_session_factory() as db:
        record = db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student_id,
            academic_period=period,
        ).one()
        record.fa1_observed_at = observed_at
        record.temporal_evidence_verified = True
        record.fa1_maximum_marks = None
        db.commit()
    missing_maximum = client.post(review_url, json=review_payload, headers=auth(teacher_token))
    assert missing_maximum.status_code == 409
    with client.app.state.test_session_factory() as db:
        record = db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student_id,
            academic_period=period,
        ).one()
        record.fa1_maximum_marks = 50
        record.import_batch_id = None
        db.commit()
    missing_provenance = client.post(review_url, json=review_payload, headers=auth(teacher_token))
    assert missing_provenance.status_code == 409
    with client.app.state.test_session_factory() as db:
        record = db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student_id,
            academic_period=period,
        ).one()
        record.import_batch_id = import_batch_id
        db.commit()
    import_filename_only = client.post(
        review_url,
        json={'source_reference': 'cn-fa1-source.csv'},
        headers=auth(teacher_token),
    )
    assert import_filename_only.status_code == 409

    reviewed = client.post(
        review_url,
        json=review_payload,
        headers=auth(teacher_token),
    )
    assert reviewed.status_code == 200
    assert reviewed.json()['fa1_maximum_verified'] is True
    assert reviewed.json()['fa1_maximum_marks'] == 50
    assert reviewed.json()['fa1_maximum_source_reference'] == 'Official FA-1 assessment register, page 3, cell F8'
    with client.app.state.test_session_factory() as db:
        record = db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student_id,
            academic_period=period,
        ).one()
        assert record.fa1_maximum_verified is True
        history = db.query(StudentSubjectAcademicRecordHistory).filter_by(
            academic_record_id=record.id,
            change_kind='fa1_maximum_verified',
        ).one()
        assert history.fa1_maximum_marks == 50
        assert history.fa1_maximum_verified is True
        audit = db.query(AuditLog).filter_by(
            action='academic_subject_record.fa1_maximum_verified',
            resource_id=str(record.id),
        ).one()
        assert audit.detail['source_reference'] == 'Official FA-1 assessment register, page 3, cell F8'


def test_ambiguous_student_identifier_is_rejected(client, teacher_token):
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        db.add(Student(student_id='t-001', full_name='Ambiguous duplicate'))
        db.commit()
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'attendance'},
        files=csv_file('student_id,attendance_rate,observed_at\nT-001,75,2026-09-02\n'),
        headers=auth(teacher_token),
    )
    assert response.status_code == 200
    assert response.json()['rejected_rows'] == 1
    assert 'Ambiguous student_id' in response.json()['validation_errors'][0]['errors'][0]


def test_duplicate_rows_and_repeated_upload_are_not_reimported(client, teacher_token):
    content = 'student_id,attendance_rate,observed_at\nT-001,75,2026-09-02\nt-001,75,2026-09-02\n'
    first = client.post('/api/v1/academic-data/import', data={'dataset_type': 'attendance'}, files=csv_file(content), headers=auth(teacher_token))
    assert first.status_code == 200
    assert first.json()['imported_rows'] == 1
    assert first.json()['duplicate_rows'] == 1
    second = client.post('/api/v1/academic-data/import', data={'dataset_type': 'attendance'}, files=csv_file(content), headers=auth(teacher_token))
    assert second.status_code == 200
    assert second.json()['duplicate_import'] is True
    assert second.json()['imported_rows'] == 0
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        assert db.query(Attendance).count() == 1
        assert db.query(AcademicMomentum).count() == 1
        assert db.query(AcademicDataImportBatch).count() == 1


def test_student_cannot_import_academic_data(client, student_token):
    response = client.post(
        '/api/v1/academic-data/import',
        data={'dataset_type': 'attendance'},
        files=csv_file('student_id,attendance_rate,observed_at\nT-001,75,2026-09-02\n'),
        headers=auth(student_token),
    )
    assert response.status_code == 403


def test_teacher_only_import_listing(client, teacher_token, student_token):
    assert client.get('/api/v1/academic-data/imports', headers=auth(teacher_token)).status_code == 200
    assert client.get('/api/v1/academic-data/imports', headers=auth(student_token)).status_code == 403
    assert client.get('/api/v1/academic-data/imports').status_code == 401


def test_import_commit_failure_rolls_back_all_database_updates(client, teacher_token, monkeypatch):
    original_commit = __import__('sqlalchemy.orm', fromlist=['Session']).Session.commit

    def fail_commit(session):
        if session.query(AcademicDataImportBatch).count():
            raise RuntimeError('simulated commit failure')
        return original_commit(session)

    monkeypatch.setattr('backend.app.services.academic_imports.Session.commit', fail_commit)
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        teacher = db.query(User).filter(User.email == 'teacher@example.com').one()
        with pytest.raises(RuntimeError, match='simulated commit failure'):
            import_academic_data(
                db,
                user_id=teacher.id,
                filename='attendance.csv',
                content=b'student_id,attendance_rate,observed_at\nT-001,75,2026-09-02\n',
                dataset_type='attendance',
            )
        assert db.query(AcademicDataImportBatch).count() == 0
        assert db.query(AcademicDataImportRow).count() == 0
        assert db.query(Attendance).count() == 0
        assert db.query(AcademicMomentum).count() == 0

def test_student_cohort_import_preserves_null_marks_and_tracks_batch(client):
    factory = app.state.test_session_factory

    headers = [
        "student_id", "usn", "name", "department", "course",
        "branch", "semester", "previous_cgpa", "current_cgpa",
    ]

    subjects = [
        "ai", "evs", "network", "research_methodology",
        "software_engineering", "toc",
    ]

    for stage in (1, 2):
        headers.extend(
            f"internal_{stage}_{subject}_marks_out_of_25"
            for subject in subjects
        )

    values = [
        "COHORT001", "TEST26CS001", "Test Student",
        "Computer Science and Engineering", "B.Tech",
        "CSE", "5th Semester", "7.2", "7.5",
    ]

    values.extend(["18", "", "17", "19", "20", "21"])
    values.extend(["", "", "", "", "", ""])

    content = (
        ",".join(headers) + "\n"
        + ",".join(values) + "\n"
    ).encode("utf-8")

    with factory() as db:
        teacher = db.query(User).filter_by(
            email="teacher@example.com"
        ).one()

        db.add_all([
            Subject(code="CN", name="Computer Networks"),
            Subject(code="EVS", name="Environmental Studies"),
            Subject(code="RM", name="Research Methodology"),
            Subject(code="SE", name="Software Engineering"),
            Subject(code="TOC", name="Theory of Computation"),
        ])
        db.commit()

        result = import_student_cohort(
            db,
            user_id=teacher.id,
            filename="cohort.csv",
            content=content,
        )
        db.commit()

        assert result["imported_rows"] == 1
        assert result["internal_mark_records"] == 12
        assert result["created_subjects"] == ["AI"]

        student = db.query(Student).filter_by(
            student_id="COHORT001"
        ).one()

        marks = db.query(InternalAssessmentMark).filter_by(
            student_id=student.id
        ).all()

        assert len(marks) == 12
        assert sum(mark.marks is None for mark in marks) == 7

        assert student.previous_cgpa == 7.2
        assert student.current_cgpa == 7.5
        assert student.course_id is None
        assert student.data_origin == "SYNTHETIC DEMO"

        assert db.query(AcademicDataImportBatch).filter_by(
            id=result["batch_id"]
        ).one().imported_rows == 1
