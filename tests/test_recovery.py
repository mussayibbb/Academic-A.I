import json
from datetime import date, datetime, timedelta
from io import BytesIO

import pytest
from ai.llm.provider import LocalLLMProvider
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from sqlalchemy import func, create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import (
    AcademicDataImportBatch,
    ApprovedTopic,
    AssessmentQuestion,
    Assessment,
    AuditLog,
    Course,
    Mark,
    LearningAssessment,
    RecoveryAdaptationDecision,
    RecoveryTask,
    RecoveryTaskEvent,
    RagChunk,
    RagDocument,
    Student,
    StudentSubjectAcademicRecord,
    SubjectFocusPolicy,
    SubjectModule,
    SubjectModuleResource,
    TopicLearningAssignment,
    SupportPlanVersion,
    Subject,
    Teacher,
    User,
)
from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.services.auth import hash_password
from backend.app.services.recovery import (
    build_recovery_blueprint_schedule,
    build_recovery_evidence,
    make_draft_plan,
    record_adaptation_decision,
)
from backend.app.schemas.recovery import RecoveryBlueprintGenerateRequest


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def link_teacher_and_add_evidence(client, *, include_evidence=True):
    factory = client.app.state.test_session_factory
    student_id = client.app.state.test_student_id
    with factory() as db:
        teacher_user = db.query(User).filter_by(email='teacher@example.com').one()
        teacher = Teacher(user_id=teacher_user.id, department='Science')
        subject = Subject(name='Biology', code='BIO-101', department='Science')
        db.add_all([teacher, subject])
        db.flush()
        course = Course(
            name='Biology 101',
            code='BIO-101-A',
            section='A',
            subject_id=subject.id,
            teacher_id=teacher.id,
        )
        db.add(course)
        db.flush()
        student = db.query(Student).filter_by(id=student_id).one()
        student.course_id = course.id
        if include_evidence:
            db.add(StudentSubjectAcademicRecord(
                student_id=student.id,
                subject_id=subject.id,
                academic_period='2026-T1',
                fa1=45,
                fa1_observed_at=datetime(2026, 8, 10),
            ))
            assessment = Assessment(course_id=course.id, title='SYNTHETIC TEST FA1', maximum_marks=100)
            db.add(assessment)
            db.flush()
            db.add(Mark(student_id=student.id, assessment_id=assessment.id, score=45, recorded_at=datetime(2026, 8, 10)))
        db.commit()
        return student.id


def create_blueprint_test_student(client):
    student_id = link_teacher_and_add_evidence(client, include_evidence=False)
    with client.app.state.test_session_factory() as db:
        db.add(SubjectFocusPolicy(
            version=1,
            threshold_score=12,
            threshold_maximum=25,
            change_reason='Test subject support policy',
        ))
        db.commit()
    return student_id


def add_blueprint_test_subject(
    client,
    student_id,
    code,
    assessments=(),
    *,
    record_fa1=14,
    record_observed_at=datetime(2026, 1, 1),
    attendance_only=False,
    teacher_authorized=True,
    add_approved_topic=False,
):
    with client.app.state.test_session_factory() as db:
        teacher = db.query(Teacher).join(User).filter(User.email == 'teacher@example.com').one()
        subject = Subject(name=f'{code} subject', code=code, department='Science')
        db.add(subject)
        db.flush()
        course_teacher = teacher
        if not teacher_authorized:
            other_teacher_user = User(
                email=f'{code.lower()}-teacher@example.com',
                full_name='Other Teacher',
                role='TEACHER',
                password_hash=hash_password('other-teacher-pass'),
            )
            db.add(other_teacher_user)
            db.flush()
            course_teacher = Teacher(user_id=other_teacher_user.id, department='Science')
            db.add(course_teacher)
            db.flush()
        course = Course(
            name=f'{code} course',
            code=f'{code}-A',
            section='A',
            subject_id=subject.id,
            teacher_id=course_teacher.id,
        )
        db.add(course)
        db.flush()
        record = StudentSubjectAcademicRecord(
            student_id=student_id,
            subject_id=subject.id,
            academic_period='2026-S1',
            attendance_rate=85 if attendance_only else None,
            attendance_observed_at=record_observed_at if attendance_only else None,
            fa1=record_fa1,
            fa1_observed_at=record_observed_at if record_fa1 is not None else None,
        )
        db.add(record)
        for index, (score, maximum, verified, official_result, recorded_at) in enumerate(assessments):
            assessment = Assessment(
                course_id=course.id,
                title=f'SYNTHETIC TEST FA-1 {code} assessment {index + 1}',
                maximum_marks=maximum,
                maximum_marks_verified=verified,
            )
            db.add(assessment)
            db.flush()
            db.add(Mark(
                student_id=student_id,
                assessment_id=assessment.id,
                score=score,
                official_result=official_result,
                recorded_at=recorded_at,
            ))
        if add_approved_topic:
            module = SubjectModule(subject_id=subject.id, module_number=1, title=f'{code} module')
            db.add(module)
            db.flush()
            resource = SubjectModuleResource(
                module_id=module.id,
                subject_id=subject.id,
                teacher_id=teacher.id,
                title=f'{code} source',
                original_filename=f'{code}.txt',
                storage_path='not-read-by-this-test',
                file_type='txt',
                file_size=10,
                content_hash='b' * 64,
                processing_status='PROCESSED',
                extracted_text='Teacher-approved source text.',
            )
            db.add(resource)
            db.flush()
            db.add(ApprovedTopic(
                subject_id=subject.id,
                module_id=module.id,
                source_resource_id=resource.id,
                teacher_id=teacher.id,
                title=f'{code} topic',
                summary='A source-backed topic.',
                learning_objectives=['Explain the topic.'],
                concepts=['topic'],
                approval_status='APPROVED',
                approved_by_user_id=teacher.user_id,
                source_provenance={'resource_id': resource.id, 'order_uncertain': False},
            ))
        db.commit()
        return subject.id


def request_recovery_blueprint(client, teacher_token, student_id, subject_ids=None):
    payload = {
        'academic_period': '2026-S1',
        'exam_date': (date.today() + timedelta(days=30)).isoformat(),
        'acknowledge_no_syllabus': True,
        'available_minutes_per_day': 60,
    }
    if subject_ids is not None:
        payload['subject_ids'] = subject_ids
    return client.post(
        f'/api/v1/students/{student_id}/recovery-blueprints',
        json=payload,
        headers=auth(teacher_token),
    )


def test_blueprint_request_does_not_require_or_limit_a_selected_subject_list():
    shared = {
        'academic_period': '2026-S1',
        'exam_date': (date.today() + timedelta(days=30)).isoformat(),
        'acknowledge_no_syllabus': True,
    }
    assert RecoveryBlueprintGenerateRequest(**shared).subject_ids is None
    assert len(RecoveryBlueprintGenerateRequest(
        **shared,
        subject_ids=list(range(1, 22)),
    ).subject_ids) == 21


def test_blueprint_schedule_includes_all_subjects_without_a_fixed_entry_limit():
    recommendations = [
        {'topic_id': topic_id, 'priority': 'LOW', 'suggested_minutes': 5}
        for topic_id in range(1, 32)
    ]
    topic_subjects = {topic_id: topic_id for topic_id in range(1, 32)}
    topic_module_numbers = {topic_id: 3 for topic_id in range(1, 32)}

    schedule, blockers = build_recovery_blueprint_schedule(
        recommendations,
        topic_subjects,
        start_date=date.today() + timedelta(days=1),
        exam_date=date.today() + timedelta(days=30),
        minutes_per_day=600,
        topic_module_numbers=topic_module_numbers,
        eligible_subject_ids=set(range(1, 32)),
    )

    assert len(schedule) > 120
    assert {item['subject_id'] for item in schedule} == set(range(1, 32))
    assert not blockers


def test_blueprint_schedule_gates_module_four_and_revision_after_prior_stages():
    start = date.today() + timedelta(days=1)
    exam = start + timedelta(days=20)
    schedule, blockers = build_recovery_blueprint_schedule(
        [
            {'topic_id': 3, 'priority': 'LOW', 'suggested_minutes': 30},
            {'topic_id': 4, 'priority': 'HIGH', 'suggested_minutes': 30},
            {'topic_id': 5, 'priority': 'MEDIUM', 'suggested_minutes': 30},
            {'topic_id': 6, 'priority': 'LOW', 'suggested_minutes': 30},
        ],
        {3: 1, 4: 2, 5: 1, 6: 2},
        start_date=start,
        exam_date=exam,
        minutes_per_day=60,
        topic_module_numbers={3: 3, 4: 3, 5: 4, 6: 4},
        eligible_subject_ids={1, 2},
    )

    stage_one = [item for item in schedule if item['stage'] == 'MODULE_3']
    stage_two = [item for item in schedule if item['stage'] == 'MODULE_4']
    stage_three = [item for item in schedule if item['stage'] == 'REVISION_ASSESSMENT']
    assert max(item['scheduled_date'] for item in stage_one) < min(item['scheduled_date'] for item in stage_two)
    assert max(item['scheduled_date'] for item in stage_two) < min(item['scheduled_date'] for item in stage_three)
    assert {item['subject_id'] for item in stage_one} == {1, 2}
    assert {item['subject_id'] for item in stage_two} == {1, 2}
    assert {item['subject_id'] for item in stage_three} == {1, 2}
    assert not blockers


def test_blueprint_automatically_includes_more_than_twenty_eligible_subjects(
    client,
    teacher_token,
):
    student_id = create_blueprint_test_student(client)
    factory = client.app.state.test_session_factory
    with factory() as db:
        student = db.get(Student, student_id)
        student.course_id = None
        student.section = 'A'
        teacher = db.query(Teacher).join(User).filter(User.email == 'teacher@example.com').one()
        subject_ids = set()
        for index in range(21):
            subject = Subject(
                code=f'AUTO-{index:02d}',
                name=f'Automatically included subject {index}',
                department='Science',
            )
            db.add(subject)
            db.flush()
            course = Course(
                name=f'Course {index}',
                code=f'AUTO-{index:02d}-A',
                section='A',
                subject_id=subject.id,
                teacher_id=teacher.id,
            )
            db.add(course)
            db.flush()
            assessment = Assessment(
                course_id=course.id,
                title='SYNTHETIC TEST FA-1',
                maximum_marks=25,
                maximum_marks_verified=True,
            )
            db.add(assessment)
            db.flush()
            db.add(Mark(
                student_id=student_id,
                assessment_id=assessment.id,
                score=11.99,
                recorded_at=datetime(2026, 8, 1) + timedelta(days=index),
            ))
            subject_ids.add(subject.id)
        db.commit()

    response = request_recovery_blueprint(client, teacher_token, student_id)

    assert response.status_code == 201, response.text
    blueprint = response.json()['plan']['blueprint']
    assert {item['subject_id'] for item in blueprint['subjects']} == subject_ids
    assert len(blueprint['subjects']) == 21
    assert all(item['eligibility']['status'] == 'LOW' for item in blueprint['subjects'])
    assert all(
        item['fa1_evidence']['records'][0]['normalized_score'] == 11.99
        for item in blueprint['subjects']
    )
    assert blueprint['schedule'] == []
    assert any('Module 3' in item for item in blueprint['blockers'])
    assert any('Module 4' in item for item in blueprint['blockers'])


def test_student_subject_roadmaps_match_each_students_complete_eligible_set(
    client,
    teacher_token,
):
    create_blueprint_test_student(client)
    scenario_counts = (1, 3, 5, 12)
    expected_by_student = {}
    factory = client.app.state.test_session_factory

    with factory() as db:
        teacher = db.query(Teacher).join(User).filter(User.email == 'teacher@example.com').one()
        for scenario_index, eligible_count in enumerate(scenario_counts):
            student = Student(
                student_id=f'SYNTHETIC-ROADMAP-{scenario_index}',
                full_name=f'Synthetic roadmap student {scenario_index}',
                section='A',
                risk_level='LOW',
                risk_probability=0.1,
            )
            db.add(student)
            db.flush()
            source_filename = f'SYNTHETIC_test-recovery-fixture-{scenario_index}.csv'
            import_batch = AcademicDataImportBatch(
                dataset_type='marks',
                source_filename=source_filename,
                file_checksum=f'{scenario_index + 1:064x}',
                imported_by_user_id=teacher.user_id,
            )
            db.add(import_batch)
            db.flush()
            expected_subject_ids = set()
            subject_specs = [
                (index, 10, True)
                for index in range(eligible_count)
            ]
            subject_specs.extend([
                (eligible_count, 12, True),
                (eligible_count + 1, 1, False),
            ])

            for subject_index, score, maximum_verified in reversed(subject_specs):
                code = f'SYN-{scenario_index}-{subject_index}'
                subject = Subject(
                    code=code,
                    name=f'Synthetic {scenario_index} subject {subject_index}',
                    department='Science',
                )
                db.add(subject)
                db.flush()
                course = Course(
                    name=f'Synthetic {scenario_index} course {subject_index}',
                    code=f'{code}-A',
                    section='A',
                    subject_id=subject.id,
                    teacher_id=teacher.id,
                )
                db.add(course)
                db.flush()
                assessment = Assessment(
                    course_id=course.id,
                    title=f'SYNTHETIC TEST FA-1 {code}',
                    maximum_marks=25,
                    maximum_marks_verified=maximum_verified,
                    import_batch_id=import_batch.id,
                )
                db.add(assessment)
                db.flush()
                db.add(Mark(
                    student_id=student.id,
                    assessment_id=assessment.id,
                    score=score,
                    recorded_at=datetime(2026, 8, 1) + timedelta(days=subject_index),
                    import_batch_id=import_batch.id,
                ))
                if maximum_verified and score < 12:
                    expected_subject_ids.add(subject.id)

            expected_by_student[student.id] = (expected_subject_ids, source_filename)
        db.commit()

    for student_id, (expected_subject_ids, source_filename) in expected_by_student.items():
        response = request_recovery_blueprint(client, teacher_token, student_id)

        assert response.status_code == 201, response.text
        blueprint = response.json()['plan']['blueprint']
        generated_subject_ids = {
            item['subject_id'] for item in blueprint['subjects']
        }
        assert generated_subject_ids == expected_subject_ids
        assert len(blueprint['subjects']) == len(expected_subject_ids)
        assert all(
            item['fa1_evidence']['records'][0]['source_filenames'] == [source_filename]
            for item in blueprint['subjects']
        )
        with factory() as db:
            persisted = db.query(SupportPlanVersion).filter_by(
                student_id=student_id,
            ).one()
            persisted_subject_ids = {
                item['subject_id']
                for item in persisted.plan_json['blueprint']['subjects']
            }
        assert persisted_subject_ids == expected_subject_ids


def login(client, email, password):
    response = client.post('/api/v1/auth/login', json={'email': email, 'password': password})
    assert response.status_code == 200, response.text
    return response.json()['access_token']


def test_blueprint_eligibility_uses_strict_normalized_threshold_and_audits_triggers(
    client,
    teacher_token,
):
    student_id = create_blueprint_test_student(client)
    low_subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'LOW-50',
        [(22, 50, True, None, datetime(2026, 1, 2))],
    )
    response = request_recovery_blueprint(client, teacher_token, student_id, [low_subject_id])

    assert response.status_code == 201, response.text
    eligibility = response.json()['plan']['blueprint']['subjects'][0]['eligibility']
    assert eligibility['eligible'] is True
    assert eligibility['status'] == 'LOW'
    assert eligibility['support_triggers'] == ['LOW_SCORE']
    assert eligibility['official_failure_status'] is None
    assert eligibility['latest_evidence'][0]['normalized_score'] == 11
    with client.app.state.test_session_factory() as db:
        audit = db.query(AuditLog).filter_by(action='recovery_blueprint.generated').one()
        detail = audit.detail['subject_eligibility'][0]
        assert detail['support_triggers'] == ['LOW_SCORE']
        assert detail['official_failure_status'] is None


def test_blueprint_rejects_exact_twelve_of_twenty_five(client, teacher_token):
    student_id = create_blueprint_test_student(client)
    subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'BOUNDARY',
        [(12, 25, True, None, datetime(2026, 1, 2))],
    )

    response = request_recovery_blueprint(client, teacher_token, student_id, [subject_id])

    assert response.status_code == 422
    evidence = response.json()['detail']['subjects'][0]
    assert evidence['status'] == 'NOT_LOW'
    assert evidence['support_triggers'] == []
    assert evidence['evidence'][0]['normalized_score'] == 12


def test_blueprint_accepts_eleven_point_nine_nine_as_below_twelve(client, teacher_token):
    student_id = create_blueprint_test_student(client)
    subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'BOUNDARY-LOW',
        [(11.99, 25, True, None, datetime(2026, 1, 2))],
    )

    response = request_recovery_blueprint(client, teacher_token, student_id)

    assert response.status_code == 201, response.text
    subject = next(
        item for item in response.json()['plan']['blueprint']['subjects']
        if item['subject_id'] == subject_id
    )
    assert subject['eligibility']['status'] == 'LOW'
    assert subject['eligibility']['support_triggers'] == ['LOW_SCORE']
    assert subject['fa1_evidence']['records'][0]['normalized_score'] == 11.99


def test_blueprint_eligibility_uses_fa1_not_a_later_failed_assessment(client, teacher_token):
    student_id = create_blueprint_test_student(client)
    subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'FA1-SCOPE',
        [],
        record_fa1=14,
        record_observed_at=datetime(2026, 10, 1),
    )
    with client.app.state.test_session_factory() as db:
        course = db.query(Course).filter_by(subject_id=subject_id).one()
        assessment = Assessment(
            course_id=course.id,
            title='FA-2',
            maximum_marks=25,
            maximum_marks_verified=True,
        )
        db.add(assessment)
        db.flush()
        db.add(Mark(
            student_id=student_id,
            assessment_id=assessment.id,
            score=4,
            official_result='FAIL',
            recorded_at=datetime(2026, 10, 10),
        ))
        db.commit()

    response = request_recovery_blueprint(client, teacher_token, student_id, [subject_id])

    assert response.status_code == 422
    evidence = response.json()['detail']['subjects'][0]
    assert evidence['status'] == 'INSUFFICIENT_EVIDENCE'
    assert evidence['support_triggers'] == []
    assert evidence['evidence'] == []
    assert 'No first-internal or FA-1 evidence' in evidence['reason']


def test_official_failure_does_not_make_subject_score_eligible_for_blueprint(client, teacher_token):
    student_id = create_blueprint_test_student(client)
    subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'OFFICIAL-FAIL',
        [(24, 50, True, 'FAIL', datetime(2026, 1, 2))],
    )

    response = request_recovery_blueprint(client, teacher_token, student_id, [subject_id])

    assert response.status_code == 422, response.text
    evidence = next(
        item for item in response.json()['detail']['subjects']
        if item['subject_id'] == subject_id
    )
    assert evidence['eligible'] is False
    assert evidence['status'] == 'NOT_LOW'
    assert evidence['support_triggers'] == ['OFFICIAL_FAIL']
    assert evidence['official_failure_status'] == 'FAIL'
    with client.app.state.test_session_factory() as db:
        assert db.query(SupportPlanVersion).filter_by(student_id=student_id).count() == 0
        assert db.query(AuditLog).filter_by(action='recovery_blueprint.generated').count() == 0


@pytest.mark.parametrize(
    ('code', 'assessments', 'record_fa1', 'attendance_only', 'expected_status'),
    [
        ('MISSING', (), None, True, 'INSUFFICIENT_EVIDENCE'),
        ('UNVERIFIED', ((10, 25, False, None, datetime(2026, 1, 2)),), 14, False, 'INSUFFICIENT_EVIDENCE'),
        (
            'AMBIGUOUS',
            (
                (10, 25, True, None, datetime(2026, 1, 2)),
                (13, 25, True, None, datetime(2026, 1, 2)),
            ),
            14,
            False,
            'INSUFFICIENT_EVIDENCE',
        ),
        ('OFFICIAL-PASS', ((24, 50, True, 'PASS', datetime(2026, 1, 2)),), 14, False, 'NOT_LOW'),
    ],
)
def test_blueprint_rejects_insufficient_evidence_and_official_pass(
    client,
    teacher_token,
    code,
    assessments,
    record_fa1,
    attendance_only,
    expected_status,
):
    student_id = create_blueprint_test_student(client)
    subject_id = add_blueprint_test_subject(
        client,
        student_id,
        code,
        assessments,
        record_fa1=record_fa1,
        attendance_only=attendance_only,
    )

    response = request_recovery_blueprint(client, teacher_token, student_id, [subject_id])

    assert response.status_code == 422
    reported = next(
        item for item in response.json()['detail']['subjects']
        if item['subject_id'] == subject_id
    )
    assert reported['status'] == expected_status
    assert reported['support_triggers'] == []
    with client.app.state.test_session_factory() as db:
        assert db.query(SupportPlanVersion).filter_by(student_id=student_id).count() == 0
        assert db.query(AuditLog).filter_by(action='recovery_blueprint.generated').count() == 0


def test_blueprint_includes_every_eligible_subject_and_reports_boundary_subjects(client, teacher_token):
    student_id = create_blueprint_test_student(client)
    eligible_subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'MIXED-LOW',
        [(10, 25, True, None, datetime(2026, 1, 2))],
        add_approved_topic=True,
    )
    ineligible_subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'MIXED-BOUNDARY',
        [(12, 25, True, None, datetime(2026, 1, 2))],
    )

    response = request_recovery_blueprint(client, teacher_token, student_id)

    assert response.status_code == 201, response.text
    blueprint = response.json()['plan']['blueprint']
    assert [item['subject_id'] for item in blueprint['subjects']] == [eligible_subject_id]
    boundary = next(
        item for item in blueprint['subject_evidence']
        if item['subject_id'] == ineligible_subject_id
    )
    assert boundary['eligible'] is False
    assert boundary['status'] == 'NOT_LOW'
    with client.app.state.test_session_factory() as db:
        assert db.query(SupportPlanVersion).filter_by(student_id=student_id).count() == 1
        assert db.query(TopicLearningAssignment).filter_by(student_id=student_id).count() == 0
        assert db.query(AuditLog).filter_by(action='recovery_blueprint.generated').count() == 1


def test_blueprint_rejects_subject_outside_authenticated_teachers_scope(client, teacher_token):
    student_id = create_blueprint_test_student(client)
    unauthorized_subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'OTHER-TEACHER',
        [(10, 25, True, None, datetime(2026, 1, 2))],
        teacher_authorized=False,
    )

    response = request_recovery_blueprint(
        client,
        teacher_token,
        student_id,
        [unauthorized_subject_id],
    )

    assert response.status_code == 422
    assert unauthorized_subject_id not in {
        item['subject_id'] for item in response.json()['detail']['subjects']
    }

    assigned_subject_id = add_blueprint_test_subject(
        client,
        student_id,
        'AUTHORIZED-SUBJECT',
        [(10, 25, True, None, datetime(2026, 1, 2))],
    )
    unauthorized_student_id = client.app.state.test_other_student_id
    student_response = request_recovery_blueprint(
        client,
        teacher_token,
        unauthorized_student_id,
        [assigned_subject_id],
    )
    assert student_response.status_code == 404
    with client.app.state.test_session_factory() as db:
        assert db.query(SupportPlanVersion).filter_by(student_id=student_id).count() == 0
        assert db.query(AuditLog).filter_by(action='recovery_blueprint.generated').count() == 0


def test_draft_generation_is_idempotent_and_evidence_grounded(client, teacher_token):
    student_id = link_teacher_and_add_evidence(client)
    path = f'/api/v1/students/{student_id}/recovery-plans'

    first = client.post(path, json={'generation_key': 'same-evidence-key'}, headers=auth(teacher_token))
    assert first.status_code == 201, first.text
    body = first.json()
    assert body['status'] == 'DRAFT'
    assert body['plan']['tasks'][0]['subject'] == 'Biology'
    assert body['plan']['tasks'][0]['scheduled_at'] is None
    assert body['plan']['tasks'][0]['deadline'] is None
    assert body['plan']['tasks'][0]['topic'] is None
    assert 'NOT_INFERRED_FROM_AGGREGATE_MARKS' == body['plan']['tasks'][0]['field_provenance']['mastery_criteria']
    assert body['evidence']['academic_records']
    assert body['evidence']['generation']['generator'] == 'deterministic_evidence_rules'
    activity = body['plan']['tasks'][0]['activity']
    assert 'not a verified lesson from the course' in activity['lesson']
    assert all(label in activity['exercise'] for label in ('Observation', 'Limit', 'Question'))
    assert len(activity['steps']) >= 3

    repeated = client.post(path, json={'generation_key': 'same-evidence-key'}, headers=auth(teacher_token))
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()['id'] == body['id']
    with client.app.state.test_session_factory() as db:
        assert db.query(func.count()).select_from(SupportPlanVersion).filter_by(student_id=student_id).scalar() == 1


def test_empty_evidence_produces_no_fabricated_tasks(client, teacher_token):
    student_id = link_teacher_and_add_evidence(client, include_evidence=False)
    response = client.post(
        f'/api/v1/students/{student_id}/recovery-plans',
        json={'generation_key': 'no-evidence-key'},
        headers=auth(teacher_token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['plan']['tasks'] == []
    assert any('No verified academic' in item for item in body['evidence']['missing_information'])
    assert body['evidence']['model_outputs']['risk_prediction'] is None

    no_llm_summary, no_llm_tasks, no_llm_evidence = make_draft_plan({
        'academic_records': [],
        'model_outputs': {'risk_prediction': None},
        'missing_information': ['no records'],
        'provenance': {},
        '_by_subject': {},
    })
    assert no_llm_summary.startswith('No task can be generated')
    assert no_llm_tasks == []
    assert 'deterministic_evidence_rules' == no_llm_evidence['generation']['generator']
    assert 'no_structured_evidence_constrained_recovery_provider' in no_llm_evidence['generation']['llm_status']


def test_recovery_blueprint_is_source_grounded_and_draft_assignments_are_hidden(
    client,
    teacher_token,
    student_token,
    monkeypatch,
):
    from backend.app import main as main_module

    student_id = link_teacher_and_add_evidence(client)
    factory = client.app.state.test_session_factory
    with factory() as db:
        subject = db.query(Subject).filter_by(code='BIO-101').one()
        student = db.query(Student).filter_by(id=student_id).one()
        db.add(SubjectFocusPolicy(
            version=1,
            threshold_score=12,
            threshold_maximum=25,
            change_reason='Test subject support policy',
        ))
        existing_record = db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student.id,
            subject_id=subject.id,
            academic_period='2026-T1',
        ).one()
        existing_record.fa1 = 14
        existing_record.fa1_observed_at = datetime(2026, 8, 8)
        db.query(Assessment).filter_by(course_id=student.course_id).one().maximum_marks_verified = True
        db.add(StudentSubjectAcademicRecord(
            student_id=student.id,
            subject_id=subject.id,
            academic_period='2026-S1',
            fa1=14,
            fa1_observed_at=datetime(2026, 8, 9),
        ))
        module = SubjectModule(subject_id=subject.id, module_number=3, title='Cell Systems')
        db.add(module)
        db.flush()
        module_four = SubjectModule(subject_id=subject.id, module_number=4, title='Cell Signaling')
        db.add(module_four)
        db.flush()
        resource = SubjectModuleResource(
            module_id=module.id,
            subject_id=subject.id,
            teacher_id=db.query(Teacher).one().id,
            title='Cell Systems notes',
            original_filename='cell-systems.txt',
            storage_path='not-read-by-this-test',
            file_type='txt',
            file_size=100,
            content_hash='a' * 64,
            processing_status='PROCESSED',
            extracted_text='Cell membranes regulate transport across cells.',
        )
        db.add(resource)
        db.flush()
        module_four_resource = SubjectModuleResource(
            module_id=module_four.id,
            subject_id=subject.id,
            teacher_id=db.query(Teacher).one().id,
            title='Cell Signaling notes',
            original_filename='cell-signaling.txt',
            storage_path='not-read-by-this-test',
            file_type='txt',
            file_size=100,
            content_hash='c' * 64,
            processing_status='PROCESSED',
            extracted_text='Cell signals coordinate responses between cells.',
        )
        db.add(module_four_resource)
        db.flush()
        topic = ApprovedTopic(
            subject_id=subject.id,
            module_id=module.id,
            source_resource_id=resource.id,
            teacher_id=db.query(Teacher).one().id,
            title='Cell membrane transport',
            summary='Explain transport across the cell membrane.',
            learning_objectives=['Describe membrane transport.'],
            concepts=['diffusion', 'osmosis'],
            approval_status='APPROVED',
            approved_by_user_id=db.query(User).filter_by(email='teacher@example.com').one().id,
            source_provenance={'resource_id': resource.id, 'order_uncertain': False},
        )
        unapproved_topic = ApprovedTopic(
            subject_id=subject.id,
            module_id=module.id,
            source_resource_id=resource.id,
            teacher_id=db.query(Teacher).one().id,
            title='Unapproved membrane topic',
            summary='This topic remains a draft until a teacher approves it.',
            learning_objectives=['Review the draft topic.'],
            concepts=['draft'],
            approval_status='DRAFT',
            approved_by_user_id=None,
            source_provenance={'resource_id': resource.id},
        )
        module_four_topic = ApprovedTopic(
            subject_id=subject.id,
            module_id=module_four.id,
            source_resource_id=module_four_resource.id,
            teacher_id=db.query(Teacher).one().id,
            title='Cell signaling',
            summary='Explain cell signaling pathways.',
            learning_objectives=['Describe cell signaling.'],
            concepts=['signaling'],
            approval_status='APPROVED',
            approved_by_user_id=db.query(User).filter_by(email='teacher@example.com').one().id,
            source_provenance={'resource_id': module_four_resource.id, 'order_uncertain': False},
        )
        db.add_all([topic, unapproved_topic, module_four_topic])
        db.commit()
        topic_id = topic.id
        module_four_topic_id = module_four_topic.id
        topic_ids = [topic_id, module_four_topic_id]
        unapproved_topic_id = unapproved_topic.id
        subject_id = subject.id
        resource_id = resource.id

    class BlueprintProvider:
        provider_name = 'test-blueprint-provider'

        def generate_recovery_blueprint(self, context):
            supplied = context['approved_source_backed_topics']
            return json.dumps({
                'topics': [{
                    'topic_id': item['topic_id'],
                    'priority': 'HIGH',
                    'suggested_minutes': 30,
                } for item in supplied],
            })

    monkeypatch.setattr(main_module, 'build_provider', lambda _: BlueprintProvider())
    response = client.post(
        f'/api/v1/students/{student_id}/recovery-blueprints',
        json={
            'academic_period': '2026-S1',
            'exam_date': (date.today() + timedelta(days=10)).isoformat(),
            'subject_ids': [subject_id],
            'acknowledge_no_syllabus': True,
            'available_minutes_per_day': 60,
        },
        headers=auth(teacher_token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['status'] == 'DRAFT'
    blueprint = body['plan']['blueprint']
    fa1_record = blueprint['subjects'][0]['fa1_evidence']['records'][0]
    assert fa1_record['assessment'] == 'SYNTHETIC TEST FA1'
    assert fa1_record['maximum_marks'] == 100
    assert fa1_record['maximum_marks_verified'] is True
    assert fa1_record['normalized_score'] == 11.25
    assert fa1_record['mark_id'] is not None
    assert [item['topic_id'] for item in blueprint['subjects'][0]['topics']] == topic_ids
    assert {item['stage'] for item in blueprint['subjects'][0]['topics']} == {'MODULE_3', 'MODULE_4'}
    assert blueprint['schedule']
    assert {item['stage'] for item in blueprint['schedule']} == {'MODULE_3', 'MODULE_4', 'REVISION_ASSESSMENT'}
    assert max(
        item['scheduled_date'] for item in blueprint['schedule'] if item['stage'] == 'MODULE_3'
    ) < min(
        item['scheduled_date'] for item in blueprint['schedule'] if item['stage'] == 'MODULE_4'
    )
    assert max(
        item['scheduled_date'] for item in blueprint['schedule'] if item['stage'] == 'MODULE_4'
    ) < min(
        item['scheduled_date'] for item in blueprint['schedule'] if item['stage'] == 'REVISION_ASSESSMENT'
    )
    assert blueprint['subjects'][0]['topics'][0]['priority'] == 'HIGH'
    assert blueprint['subjects'][0]['topics'][0]['assessment']['status'] == 'NOT_GENERATED'

    with factory() as db:
        assignments = db.query(TopicLearningAssignment).filter(
            TopicLearningAssignment.student_id == student_id,
            TopicLearningAssignment.approved_topic_id.in_(topic_ids),
        ).all()
        assert len(assignments) == 2
        assert all(assignment.status == 'DRAFT' for assignment in assignments)
        assignment = db.query(TopicLearningAssignment).filter_by(
            student_id=student_id,
            approved_topic_id=topic_id,
        ).one()
        assert assignment.status == 'DRAFT'
        assignment_id = assignment.id
        assert db.query(TopicLearningAssignment).filter_by(
            student_id=student_id,
            approved_topic_id=unapproved_topic_id,
        ).count() == 0
    assert client.get(
        '/api/v1/students/me/topic-learning-assignments',
        headers=auth(student_token),
    ).json() == []
    hidden = client.get(
        f'/api/v1/topic-learning-assignments/{assignment_id}/study-session',
        headers=auth(student_token),
    )
    assert hidden.status_code == 404

    approval = client.post(
        f"/api/v1/recovery-plans/{body['id']}/approve",
        json={'reason': 'Review assessment and scope first.'},
        headers=auth(teacher_token),
    )
    assert approval.status_code == 409
    assert approval.json()['detail']['blockers']

    reviewed = client.patch(
        f"/api/v1/recovery-plans/{body['id']}/blueprint",
        json={
            'topic_decisions': [
                {'topic_id': item, 'decision': 'INCLUDE'} for item in topic_ids
            ],
            'syllabus_reviewed': True,
            'schedule': [
                {
                    key: item[key]
                    for key in (
                        'scheduled_date', 'activity', 'stage', 'estimated_minutes',
                        'topic_id', 'subject_id',
                    )
                }
                for item in blueprint['schedule']
            ],
        },
        headers=auth(teacher_token),
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()['plan']['blueprint']['subjects'][0]['topics'][0]['decision'] == 'INCLUDE', reviewed.json()
    assert reviewed.json()['plan']['blueprint']['syllabus']['reviewed'] is True
    with factory() as db:
        for assignment in db.query(TopicLearningAssignment).filter(
            TopicLearningAssignment.student_id == student_id,
            TopicLearningAssignment.approved_topic_id.in_(topic_ids),
        ).all():
            assessment = LearningAssessment(
                assignment_id=assignment.id,
                version=1,
                status='PUBLISHED',
                question_count=20,
                validation_results={'valid': True, 'issues': []},
                published_at=datetime.utcnow(),
            )
            db.add(assessment)
            db.flush()
            for index in range(20):
                db.add(AssessmentQuestion(
                    assessment_id=assessment.id,
                    subject_id=subject_id,
                    module_id=assignment.module_id,
                    topic_id=assignment.approved_topic_id,
                    question_type='MCQ' if index < 15 else 'SHORT_ANSWER',
                    question=f'Source-grounded test question {index + 1}',
                    options=['A', 'B', 'C', 'D'] if index < 15 else [],
                    correct_answer='A' if index < 15 else 'Supported answer',
                    explanation='Supported by the approved source.',
                    learning_objective=assignment.learning_objective,
                    source_reference=(
                        f"resource:{db.get(ApprovedTopic, assignment.approved_topic_id).source_resource_id}"
                    ),
                    rubric={'criteria': 'source-backed'} if index >= 15 else {},
                    source_provenance={
                        'resource_id': db.get(ApprovedTopic, assignment.approved_topic_id).source_resource_id,
                    },
                ))
        db.commit()

    refreshed = client.patch(
        f"/api/v1/recovery-plans/{body['id']}/blueprint",
        json={'syllabus_reviewed': True},
        headers=auth(teacher_token),
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['plan']['blueprint']['subjects'][0]['topics'][0]['decision'] == 'INCLUDE'
    assert refreshed.json()['plan']['blueprint']['syllabus']['reviewed'] is True
    approved = client.post(
        f"/api/v1/recovery-plans/{body['id']}/approve",
        json={'reason': 'Reviewed source-backed topic and assessment.'},
        headers=auth(teacher_token),
    )
    assert approved.status_code == 200, approved.text
    published = client.post(
        f"/api/v1/recovery-plans/{body['id']}/publish",
        json={'reason': 'Publish reviewed future study work.'},
        headers=auth(teacher_token),
    )
    assert published.status_code == 200, published.text
    student_assignments = client.get(
        '/api/v1/students/me/topic-learning-assignments',
        headers=auth(student_token),
    )
    assert student_assignments.status_code == 200, student_assignments.text
    assert len(student_assignments.json()) == 2
    assert all(item['status'] == 'ACTIVE' for item in student_assignments.json())

    student_tasks_before_revision = client.get(
        f'/api/v1/students/{student_id}/recovery-tasks',
        headers=auth(student_token),
    )
    assert student_tasks_before_revision.status_code == 200, student_tasks_before_revision.text
    original_tasks = student_tasks_before_revision.json()
    assert len(original_tasks) >= 2
    assert {task['stage'] for task in original_tasks} == {
        'MODULE_3',
        'MODULE_4',
        'REVISION_ASSESSMENT',
    }
    completed_task_id, in_progress_task_id = [task['id'] for task in original_tasks[:2]]
    completed_task_url = f'/api/v1/recovery-tasks/{completed_task_id}'
    assert client.patch(
        completed_task_url,
        json={'action': 'start', 'idempotency_key': 'blueprint-revision-start-complete'},
        headers=auth(student_token),
    ).status_code == 200
    assert client.post(
        f'{completed_task_url}/submit',
        json={'idempotency_key': 'blueprint-revision-submit-complete', 'response': 'Completed the assigned review.'},
        headers=auth(student_token),
    ).status_code == 200
    assert client.patch(
        f'{completed_task_url}/assess',
        json={
            'idempotency_key': 'blueprint-revision-assess-complete',
            'decision': 'complete',
            'feedback': 'Completion recorded.',
            'score': 90,
        },
        headers=auth(teacher_token),
    ).status_code == 200
    assert client.patch(
        f'/api/v1/recovery-tasks/{in_progress_task_id}',
        json={'action': 'start', 'idempotency_key': 'blueprint-revision-start-pending'},
        headers=auth(student_token),
    ).status_code == 200

    revision = client.post(
        f"/api/v1/recovery-plans/{body['id']}/blueprint/revisions",
        headers=auth(teacher_token),
    )
    assert revision.status_code == 201, revision.text
    assert revision.json()['version'] == 2
    revision_id = revision.json()['id']
    assert revision.json()['blueprint_group_id'] == body['blueprint_group_id']
    assert revision.json()['plan']['blueprint']['subjects'][0]['topics'][0]['decision'] == 'PENDING'
    reviewed_revision = client.patch(
        f'/api/v1/recovery-plans/{revision_id}/blueprint',
        json={
            'topic_decisions': [
                {'topic_id': item, 'decision': 'INCLUDE'} for item in topic_ids
            ],
            'syllabus_reviewed': True,
        },
        headers=auth(teacher_token),
    )
    assert reviewed_revision.status_code == 200, reviewed_revision.text
    approved_revision = client.post(
        f'/api/v1/recovery-plans/{revision_id}/approve',
        json={'reason': 'Review future work while preserving active tasks.'},
        headers=auth(teacher_token),
    )
    assert approved_revision.status_code == 200, approved_revision.text
    published_revision = client.post(
        f'/api/v1/recovery-plans/{revision_id}/publish',
        json={'reason': 'Publish the revised future schedule.'},
        headers=auth(teacher_token),
    )
    assert published_revision.status_code == 200, published_revision.text
    with factory() as db:
        group_plan = db.query(SupportPlanVersion).filter_by(id=body['id']).one()
        assert group_plan.subject_id is None
        assert group_plan.is_blueprint_group is True
        assert group_plan.status == 'PUBLISHED'
        group_tasks = db.query(RecoveryTask).filter_by(
            source_blueprint_plan_id=group_plan.id,
        ).all()
        assert len(group_tasks) == len(blueprint['schedule'])
        assert all(task.subject_id == subject_id for task in group_tasks)
        assert {task.scheduled_date.isoformat() for task in group_tasks} == {
            item['scheduled_date'] for item in blueprint['schedule']
        }
        assert len({task.source_schedule_entry_key for task in group_tasks}) == len(group_tasks)
        published_child = db.query(SupportPlanVersion).filter_by(
            source_blueprint_plan_id=group_plan.id,
            subject_id=subject_id,
        ).one()
        assert published_child.status == 'PUBLISHED'
        assert published_child.subject_scope_review_required is False
        assert db.query(RecoveryTaskEvent).join(RecoveryTask).filter(
            RecoveryTask.roadmap_version_id == published_child.id,
            RecoveryTaskEvent.subject_id == subject_id,
            RecoveryTaskEvent.event_type == 'PUBLISHED',
        ).count() == len(group_tasks)
        original_tasks_by_id = {
            task.id: task
            for task in db.query(RecoveryTask).filter_by(roadmap_version_id=published_child.id).all()
        }
        assert original_tasks_by_id[completed_task_id].status == 'COMPLETED'
        assert original_tasks_by_id[in_progress_task_id].status == 'IN_PROGRESS'
        assert all(
            task.status == 'SUPERSEDED'
            for task in original_tasks_by_id.values()
            if task.id not in {completed_task_id, in_progress_task_id}
        )
        assert db.query(RecoveryTaskEvent).filter_by(
            event_type='SUPERSEDED',
            subject_id=subject_id,
        ).count() == len(group_tasks) - 2
        revision_child = db.query(SupportPlanVersion).filter_by(
            source_blueprint_plan_id=revision_id,
            subject_id=subject_id,
        ).one()
        assert revision_child.status == 'PUBLISHED'
        assert db.query(RecoveryTask).filter_by(roadmap_version_id=revision_child.id).count() == len(blueprint['schedule'])

    student_subject_plans = client.get(
        f'/api/v1/students/{student_id}/recovery-plans',
        headers=auth(student_token),
    )
    assert student_subject_plans.status_code == 200
    assert len(student_subject_plans.json()) == 2
    assert all(plan['subject_id'] == subject_id for plan in student_subject_plans.json())

    published_tasks = client.get(
        f'/api/v1/students/{student_id}/recovery-tasks',
        headers=auth(student_token),
    )
    assert published_tasks.status_code == 200
    assert len(published_tasks.json()) == len(blueprint['schedule']) + 2
    assert all(task['subject_id'] == subject_id for task in published_tasks.json())
    assert {task['id']: task['status'] for task in published_tasks.json()}[completed_task_id] == 'COMPLETED'
    assert {task['id']: task['status'] for task in published_tasks.json()}[in_progress_task_id] == 'IN_PROGRESS'
    assert all(task['status'] != 'SUPERSEDED' for task in published_tasks.json())

    repeated_publish = client.post(
        f"/api/v1/recovery-plans/{body['id']}/publish",
        json={'reason': 'Idempotent retry.'},
        headers=auth(teacher_token),
    )
    assert repeated_publish.status_code == 200
    with factory() as db:
        assert db.query(RecoveryTask).filter_by(
            source_blueprint_plan_id=body['id'],
        ).count() == len(blueprint['schedule'])
    repeated_revision_publish = client.post(
        f'/api/v1/recovery-plans/{revision_id}/publish',
        json={'reason': 'Idempotent revision retry.'},
        headers=auth(teacher_token),
    )
    assert repeated_revision_publish.status_code == 200
    with factory() as db:
        assert db.query(RecoveryTask).filter_by(
            source_blueprint_plan_id=revision_id,
        ).count() == len(blueprint['schedule'])
        assert db.query(RecoveryTask).filter_by(id=completed_task_id).one().status == 'COMPLETED'
        assert db.query(RecoveryTask).filter_by(id=in_progress_task_id).one().status == 'IN_PROGRESS'

    with factory() as db:
        assignment = db.query(TopicLearningAssignment).filter_by(
            student_id=student_id,
            approved_topic_id=topic_id,
        ).one()
        assert assignment.status == 'ACTIVE'


def test_teacher_review_approval_publication_and_student_visibility(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    create_path = f'/api/v1/students/{student_id}/recovery-plans'
    created = client.post(create_path, json={'generation_key': 'publish-plan-key'}, headers=auth(teacher_token))
    assert created.status_code == 201, created.text
    plan_id = created.json()['id']

    assert client.get(create_path, headers=auth(student_token)).json() == []
    approve_as_student = client.post(
        f'/api/v1/recovery-plans/{plan_id}/approve', json={}, headers=auth(student_token),
    )
    assert approve_as_student.status_code == 403

    approved = client.post(
        f'/api/v1/recovery-plans/{plan_id}/approve',
        json={'reason': 'Reviewed against verified assessment records.'},
        headers=auth(teacher_token),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()['status'] == 'APPROVED'
    published = client.post(
        f'/api/v1/recovery-plans/{plan_id}/publish',
        json={'reason': 'Publish after review.'},
        headers=auth(teacher_token),
    )
    assert published.status_code == 200, published.text
    assert published.json()['status'] == 'PUBLISHED'

    student_plans = client.get(create_path, headers=auth(student_token))
    assert len(student_plans.json()) == 1
    assert student_plans.json()[0]['id'] != plan_id
    assert student_plans.json()[0]['subject_id'] is not None
    tasks = client.get(f'/api/v1/students/{student_id}/recovery-tasks', headers=auth(student_token))
    assert tasks.status_code == 200, tasks.text
    assert len(tasks.json()) == 1
    assert tasks.json()[0]['scheduled_at'] is None
    assert tasks.json()[0]['evidence_refs']
    assert {item['kind'] for item in tasks.json()[0]['evidence']} == {
        'student_subject_academic_record', 'assessment_mark',
    }
    my_tasks = client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token))
    assert my_tasks.status_code == 200, my_tasks.text
    assert my_tasks.json()[0]['id'] == tasks.json()[0]['id']

    with client.app.state.test_session_factory() as db:
        task = db.query(RecoveryTask).one()
        events = db.query(RecoveryTaskEvent).filter_by(task_id=task.id).all()
        assert [event.event_type for event in events] == ['PUBLISHED']
        with pytest.raises(ValueError, match='immutable'):
            events[0].detail = {'tampered': True}
            db.commit()
        db.rollback()


def test_recovery_task_progress_is_isolated_between_subjects(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    factory = client.app.state.test_session_factory
    with factory() as db:
        student = db.query(Student).filter_by(id=student_id).one()
        teacher = db.query(Teacher).join(User).filter(User.email == 'teacher@example.com').one()
        second_subject = Subject(name='Computer Networks', code='NET-201', department='Science')
        db.add(second_subject)
        db.flush()
        second_course = Course(
            name='Computer Networks',
            code='NET-201-A',
            section='A',
            subject_id=second_subject.id,
            teacher_id=teacher.id,
        )
        db.add(second_course)
        db.flush()
        db.add(StudentSubjectAcademicRecord(
            student_id=student.id,
            subject_id=second_subject.id,
            academic_period='2026-S1',
            fa1=8,
            fa1_observed_at=datetime(2026, 8, 10),
        ))
        second_assessment = Assessment(
            course_id=second_course.id,
            title='Verified networks assessment',
            maximum_marks=25,
            maximum_marks_verified=True,
        )
        db.add(second_assessment)
        db.flush()
        db.add(Mark(
            student_id=student.id,
            assessment_id=second_assessment.id,
            score=8,
            recorded_at=datetime(2026, 8, 10),
        ))
        second_subject_id = second_subject.id
        db.commit()

    create_path = f'/api/v1/students/{student_id}/recovery-plans'
    created = client.post(
        create_path,
        json={'generation_key': 'two-subject-isolation-plan'},
        headers=auth(teacher_token),
    )
    assert created.status_code == 201, created.text
    group_plan_id = created.json()['id']
    assert created.json()['is_blueprint_group'] is True
    assert client.post(
        f'/api/v1/recovery-plans/{group_plan_id}/approve',
        json={'reason': 'Approve subject-scoped evidence review.'},
        headers=auth(teacher_token),
    ).status_code == 200
    assert client.post(
        f'/api/v1/recovery-plans/{group_plan_id}/publish',
        json={'reason': 'Publish each subject as its own roadmap.'},
        headers=auth(teacher_token),
    ).status_code == 200

    tasks_response = client.get(
        f'/api/v1/students/{student_id}/recovery-tasks',
        headers=auth(student_token),
    )
    assert tasks_response.status_code == 200, tasks_response.text
    tasks_by_subject = {task['subject_id']: task for task in tasks_response.json()}
    assert len(tasks_by_subject) == 2
    assert second_subject_id in tasks_by_subject
    biology_task = next(task for task in tasks_by_subject.values() if task['subject_id'] != second_subject_id)
    networks_task = tasks_by_subject[second_subject_id]
    networks_before = {
        key: networks_task[key]
        for key in ('id', 'subject_id', 'status', 'progress_percent', 'roadmap_version_id')
    }

    task_url = f"/api/v1/recovery-tasks/{biology_task['id']}"
    assert client.patch(
        task_url,
        json={'action': 'start', 'idempotency_key': 'biology-task-start'},
        headers=auth(student_token),
    ).status_code == 200
    assert client.post(
        f'{task_url}/submit',
        json={'idempotency_key': 'biology-task-submit', 'response': 'Reviewed the Biology evidence.'},
        headers=auth(student_token),
    ).status_code == 200
    assessed = client.patch(
        f'{task_url}/assess',
        json={
            'idempotency_key': 'biology-task-assess',
            'decision': 'complete',
            'feedback': 'Biology task reviewed.',
            'score': 80,
        },
        headers=auth(teacher_token),
    )
    assert assessed.status_code == 200, assessed.text
    assert assessed.json()['subject_id'] == biology_task['subject_id']

    refreshed_tasks = client.get(
        f'/api/v1/students/{student_id}/recovery-tasks',
        headers=auth(student_token),
    )
    assert refreshed_tasks.status_code == 200, refreshed_tasks.text
    refreshed_by_id = {task['id']: task for task in refreshed_tasks.json()}
    assert refreshed_by_id[biology_task['id']]['status'] == 'COMPLETED'
    assert refreshed_by_id[networks_task['id']]['status'] == 'AVAILABLE'
    assert refreshed_by_id[networks_task['id']]['progress_percent'] == 0
    assert refreshed_by_id[networks_task['id']]['roadmap_version_id'] == networks_before['roadmap_version_id']

    with factory() as db:
        persisted_networks_task = db.query(RecoveryTask).filter_by(id=networks_task['id']).one()
        assert persisted_networks_task.status == networks_before['status']
        assert persisted_networks_task.progress_percent == networks_before['progress_percent']
        assert db.query(RecoveryTaskEvent).filter_by(task_id=biology_task['id']).count() == 4
        networks_events = db.query(RecoveryTaskEvent).filter_by(task_id=networks_task['id']).all()
        assert [event.event_type for event in networks_events] == ['PUBLISHED']
        assert all(event.subject_id == second_subject_id for event in networks_events)
        subject_plans = db.query(SupportPlanVersion).filter(
            SupportPlanVersion.student_id == student_id,
            SupportPlanVersion.is_blueprint_group.is_(False),
            SupportPlanVersion.status == 'PUBLISHED',
        ).all()
        plans_by_subject = {plan.subject_id: plan for plan in subject_plans}
        assert set(plans_by_subject) == {biology_task['subject_id'], second_subject_id}
        assert plans_by_subject[second_subject_id].id == networks_before['roadmap_version_id']


def test_teacher_student_ownership_and_assignment_boundaries(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    other_id = client.app.state.test_other_student_id
    student2_token = login(client, 'other@example.com', 'other-pass')

    unauthorized_generation = client.post(
        f'/api/v1/students/{other_id}/recovery-plans',
        json={'generation_key': 'wrong-teacher-key'},
        headers=auth(teacher_token),
    )
    assert unauthorized_generation.status_code == 404
    assert client.get(
        f'/api/v1/students/{other_id}/recovery-tasks',
        headers=auth(student_token),
    ).status_code == 404
    assert client.get(
        f'/api/v1/students/{student_id}/recovery-tasks',
        headers=auth(student2_token),
    ).status_code == 404


def test_task_transitions_idempotency_submission_feedback_and_timeline(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    create_path = f'/api/v1/students/{student_id}/recovery-plans'
    created = client.post(create_path, json={'generation_key': 'task-lifecycle-key'}, headers=auth(teacher_token))
    plan_id = created.json()['id']
    client.post(f'/api/v1/recovery-plans/{plan_id}/approve', json={}, headers=auth(teacher_token))
    client.post(f'/api/v1/recovery-plans/{plan_id}/publish', json={}, headers=auth(teacher_token))

    task = client.get(f'/api/v1/students/{student_id}/recovery-tasks', headers=auth(student_token)).json()[0]
    task_id = task['id']
    progress_url = f'/api/v1/recovery-tasks/{task_id}'

    invalid_progress = client.patch(
        progress_url,
        json={'action': 'progress', 'progress_percent': 30, 'idempotency_key': 'progress-early'},
        headers=auth(student_token),
    )
    assert invalid_progress.status_code == 409

    started = client.patch(
        progress_url,
        json={'action': 'start', 'idempotency_key': 'task-start-001'},
        headers=auth(student_token),
    )
    assert started.status_code == 200, started.text
    assert started.json()['status'] == 'IN_PROGRESS'
    assert client.patch(
        progress_url,
        json={'action': 'start', 'idempotency_key': 'task-start-001'},
        headers=auth(student_token),
    ).status_code == 200
    assert client.patch(
        progress_url,
        json={'action': 'progress', 'progress_percent': 50, 'idempotency_key': 'task-start-001'},
        headers=auth(student_token),
    ).status_code == 409

    submitted = client.post(
        f'{progress_url}/submit',
        json={'idempotency_key': 'task-submit-001', 'response': 'Reviewed the recorded evidence with my teacher.'},
        headers=auth(student_token),
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()['status'] == 'SUBMITTED'
    assert client.post(
        f'{progress_url}/submit',
        json={'idempotency_key': 'task-submit-001', 'response': 'Reviewed the recorded evidence with my teacher.'},
        headers=auth(student_token),
    ).status_code == 200

    assessed = client.patch(
        f'{progress_url}/assess',
        json={
            'idempotency_key': 'task-assess-001',
            'decision': 'complete',
            'feedback': 'Thank you for documenting the review.',
            'score': 90,
        },
        headers=auth(teacher_token),
    )
    assert assessed.status_code == 200, assessed.text
    assert assessed.json()['status'] == 'COMPLETED'
    assert assessed.json()['teacher_feedback'] == 'Thank you for documenting the review.'
    assert client.patch(
        f'{progress_url}/assess',
        json={
            'idempotency_key': 'task-assess-001',
            'decision': 'complete',
            'feedback': 'Thank you for documenting the review.',
            'score': 90,
        },
        headers=auth(teacher_token),
    ).status_code == 200
    conflicting_assessment_retry = client.patch(
        f'{progress_url}/assess',
        json={
            'idempotency_key': 'task-assess-001',
            'decision': 'needs_revision',
            'feedback': 'Different decision for the same key.',
            'score': 90,
        },
        headers=auth(teacher_token),
    )
    assert conflicting_assessment_retry.status_code == 409
    adaptation_response = client.get(
        f'/api/v1/teacher/recovery-adaptations?student_id={student_id}',
        headers=auth(teacher_token),
    )
    assert adaptation_response.status_code == 200, adaptation_response.text
    adaptation = adaptation_response.json()[0]
    assert adaptation['decision_type'] == 'INSUFFICIENT_EVIDENCE'
    assert adaptation['status'] == 'NO_CHANGE'
    assert adaptation['evidence']['score_is_mastery_evidence'] is False
    assert adaptation['evidence']['academic_evidence_refs']
    assert 'do not establish topic mastery' in adaptation['rationale']

    timeline = client.get(
        f'/api/v1/students/{student_id}/recovery-timeline',
        headers=auth(student_token),
    )
    assert timeline.status_code == 200, timeline.text
    my_timeline = client.get('/api/v1/students/me/recovery-timeline', headers=auth(student_token))
    assert my_timeline.status_code == 200, my_timeline.text
    assert my_timeline.json()['items'] == timeline.json()['items']
    assert {item['title'] for item in timeline.json()['items']} >= {
        'Recovery roadmap v1', 'Published', 'Started', 'Submitted', 'Assessed',
    }
    with client.app.state.test_session_factory() as db:
        assert db.query(RecoveryTaskEvent).filter_by(task_id=task_id).count() == 4
        assert db.query(RecoveryAdaptationDecision).filter_by(task_id=task_id).count() == 1
        generated_audit = db.query(AuditLog).filter_by(action='recovery_adaptation.generated').one()
        assert generated_audit.resource_id == str(adaptation['id'])
        assert generated_audit.detail['actor_role'] == 'TEACHER'
        assert generated_audit.detail['new_state'] == 'NO_CHANGE'
        assert generated_audit.detail['reason'] == adaptation['rationale']


def test_student_recovery_drafts_persist_submit_and_allow_revision(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    created = client.post(
        f'/api/v1/students/{student_id}/recovery-plans',
        json={'generation_key': 'draft-workflow-plan'},
        headers=auth(teacher_token),
    )
    plan_id = created.json()['id']
    assert client.post(f'/api/v1/recovery-plans/{plan_id}/approve', json={}, headers=auth(teacher_token)).status_code == 200
    assert client.post(f'/api/v1/recovery-plans/{plan_id}/publish', json={}, headers=auth(teacher_token)).status_code == 200
    task = client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token)).json()[0]
    task_url = f'/api/v1/recovery-tasks/{task["id"]}'
    assert task['activity']['lesson']
    assert task['activity']['exercise']
    assert task['activity']['steps']
    assert task['activity']['submission_format']

    started = client.patch(
        task_url, json={'action': 'start', 'idempotency_key': 'student-activity-start'}, headers=auth(student_token),
    )
    assert started.status_code == 200, started.text
    other_token = login(client, 'other@example.com', 'other-pass')
    forbidden_draft = client.put(
        f'{task_url}/draft',
        json={'idempotency_key': 'cross-student-draft', 'response': 'private work'},
        headers=auth(other_token),
    )
    assert forbidden_draft.status_code == 404

    saved = client.put(
        f'{task_url}/draft',
        json={'idempotency_key': 'student-draft-save-1', 'response': 'Evidence review notes, version one.'},
        headers=auth(student_token),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()['student_response'] == 'Evidence review notes, version one.'
    retry = client.put(
        f'{task_url}/draft',
        json={'idempotency_key': 'student-draft-save-1', 'response': 'Evidence review notes, version one.'},
        headers=auth(student_token),
    )
    assert retry.status_code == 200, retry.text
    conflicting_retry = client.put(
        f'{task_url}/draft',
        json={'idempotency_key': 'student-draft-save-1', 'response': 'Changed content under the same key.'},
        headers=auth(student_token),
    )
    assert conflicting_retry.status_code == 409
    listed = client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token)).json()[0]
    assert listed['student_response'] == 'Evidence review notes, version one.'
    submitted = client.post(
        f'{task_url}/submit',
        json={'idempotency_key': 'student-submit-first', 'response': listed['student_response']},
        headers=auth(student_token),
    )
    assert submitted.status_code == 200, submitted.text
    revision = client.patch(
        f'{task_url}/assess',
        json={
            'idempotency_key': 'teacher-revision-request',
            'decision': 'needs_revision',
            'feedback': 'Please include the evidence reference and one follow-up question.',
        },
        headers=auth(teacher_token),
    )
    assert revision.status_code == 200, revision.text
    assert revision.json()['status'] == 'NEEDS_REVISION'
    revised = client.put(
        f'{task_url}/draft',
        json={'idempotency_key': 'student-draft-save-revision', 'response': 'Revised evidence reflection.'},
        headers=auth(student_token),
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()['student_response'] == 'Revised evidence reflection.'
    resubmitted = client.post(
        f'{task_url}/submit',
        json={'idempotency_key': 'student-submit-revision', 'response': 'Revised evidence reflection.'},
        headers=auth(student_token),
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()['revision_count'] == 1
    with client.app.state.test_session_factory() as db:
        events = db.query(RecoveryTaskEvent).filter_by(task_id=task['id']).all()
        assert [event.event_type for event in events].count('DRAFT_SAVED') == 2
        assert [event.event_type for event in events].count('SUBMITTED') == 2
        assert db.query(AuditLog).filter_by(action='recovery_task.draft_saved').count() == 2


def test_teacher_plan_edits_preserve_generated_activity(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    created = client.post(
        f'/api/v1/students/{student_id}/recovery-plans',
        json={'generation_key': 'preserve-generated-activity'},
        headers=auth(teacher_token),
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    task = plan['plan']['tasks'][0]
    original_activity = task['activity']

    edited = client.patch(
        f'/api/v1/recovery-plans/{plan["id"]}',
        json={'tasks': [{
            'title': task['title'],
            'learning_objective': task['learning_objective'],
            'instructions': task['instructions'],
            'activity_type': task['activity_type'],
            'priority': task['priority'],
            'subject_id': task['subject_id'],
            'subject': task['subject'],
            'topic': task['topic'],
            'scheduled_at': task['scheduled_at'],
            'deadline': task['deadline'],
            'estimated_minutes': task['estimated_minutes'],
            'resources': task['resources'],
            'completion_criteria': task['completion_criteria'],
            'mastery_criteria': task['mastery_criteria'],
            'evidence_refs': task['evidence_refs'],
            'activity': original_activity,
        }]},
        headers=auth(teacher_token),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()['plan']['tasks'][0]['activity'] == original_activity
    assert client.post(
        f'/api/v1/recovery-plans/{plan["id"]}/approve',
        json={},
        headers=auth(teacher_token),
    ).status_code == 200
    assert client.post(
        f'/api/v1/recovery-plans/{plan["id"]}/publish',
        json={},
        headers=auth(teacher_token),
    ).status_code == 200
    student_tasks = client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token))
    assert student_tasks.status_code == 200, student_tasks.text
    assert student_tasks.json()[0]['activity'] == original_activity


def test_teacher_activity_replacement_is_withheld_until_approved(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    created = client.post(
        f'/api/v1/students/{student_id}/recovery-plans',
        json={'generation_key': 'activity-approval-plan'},
        headers=auth(teacher_token),
    )
    plan_id = created.json()['id']
    client.post(f'/api/v1/recovery-plans/{plan_id}/approve', json={}, headers=auth(teacher_token))
    client.post(f'/api/v1/recovery-plans/{plan_id}/publish', json={}, headers=auth(teacher_token))
    task = client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token)).json()[0]
    task_url = f'/api/v1/recovery-tasks/{task["id"]}'
    replacement = {
        'idempotency_key': 'teacher-activity-proposal',
        'title': 'Teacher-approved Biology evidence activity',
        'learning_objective': 'Interpret the linked evidence and complete a teacher-selected follow-up.',
        'instructions': 'Review the lesson and submit a written evidence reflection.',
        'completion_criteria': 'Identify the record and document one teacher-guided next step.',
        'resources': [],
        'activity': {
            'reason': 'The teacher has enriched the existing evidence-review activity.',
            'lesson': 'A recorded result describes a specific assessment, not topic mastery.',
            'steps': ['Identify the linked assessment.', 'Discuss one follow-up question with your teacher.'],
            'exercise': 'Write what the result records and one question for your teacher.',
            'submission_format': 'Submit a short written reflection.',
        },
    }

    assert client.put(f'{task_url}/activity', json=replacement, headers=auth(student_token)).status_code == 403
    proposed = client.put(f'{task_url}/activity', json=replacement, headers=auth(teacher_token))
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()['status'] == 'DRAFT'
    assert client.put(f'{task_url}/activity', json=replacement, headers=auth(teacher_token)).status_code == 200
    latest_replacement = {
        **replacement,
        'idempotency_key': 'teacher-activity-proposal-latest',
        'title': 'Latest teacher-reviewed Biology activity',
        'activity': {
            **replacement['activity'],
            'lesson': 'A recorded result describes an assessment and does not establish topic mastery.',
        },
    }
    latest_proposed = client.put(f'{task_url}/activity', json=latest_replacement, headers=auth(teacher_token))
    assert latest_proposed.status_code == 200, latest_proposed.text
    monitoring = client.get('/api/v1/teacher/recovery-monitoring', headers=auth(teacher_token))
    assert monitoring.status_code == 200, monitoring.text
    assert monitoring.json()['tasks'][0]['pending_activity']['title'] == latest_replacement['title']
    assert monitoring.json()['tasks'][0]['pending_activity']['activity']['lesson'] == latest_replacement['activity']['lesson']
    assert client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token)).json() == []
    hidden_task = client.patch(
        task_url,
        json={'action': 'start', 'idempotency_key': 'student-start-unapproved-activity'},
        headers=auth(student_token),
    )
    assert hidden_task.status_code == 404

    approved_as_student = client.post(
        f'{task_url}/approve-activity',
        json={'idempotency_key': 'student-approve-activity'},
        headers=auth(student_token),
    )
    assert approved_as_student.status_code == 403
    approved = client.post(
        f'{task_url}/approve-activity',
        json={'idempotency_key': 'teacher-approve-activity'},
        headers=auth(teacher_token),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()['status'] == 'AVAILABLE'
    assert approved.json()['title'] == latest_replacement['title']
    with client.app.state.test_session_factory() as db:
        persisted_plan = db.query(SupportPlanVersion).filter_by(id=task['roadmap_version_id']).one()
        persisted_definition = next(
            item for item in persisted_plan.plan_json['tasks'] if item['task_key'] == task['task_key']
        )
        assert persisted_definition['activity']['lesson'] == latest_replacement['activity']['lesson']
    assert approved.json()['activity']['lesson'] == latest_replacement['activity']['lesson']
    assert client.post(
        f'{task_url}/approve-activity',
        json={'idempotency_key': 'teacher-approve-activity'},
        headers=auth(teacher_token),
    ).status_code == 200
    student_task = client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token)).json()[0]
    assert student_task['title'] == latest_replacement['title']
    started = client.patch(
        task_url, json={'action': 'start', 'idempotency_key': 'student-start-approved-activity'}, headers=auth(student_token),
    )
    assert started.status_code == 200
    assert client.put(f'{task_url}/activity', json={**replacement, 'idempotency_key': 'late-activity'}, headers=auth(teacher_token)).status_code == 409
    with client.app.state.test_session_factory() as db:
        assert [event.event_type for event in db.query(RecoveryTaskEvent).filter_by(task_id=task['id']).all()] == [
            'PUBLISHED', 'ACTIVITY_PROPOSED', 'ACTIVITY_PROPOSED', 'ACTIVITY_APPROVED', 'STARTED',
        ]
        assert db.query(AuditLog).filter_by(action='recovery_task.activity_approved').count() == 1


def test_repeated_revision_counts_distinct_assessments_for_the_same_task(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    with client.app.state.test_session_factory() as db:
        outsider = User(
            email='outsider-teacher@example.com',
            full_name='Outsider Teacher',
            role='TEACHER',
            password_hash=hash_password('outsider-pass'),
        )
        db.add(outsider)
        db.commit()
    outsider_token = login(client, 'outsider-teacher@example.com', 'outsider-pass')
    assert client.get(
        '/api/v1/teacher/recovery-adaptations',
        headers=auth(outsider_token),
    ).status_code == 403
    created = client.post(
        f'/api/v1/students/{student_id}/recovery-plans',
        json={'generation_key': 'adapt-revision-key'},
        headers=auth(teacher_token),
    )
    plan_id = created.json()['id']
    source_task = created.json()['plan']['tasks'][0]
    task_fields = (
        'title',
        'learning_objective',
        'instructions',
        'activity_type',
        'priority',
        'subject_id',
        'subject',
        'topic',
        'scheduled_at',
        'deadline',
        'estimated_minutes',
        'resources',
        'completion_criteria',
        'mastery_criteria',
        'evidence_refs',
    )
    original_task = {field: source_task[field] for field in task_fields}
    unrelated_task = {
        'title': 'Independent evidence review',
        'learning_objective': source_task['learning_objective'],
        'instructions': source_task['instructions'],
        'activity_type': source_task['activity_type'],
        'priority': source_task['priority'],
        'subject_id': source_task['subject_id'],
        'subject': source_task['subject'],
        'topic': None,
        'scheduled_at': None,
        'deadline': None,
        'estimated_minutes': 15,
        'resources': [],
        'completion_criteria': source_task['completion_criteria'],
        'mastery_criteria': None,
        'evidence_refs': source_task['evidence_refs'],
    }
    edited = client.patch(
        f'/api/v1/recovery-plans/{plan_id}',
        json={'tasks': [original_task, unrelated_task]},
        headers=auth(teacher_token),
    )
    assert edited.status_code == 200, edited.text
    assert client.get(
        f'/api/v1/teacher/recovery-adaptations?student_id={student_id}',
        headers=auth(teacher_token),
    ).json() == []
    client.post(f'/api/v1/recovery-plans/{plan_id}/approve', json={}, headers=auth(teacher_token))
    client.post(f'/api/v1/recovery-plans/{plan_id}/publish', json={}, headers=auth(teacher_token))

    tasks = client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token)).json()
    assert len(tasks) == 2
    task = next(item for item in tasks if item['title'] != 'Independent evidence review')
    task_url = f'/api/v1/recovery-tasks/{task["id"]}'
    started = client.patch(
        task_url,
        json={'action': 'start', 'idempotency_key': 'adapt-start-001'},
        headers=auth(student_token),
    )
    assert started.status_code == 200, started.text
    first_submission = client.post(
        f'{task_url}/submit',
        json={'idempotency_key': 'adapt-submit-first', 'response': 'I reviewed the evidence.'},
        headers=auth(student_token),
    )
    assert first_submission.status_code == 200, first_submission.text
    first_assessment = {
        'idempotency_key': 'adapt-assess-first',
        'decision': 'needs_revision',
        'feedback': 'Please clarify the evidence review.',
    }
    assert client.patch(
        f'{task_url}/assess',
        json=first_assessment,
        headers=auth(teacher_token),
    ).status_code == 200
    assert client.patch(
        f'{task_url}/assess',
        json=first_assessment,
        headers=auth(teacher_token),
    ).status_code == 200

    with client.app.state.test_session_factory() as db:
        plan = db.query(SupportPlanVersion).filter_by(id=plan_id).one()
        definitions = plan.plan_json['tasks']
        independent_key = next(item['task_key'] for item in definitions if item['title'] == 'Independent evidence review')
        independent_task = (
            db.query(RecoveryTask)
            .join(SupportPlanVersion, RecoveryTask.roadmap_version_id == SupportPlanVersion.id)
            .filter(
                SupportPlanVersion.source_blueprint_plan_id == plan_id,
                RecoveryTask.task_key == independent_key,
            )
            .one()
        )
        independent_task_id = independent_task.id
    independent_url = f'/api/v1/recovery-tasks/{independent_task_id}'
    assert client.patch(
        independent_url,
        json={'action': 'start', 'idempotency_key': 'independent-start-001'},
        headers=auth(student_token),
    ).status_code == 200
    assert client.post(
        f'{independent_url}/submit',
        json={'idempotency_key': 'independent-submit-001', 'response': 'Independent work.'},
        headers=auth(student_token),
    ).status_code == 200
    assert client.patch(
        f'{independent_url}/assess',
        json={
            'idempotency_key': 'independent-assess-001',
            'decision': 'needs_revision',
            'feedback': 'Review this separate task.',
        },
        headers=auth(teacher_token),
    ).status_code == 200

    assert client.post(
        f'{task_url}/submit',
        json={'idempotency_key': 'adapt-submit-second', 'response': 'Updated evidence review.'},
        headers=auth(student_token),
    ).status_code == 200
    second_assessment = {
        'idempotency_key': 'adapt-assess-second',
        'decision': 'needs_revision',
        'feedback': 'Please clarify the evidence review again.',
    }
    assert client.patch(
        f'{task_url}/assess',
        json=second_assessment,
        headers=auth(teacher_token),
    ).status_code == 200

    response = client.get(
        f'/api/v1/teacher/recovery-adaptations?student_id={student_id}',
        headers=auth(teacher_token),
    )
    assert response.status_code == 200, response.text
    decisions = response.json()
    assert len(decisions) == 3
    by_task = {}
    for decision in decisions:
        by_task.setdefault(decision['task_id'], []).append(decision)
    assert {item['decision_type'] for item in by_task[task['id']]} == {
        'REPEATED_DIFFICULTY_REVIEW',
        'TEACHER_REVIEW_REQUIRED',
    }
    assert [item['decision_type'] for item in by_task[independent_task_id]] == ['TEACHER_REVIEW_REQUIRED']
    assert all(decision['status'] == 'PENDING_TEACHER_REVIEW' for decision in decisions)
    assert all(decision['evidence']['topic_level_mastery'] == 'NOT_ESTABLISHED' for decision in decisions)
    assert all('before changing future recovery activities' in decision['recommended_action'] for decision in decisions)

    assert client.patch(
        f'{task_url}/assess',
        json=second_assessment,
        headers=auth(teacher_token),
    ).status_code == 200
    first_page = client.get(
        f'/api/v1/teacher/recovery-adaptations?student_id={student_id}&limit=1&offset=0',
        headers=auth(teacher_token),
    )
    second_page = client.get(
        f'/api/v1/teacher/recovery-adaptations?student_id={student_id}&limit=1&offset=1',
        headers=auth(teacher_token),
    )
    assert first_page.status_code == second_page.status_code == 200
    assert len(first_page.json()) == len(second_page.json()) == 1
    assert first_page.json()[0]['id'] != second_page.json()[0]['id']
    assert client.get(
        f'/api/v1/teacher/recovery-adaptations?student_id={student_id}',
        headers=auth(student_token),
    ).status_code == 403
    assert client.get(
        f'/api/v1/teacher/recovery-adaptations?student_id={client.app.state.test_other_student_id}',
        headers=auth(teacher_token),
    ).status_code == 404
    assert client.patch(
        '/api/v1/teacher/recovery-adaptations',
        json={'status': 'RESOLVED'},
        headers=auth(teacher_token),
    ).status_code == 405

    with client.app.state.test_session_factory() as db:
        assert db.query(RecoveryTask).filter_by(student_id=student_id).count() == 2
        plan = db.query(SupportPlanVersion).filter_by(id=plan_id).one()
        assert plan.status == 'PUBLISHED'
        assert len(plan.plan_json['tasks']) == 2
        assert db.query(RecoveryAdaptationDecision).filter_by(task_id=task['id']).count() == 2
        assert db.query(RecoveryAdaptationDecision).filter_by(task_id=independent_task_id).count() == 1
        decision = db.query(RecoveryAdaptationDecision).filter_by(task_id=task['id']).first()
        trigger_event = db.query(RecoveryTaskEvent).filter_by(id=decision.trigger_event_id).one()
        task_record = db.query(RecoveryTask).filter_by(id=task['id']).one()
        duplicate, created = record_adaptation_decision(
            db,
            task=task_record,
            trigger_event=trigger_event,
        )
        assert created is False
        assert duplicate.id == decision.id
        adaptation_audits = db.query(AuditLog).filter_by(action='recovery_adaptation.generated').all()
        assert len(adaptation_audits) == 3
        assert all(row.resource_type == 'recovery_adaptation' for row in adaptation_audits)
        assert all(row.detail['actor_role'] == 'TEACHER' for row in adaptation_audits)
        assert all(row.detail['new_state'] == 'PENDING_TEACHER_REVIEW' for row in adaptation_audits)
        assert all(row.detail['reason'] for row in adaptation_audits)
        decision.rationale = 'Attempt to rewrite immutable decision.'
        with pytest.raises(ValueError, match='immutable'):
            db.commit()
        db.rollback()


def test_assessment_and_adaptation_rollback_together(client, teacher_token, student_token, monkeypatch):
    student_id = link_teacher_and_add_evidence(client)
    created = client.post(
        f'/api/v1/students/{student_id}/recovery-plans',
        json={'generation_key': 'adapt-rollback-key'},
        headers=auth(teacher_token),
    )
    plan_id = created.json()['id']
    client.post(f'/api/v1/recovery-plans/{plan_id}/approve', json={}, headers=auth(teacher_token))
    client.post(f'/api/v1/recovery-plans/{plan_id}/publish', json={}, headers=auth(teacher_token))
    task = client.get('/api/v1/students/me/recovery-tasks', headers=auth(student_token)).json()[0]
    task_url = f'/api/v1/recovery-tasks/{task["id"]}'
    assert client.patch(
        task_url,
        json={'action': 'start', 'idempotency_key': 'rollback-start-001'},
        headers=auth(student_token),
    ).status_code == 200
    assert client.post(
        f'{task_url}/submit',
        json={'idempotency_key': 'rollback-submit-001', 'response': 'Submitted work.'},
        headers=auth(student_token),
    ).status_code == 200

    def fail_adaptation(*args, **kwargs):
        raise RuntimeError('adaptation persistence failed')

    monkeypatch.setattr('backend.app.main.record_adaptation_decision', fail_adaptation)
    with pytest.raises(RuntimeError, match='adaptation persistence failed'):
        client.patch(
            f'{task_url}/assess',
            json={
                'idempotency_key': 'rollback-assess-001',
                'decision': 'needs_revision',
                'feedback': 'Needs another review.',
            },
            headers=auth(teacher_token),
        )

    with client.app.state.test_session_factory() as db:
        task_record = db.query(RecoveryTask).filter_by(id=task['id']).one()
        assert task_record.status == 'SUBMITTED'
        assert db.query(RecoveryTaskEvent).filter_by(idempotency_key='rollback-assess-001').count() == 0
        assert db.query(RecoveryAdaptationDecision).filter_by(task_id=task['id']).count() == 0
        assert db.query(AuditLog).filter_by(action='recovery_task.assessed').count() == 0
        assert db.query(AuditLog).filter_by(action='recovery_adaptation.generated').count() == 0


def test_concurrent_teacher_assessments_claim_a_submitted_task_once(client, tmp_path):
    database_path = tmp_path / 'recovery-assessment-race.sqlite'
    engine = create_engine(
        f'sqlite:///{database_path.as_posix()}',
        connect_args={'check_same_thread': False, 'timeout': 30},
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with TestingSession() as db:
        teacher_user = User(
            email='race-teacher@example.com',
            full_name='Race Teacher',
            role='TEACHER',
            password_hash=hash_password('race-teacher-pass'),
        )
        db.add(teacher_user)
        db.flush()
        teacher = Teacher(user_id=teacher_user.id, department='Science')
        db.add(teacher)
        db.flush()
        course = Course(
            name='Race Course',
            code='RACE-001',
            section='R',
            teacher_id=teacher.id,
        )
        db.add(course)
        db.flush()
        student = Student(
            student_id='RACE-001',
            full_name='Race Student',
            section='R',
            course_id=course.id,
        )
        db.add(student)
        db.flush()
        plan = SupportPlanVersion(
            student_id=student.id,
            teacher_id=teacher.id,
            version=1,
            status='PUBLISHED',
            plan_json={'tasks': []},
            evidence_json={},
            generation_key='race-plan-key',
        )
        db.add(plan)
        db.flush()
        task = RecoveryTask(
            roadmap_version_id=plan.id,
            student_id=student.id,
            task_key='race-task',
            learning_objective='Review a submitted response with a teacher.',
            title='Concurrent assessment',
            instructions='Review the response.',
            activity_type='TEACHER_REVIEW',
            source='EVIDENCE_REVIEW',
            estimated_minutes=10,
            resources=[],
            completion_criteria='Teacher records a decision.',
            status='SUBMITTED',
            student_response='Student response.',
        )
        db.add(task)
        db.commit()
        task_id = task.id
        duplicate_task = RecoveryTask(
            roadmap_version_id=plan.id,
            student_id=student.id,
            task_key='duplicate-race-task',
            learning_objective='Review a submitted response with a teacher.',
            title='Concurrent duplicate assessment',
            instructions='Review the response.',
            activity_type='TEACHER_REVIEW',
            source='EVIDENCE_REVIEW',
            estimated_minutes=10,
            resources=[],
            completion_criteria='Teacher records a decision.',
            status='SUBMITTED',
            student_response='Student response.',
        )
        db.add(duplicate_task)
        db.commit()
        duplicate_task_id = duplicate_task.id

    previous_override = client.app.dependency_overrides.get(get_db)

    def override_get_db():
        with TestingSession() as db:
            yield db

    client.app.dependency_overrides[get_db] = override_get_db
    try:
        race_token = login(client, 'race-teacher@example.com', 'race-teacher-pass')
        start = Barrier(3)

        def assess(key, decision):
            start.wait(timeout=10)
            return client.patch(
                f'/api/v1/recovery-tasks/{task_id}/assess',
                json={
                    'idempotency_key': key,
                    'decision': decision,
                    'feedback': 'Concurrent teacher assessment.',
                },
                headers=auth(race_token),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(assess, 'race-assessment-001', 'needs_revision')
            second = executor.submit(assess, 'race-assessment-002', 'complete')
            start.wait(timeout=10)
            responses = [first.result(timeout=20), second.result(timeout=20)]
        assert sorted(response.status_code for response in responses) == [200, 409]
        with TestingSession() as db:
            persisted_task = db.query(RecoveryTask).filter_by(id=task_id).one()
            assert persisted_task.status in {'COMPLETED', 'NEEDS_REVISION'}
            assert db.query(RecoveryTaskEvent).filter_by(task_id=task_id, event_type='ASSESSED').count() == 1
            assert db.query(RecoveryAdaptationDecision).filter_by(task_id=task_id).count() == 1
            assert db.query(AuditLog).filter_by(action='recovery_task.assessed').count() == 1
            assert db.query(AuditLog).filter_by(action='recovery_adaptation.generated').count() == 1

        def retry_same_assessment():
            start.wait(timeout=10)
            return client.patch(
                f'/api/v1/recovery-tasks/{duplicate_task_id}/assess',
                json={
                    'idempotency_key': 'same-race-assessment-key',
                    'decision': 'complete',
                    'feedback': 'Same idempotent teacher assessment.',
                },
                headers=auth(race_token),
            )

        start = Barrier(3)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(retry_same_assessment)
            second = executor.submit(retry_same_assessment)
            start.wait(timeout=10)
            retry_responses = [first.result(timeout=20), second.result(timeout=20)]
        assert [response.status_code for response in retry_responses] == [200, 200]
        with TestingSession() as db:
            duplicate_task_record = db.query(RecoveryTask).filter_by(id=duplicate_task_id).one()
            assert duplicate_task_record.status == 'COMPLETED'
            assert db.query(RecoveryTaskEvent).filter_by(
                task_id=duplicate_task_id,
                idempotency_key='same-race-assessment-key',
            ).count() == 1
            assert db.query(RecoveryAdaptationDecision).filter_by(task_id=duplicate_task_id).count() == 1
            assert db.query(AuditLog).filter_by(action='recovery_task.assessed').count() == 2
            assert db.query(AuditLog).filter_by(action='recovery_adaptation.generated').count() == 2
    finally:
        if previous_override is not None:
            client.app.dependency_overrides[get_db] = previous_override
        else:
            client.app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_monitoring_only_includes_published_tasks_for_assigned_students(client, teacher_token):
    student_id = link_teacher_and_add_evidence(client)
    created = client.post(
        f'/api/v1/students/{student_id}/recovery-plans',
        json={'generation_key': 'monitoring-key'},
        headers=auth(teacher_token),
    )
    plan_id = created.json()['id']
    before_publish = client.get('/api/v1/teacher/recovery-monitoring', headers=auth(teacher_token))
    assert before_publish.status_code == 200, before_publish.text
    assert before_publish.json()['tasks'] == []

    client.post(f'/api/v1/recovery-plans/{plan_id}/approve', json={}, headers=auth(teacher_token))
    client.post(f'/api/v1/recovery-plans/{plan_id}/publish', json={}, headers=auth(teacher_token))
    monitoring = client.get('/api/v1/teacher/recovery-monitoring', headers=auth(teacher_token))
    assert monitoring.status_code == 200, monitoring.text
    assert len(monitoring.json()['tasks']) == 1
    assert monitoring.json()['counts']['AVAILABLE'] == 1
    assert [student['id'] for student in monitoring.json()['students']] == [student_id]
    assert monitoring.json()['tasks'][0]['evidence_refs']
    assert client.get(
        f'/api/v1/teacher/recovery-monitoring?student_id={client.app.state.test_other_student_id}',
        headers=auth(teacher_token),
    ).status_code == 404


def test_plan_edits_and_rejections_are_teacher_only_and_audited(client, teacher_token, student_token):
    student_id = link_teacher_and_add_evidence(client)
    created = client.post(
        f'/api/v1/students/{student_id}/recovery-plans',
        json={'generation_key': 'edit-reject-key'},
        headers=auth(teacher_token),
    )
    plan_id = created.json()['id']
    edit = client.patch(
        f'/api/v1/recovery-plans/{plan_id}',
        json={'summary': 'Teacher-reviewed summary.'},
        headers=auth(student_token),
    )
    assert edit.status_code == 403
    rejected = client.post(
        f'/api/v1/recovery-plans/{plan_id}/reject',
        json={'reason': 'Requires additional teacher review.'},
        headers=auth(teacher_token),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()['status'] == 'REJECTED'
    assert client.get(f'/api/v1/students/{student_id}/recovery-plans', headers=auth(student_token)).json() == []
    with client.app.state.test_session_factory() as db:
        assert db.query(AuditLog).filter(AuditLog.action == 'recovery_plan.rejected').count() == 1


def test_service_evidence_and_missing_information_are_explicit(client):
    student_id = client.app.state.test_student_id
    with client.app.state.test_session_factory() as db:
        student = db.query(Student).filter_by(id=student_id).one()
        evidence = build_recovery_evidence(db, student)
    assert evidence['academic_records'] == []
    assert evidence['model_outputs']['risk_prediction'] is None
    assert evidence['missing_information']


def test_llm_timeout_falls_back_to_deterministic_evidence_tasks(client):
    student_id = link_teacher_and_add_evidence(client)
    with client.app.state.test_session_factory() as db:
        student = db.query(Student).filter_by(id=student_id).one()
        evidence = build_recovery_evidence(db, student)

    def timeout_selector(_):
        raise TimeoutError('selector timed out')

    _, tasks, persisted_evidence = make_draft_plan(evidence, evidence_selector=timeout_selector)
    assert tasks
    assert persisted_evidence['generation']['generator'] == 'deterministic_evidence_rules'
    assert persisted_evidence['generation']['llm_status'] == 'evidence_selector_failed_deterministic_fallback'
    assert tasks[0]['evidence_refs']


def test_recovery_tutor_requires_authentication_and_cannot_select_another_student(client, student_token, monkeypatch):
    link_teacher_and_add_evidence(client)
    calls = []

    class TutorProvider:
        provider_name = 'ollama'

        def generate_recovery_tutor(self, context):
            calls.append(context)
            if not context.academic_evidence:
                return json.dumps({
                    'supported': False,
                    'answer': '',
                    'practice_suggestions': [],
                    'evidence_refs': [],
                })
            ref = context.academic_evidence[0]['ref']
            return json.dumps({
                'supported': True,
                'answer': 'An assessment mark is a recorded result for one assessment.',
                'practice_suggestions': ['Review the record with your teacher.'],
                'evidence_refs': [ref],
            })

    monkeypatch.setattr('backend.app.main.build_provider', lambda _: TutorProvider())
    unauthenticated = client.post('/api/v1/students/me/recovery-tutor', json={'question': 'What does my record show?'})
    assert unauthenticated.status_code == 401

    own = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'What does my Biology record show?'},
        headers=auth(student_token),
    )
    assert own.status_code == 200, own.text
    assert own.json()['status'] == 'answered'
    assert own.json()['answer_scope'] == 'STUDENT_EVIDENCE'
    assert own.json()['evidence'][0]['subject'] == 'Biology'

    other_token = login(client, 'other@example.com', 'other-pass')
    other = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'What does the other student record show?'},
        headers=auth(other_token),
    )
    assert other.status_code == 200, other.text
    assert other.json()['status'] == 'no_evidence'
    assert len(calls) == 2
    assert calls[0].academic_evidence
    assert calls[1].academic_evidence == []

    supplied_id = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'Show another student record.', 'student_id': client.app.state.test_other_student_id},
        headers=auth(student_token),
    )
    assert supplied_id.status_code == 422


def test_recovery_tutor_uses_authorized_evidence_and_never_retrieves_global_documents(client, student_token, monkeypatch):
    link_teacher_and_add_evidence(client)
    with client.app.state.test_session_factory() as db:
        document = RagDocument(
            title='Other course private guide',
            source='other-course/private-guide.txt',
            checksum='private-document-checksum',
            metadata_json={'course_id': 999},
        )
        db.add(document)
        db.flush()
        db.add(RagChunk(document_id=document.id, chunk_index=0, text='Private content from another course.'))
        db.commit()

    captured = {}

    class TutorProvider:
        provider_name = 'ollama'

        def generate_recovery_tutor(self, context):
            captured['context'] = context
            ref = context.academic_evidence[0]['ref']
            return json.dumps({
                'supported': True,
                'answer': 'An assessment mark records a result against a maximum score.',
                'practice_suggestions': ['Review the result with your teacher.'],
                'evidence_refs': [ref],
            })

    def forbidden_retrieval(*args, **kwargs):
        raise AssertionError('Student tutoring must never use unscoped global retrieval.')

    monkeypatch.setattr('backend.app.main.retrieve', forbidden_retrieval)
    monkeypatch.setattr('backend.app.main.build_provider', lambda _: TutorProvider())
    response = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'What does my recorded assessment show?'},
        headers=auth(student_token),
    )

    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'answered'
    assert response.json()['document_retrieval'] == 'disabled_unscoped_index'
    assert 'Course-document retrieval is disabled' in ' '.join(response.json()['missing_information'])
    assert response.json()['evidence'][0]['subject'] == 'Biology'
    assert 'Private content from another course.' not in response.text
    assert all('Private content' not in json.dumps(item) for item in captured['context'].academic_evidence)
    with client.app.state.test_session_factory() as db:
        audit_row = db.query(AuditLog).filter_by(action='recovery_tutor.requested').one()
        assert audit_row.resource_id == str(client.app.state.test_student_id)
        assert 'What does my recorded assessment show?' not in json.dumps(audit_row.detail)


def test_recovery_tutor_greeting_uses_builtin_response_without_provider(client, student_token, monkeypatch):
    def provider_must_not_be_created(_settings):
        raise AssertionError('A built-in greeting must not require the LLM provider.')

    monkeypatch.setattr('backend.app.main.build_provider', provider_must_not_be_created)
    response = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'hi'},
        headers=auth(student_token),
    )

    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'answered'
    assert response.json()['answer_source'] == 'built_in'
    assert 'Hi!' in response.json()['answer']


def test_recovery_tutor_answers_general_question_without_academic_evidence(client, student_token, monkeypatch):
    captured = {}

    class TutorProvider:
        provider_name = 'ollama-local'

        def generate_recovery_tutor(self, context):
            captured['context'] = context
            return json.dumps({
                'supported': True,
                'answer_scope': 'GENERAL_EXPLANATION',
                'answer': 'Photosynthesis is the process plants use to convert light energy into chemical energy.',
                'practice_suggestions': ['Explain how light, water, and carbon dioxide contribute to the process.'],
                'evidence_refs': [],
            })

    monkeypatch.setattr('backend.app.main.build_provider', lambda _: TutorProvider())
    response = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={
            'question': 'Can you explain that in simpler terms?',
            'conversation_history': [
                {'role': 'user', 'content': 'What is photosynthesis?'},
                {'role': 'assistant', 'content': 'Plants use light energy to make sugars.'},
            ],
        },
        headers=auth(student_token),
    )

    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'answered'
    assert response.json()['answer_source'] == 'provider'
    assert response.json()['answer_scope'] == 'GENERAL_EXPLANATION'
    assert response.json()['evidence'] == []
    assert captured['context'].academic_evidence == []
    assert captured['context'].conversation_history[0]['content'] == 'What is photosynthesis?'


def test_recovery_tutor_rejects_oversized_conversation_history(client, student_token):
    response = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={
            'question': 'Give me a hint.',
            'conversation_history': [{'role': 'user', 'content': f'question {index}'} for index in range(7)],
        },
        headers=auth(student_token),
    )

    assert response.status_code == 422


@pytest.mark.parametrize('allow_external', [False, True])
def test_recovery_tutor_only_sends_evidence_to_explicitly_allowed_external_provider(
    client,
    student_token,
    monkeypatch,
    allow_external,
):
    link_teacher_and_add_evidence(client)
    captured = []

    class ExternalTutorProvider:
        provider_name = 'gemini'

        def generate_recovery_tutor(self, context):
            captured.append(context)
            return json.dumps({
                'supported': True,
                'answer_scope': 'STUDENT_EVIDENCE',
                'answer': 'The Biology assessment record contains one recorded result.',
                'practice_suggestions': [],
                'evidence_refs': [context.academic_evidence[0]['ref']],
            })

    monkeypatch.setattr('backend.app.main.settings.allow_external_tutor_data', allow_external)
    monkeypatch.setattr('backend.app.main.build_provider', lambda _: ExternalTutorProvider())
    response = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'What does my recorded Biology assessment show?'},
        headers=auth(student_token),
    )

    assert response.status_code == 200, response.text
    if allow_external:
        assert response.json()['status'] == 'answered'
        assert captured and captured[0].academic_evidence
    else:
        assert response.json()['status'] == 'unavailable'
        assert captured == []
        assert 'not an AI-generated answer' in response.json()['answer']


def test_recovery_tutor_returns_unsupported_question_without_fabricating_an_answer(client, student_token, monkeypatch):
    link_teacher_and_add_evidence(client)

    class TutorProvider:
        provider_name = 'ollama'

        def generate_recovery_tutor(self, context):
            return json.dumps({
                'supported': False,
                'answer': '',
                'practice_suggestions': [],
                'evidence_refs': [],
            })

    monkeypatch.setattr('backend.app.main.build_provider', lambda _: TutorProvider())
    response = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'Which topic have I mastered?'},
        headers=auth(student_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'unsupported'
    assert response.json()['practice_suggestions'] == []
    assert 'cannot be answered' in response.json()['answer']


@pytest.mark.parametrize('failure', ['timeout', 'malformed', 'unsupported-number', 'mastery-claim'])
def test_recovery_tutor_provider_failures_and_unverified_claims_fall_back(client, student_token, monkeypatch, failure):
    link_teacher_and_add_evidence(client)

    class TutorProvider:
        provider_name = 'ollama'

        def generate_recovery_tutor(self, context):
            if failure == 'timeout':
                raise RuntimeError('Provider timed out.')
            if failure == 'malformed':
                return '{not-json'
            answer = (
                'Your recorded mark is 99 out of 100.'
                if failure == 'unsupported-number'
                else 'The record shows topic-level mastery.'
            )
            return json.dumps({
                'supported': True,
                'answer': answer,
                'practice_suggestions': [],
                'evidence_refs': [context.academic_evidence[0]['ref']],
            })

    monkeypatch.setattr('backend.app.main.build_provider', lambda _: TutorProvider())
    response = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'Explain my recorded Biology assessment.'},
        headers=auth(student_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'unavailable'
    assert 'not an AI-generated answer' in response.json()['answer']
    assert response.json()['evidence'] == []


@pytest.mark.parametrize('failure', ['unavailable', 'timeout'])
def test_recovery_tutor_handles_ollama_unavailable_and_timeout(client, student_token, monkeypatch, failure):
    link_teacher_and_add_evidence(client)
    requests = 0

    def fake_urlopen(request, timeout):
        nonlocal requests
        requests += 1
        if failure == 'unavailable' or requests == 2:
            if failure == 'timeout' and requests == 2:
                raise TimeoutError('Ollama timed out.')
            raise OSError('Ollama is not reachable.')
        return BytesIO(b'{"models":[{"name":"qwen3:8b"}]}')

    monkeypatch.setattr('ai.llm.provider.urlopen', fake_urlopen)
    monkeypatch.setattr(
        'backend.app.main.build_provider',
        lambda _: LocalLLMProvider('ollama:qwen3:8b', timeout_seconds=1, health_check_retries=0),
    )
    response = client.post(
        '/api/v1/students/me/recovery-tutor',
        json={'question': 'Explain my recorded Biology assessment.'},
        headers=auth(student_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'unavailable'
    assert 'not an AI-generated answer' in response.json()['answer']
