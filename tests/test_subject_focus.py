from datetime import datetime

import pytest

from backend.app.models import (
    Assessment,
    AuditLog,
    Course,
    Mark,
    Subject,
    SubjectFocusPolicy,
    StudentSubjectAcademicRecord,
    Teacher,
    User,
)
from backend.app.services.subject_focus import evaluate_subject_assessments
from backend.app.services.auth import hash_password


def auth(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.mark.parametrize(
    ('score', 'maximum', 'normalized', 'status'),
    [
        (11.99, 25, 11.99, 'LOW'),
        (11, 25, 11, 'LOW'),
        (12, 25, 12, 'NOT_LOW'),
        (13, 25, 13, 'NOT_LOW'),
        (18, 50, 9, 'LOW'),
        (23, 50, 11.5, 'LOW'),
        (24, 50, 12, 'NOT_LOW'),
        (22, 50, 11, 'LOW'),
    ],
)
def test_assessment_threshold_uses_strict_normalized_twenty_five_mark_scale(
    score,
    maximum,
    normalized,
    status,
):
    policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy')
    assessment = Assessment(id=10, title='Quiz', maximum_marks=maximum, maximum_marks_verified=True)
    mark = Mark(id=1, assessment=assessment, score=score, recorded_at=datetime(2026, 10, 1))

    result = evaluate_subject_assessments([mark], policy)

    assert result['status'] == status
    assert result['latest_assessment']['normalized_score'] == normalized
    assert result['support_triggers'] == (['LOW_SCORE'] if status == 'LOW' else [])
    assert result['official_failure'] is None


def test_official_failure_is_a_separate_support_trigger():
    policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy')
    assessment = Assessment(id=10, title='Quiz', maximum_marks=50, maximum_marks_verified=True)
    mark = Mark(id=1, assessment=assessment, score=24, official_result='FAIL', recorded_at=datetime(2026, 10, 1))

    result = evaluate_subject_assessments([mark], policy)

    assert result['status'] == 'NOT_LOW'
    assert result['latest_assessment']['normalized_score'] == 12
    assert result['official_failure'] is True
    assert result['support_required'] is True
    assert result['support_triggers'] == ['OFFICIAL_FAIL']
    assert result['history'][0]['official_result'] == 'FAIL'


def test_missing_or_ambiguous_assessment_evidence_stays_insufficient():
    policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy')
    unknown_maximum = Assessment(id=10, title='Quiz', maximum_marks=100, maximum_marks_verified=False)
    first = Mark(id=1, assessment=unknown_maximum, score=10, recorded_at=datetime(2026, 10, 1))
    second = Mark(id=2, assessment=unknown_maximum, score=11, recorded_at=datetime(2026, 10, 1))
    missing_maximum = Assessment(id=11, title='FA-1', maximum_marks=None, maximum_marks_verified=False)
    missing_maximum_mark = Mark(id=3, assessment=missing_maximum, score=10, recorded_at=datetime(2026, 10, 2))
    duplicate_assessments = [
        Mark(
            id=4,
            assessment=Assessment(id=12, title='FA-1', maximum_marks=25, maximum_marks_verified=True),
            score=11,
            recorded_at=datetime(2026, 10, 3),
        ),
        Mark(
            id=5,
            assessment=Assessment(id=13, title='FA-1', maximum_marks=25, maximum_marks_verified=True),
            score=10,
            recorded_at=datetime(2026, 10, 3),
        ),
    ]

    missing = evaluate_subject_assessments([], policy)
    ambiguous = evaluate_subject_assessments([first, second], policy)
    no_maximum = evaluate_subject_assessments(
        [missing_maximum_mark],
        policy,
        first_internal_only=True,
    )
    duplicate_latest = evaluate_subject_assessments(
        duplicate_assessments,
        policy,
        first_internal_only=True,
    )
    no_policy = evaluate_subject_assessments(
        [Mark(id=3, assessment=Assessment(id=12, title='Quiz', maximum_marks=20, maximum_marks_verified=True), score=10, recorded_at=datetime(2026, 10, 2))],
        None,
    )

    assert missing['status'] == 'INSUFFICIENT_EVIDENCE'
    assert ambiguous['status'] == 'INSUFFICIENT_EVIDENCE'
    assert 'ambiguous' in ambiguous['reason']
    assert no_maximum['status'] == 'INSUFFICIENT_EVIDENCE'
    assert no_maximum['latest_assessment']['normalized_score'] is None
    assert 'maximum' in no_maximum['latest_assessment']['reason']
    assert duplicate_latest['status'] == 'INSUFFICIENT_EVIDENCE'
    assert 'Multiple assessments share' in duplicate_latest['reason']
    assert no_policy['status'] == 'INSUFFICIENT_EVIDENCE'

 
def test_legacy_twenty_mark_policy_and_unverified_official_failure_are_not_conflated():
    legacy_policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=20, change_reason='Legacy policy')
    verified_assessment = Assessment(id=9, title='Verified quiz', maximum_marks=50, maximum_marks_verified=True)
    verified_mark = Mark(id=2, assessment=verified_assessment, score=18, recorded_at=datetime(2026, 9, 30))
    assessment = Assessment(id=10, title='Quiz', maximum_marks=50, maximum_marks_verified=False)
    mark = Mark(id=1, assessment=assessment, score=18, official_result='FAIL', recorded_at=datetime(2026, 10, 1))

    stale_policy_result = evaluate_subject_assessments([verified_mark], legacy_policy)
    unverified_failure_result = evaluate_subject_assessments([mark], legacy_policy)

    assert stale_policy_result['status'] == 'INSUFFICIENT_EVIDENCE'
    assert '25-mark scale' in stale_policy_result['reason']
    assert stale_policy_result['latest_assessment']['normalized_score'] is None
    assert unverified_failure_result['status'] == 'INSUFFICIENT_EVIDENCE'
    assert unverified_failure_result['official_failure'] is True
    assert unverified_failure_result['support_required'] is True
    assert unverified_failure_result['support_triggers'] == ['OFFICIAL_FAIL']


def test_subject_record_without_explicit_denominator_verification_is_insufficient():
    policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy')
    older_unverified = Mark(
        id=1,
        assessment=Assessment(id=10, title='Earlier quiz', maximum_marks=100, maximum_marks_verified=False),
        score=80,
        recorded_at=datetime(2026, 1, 1),
    )
    subject_record = StudentSubjectAcademicRecord(
        id=3,
        academic_period='2026-S1',
        fa1=14,
        fa1_observed_at=datetime(2026, 10, 1),
    )

    result = evaluate_subject_assessments([older_unverified], policy, [subject_record])

    assert result['status'] == 'INSUFFICIENT_EVIDENCE'
    assert result['latest_assessment']['assessment'] == 'FA-1'
    assert result['latest_assessment']['score'] == 14
    assert result['latest_assessment']['maximum_marks'] is None
    assert result['latest_assessment']['maximum_marks_verified'] is False
    assert result['latest_assessment']['temporal_evidence_verified'] is False
    assert result['latest_assessment']['normalized_score'] is None
    assert result['latest_assessment']['evidence_status'] == 'INSUFFICIENT_EVIDENCE'
    assert [item['assessment'] for item in result['history']] == ['FA-1', 'Earlier quiz']
    assert result['history'][1]['normalized_score'] is None


def test_undated_subject_record_stays_insufficient_in_first_internal_scope():
    policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy')
    record = StudentSubjectAcademicRecord(
        id=9,
        academic_period='2026-S1',
        fa1=8,
        fa1_observed_at=None,
        temporal_evidence_verified=False,
    )

    result = evaluate_subject_assessments([], policy, [record], first_internal_only=True)

    assert result['status'] == 'INSUFFICIENT_EVIDENCE'
    assert result['latest_assessment'] is None
    assert result['latest_assessments'][0]['score'] == 8
    assert result['latest_assessments'][0]['recorded_at'] is None
    assert result['latest_assessments'][0]['temporal_evidence_verified'] is False
    assert result['latest_assessments'][0]['normalized_score'] is None
    assert 'no observation date' in result['reason']


def test_subject_record_requires_verified_date_and_maximum_before_normalizing():
    policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy')
    verified_below = StudentSubjectAcademicRecord(
        id=20,
        academic_period='2026-S1',
        fa1=22,
        fa1_observed_at=datetime(2026, 9, 1),
        temporal_evidence_verified=True,
        fa1_maximum_marks=50,
        fa1_maximum_verified=True,
    )
    exact_threshold = StudentSubjectAcademicRecord(
        id=21,
        academic_period='2026-S1',
        fa1=24,
        fa1_observed_at=datetime(2026, 9, 1),
        temporal_evidence_verified=True,
        fa1_maximum_marks=50,
        fa1_maximum_verified=True,
    )
    unverified_maximum = StudentSubjectAcademicRecord(
        id=22,
        academic_period='2026-S1',
        fa1=11,
        fa1_observed_at=datetime(2026, 9, 1),
        temporal_evidence_verified=True,
        fa1_maximum_marks=25,
        fa1_maximum_verified=False,
    )
    unverified_date = StudentSubjectAcademicRecord(
        id=23,
        academic_period='2026-S1',
        fa1=11,
        fa1_observed_at=datetime(2026, 9, 1),
        temporal_evidence_verified=False,
        fa1_maximum_marks=25,
        fa1_maximum_verified=True,
    )
    missing_date = StudentSubjectAcademicRecord(
        id=24,
        academic_period='2026-S1',
        fa1=11,
        fa1_observed_at=None,
        temporal_evidence_verified=False,
        fa1_maximum_marks=25,
        fa1_maximum_verified=True,
    )

    below = evaluate_subject_assessments([], policy, [verified_below], first_internal_only=True)
    exact = evaluate_subject_assessments([], policy, [exact_threshold], first_internal_only=True)
    no_maximum = evaluate_subject_assessments([], policy, [unverified_maximum], first_internal_only=True)
    no_date_verification = evaluate_subject_assessments([], policy, [unverified_date], first_internal_only=True)
    missing_observation = evaluate_subject_assessments([], policy, [missing_date], first_internal_only=True)

    assert below['status'] == 'LOW'
    assert below['latest_assessment']['score'] == 22
    assert below['latest_assessment']['maximum_marks'] == 50
    assert below['latest_assessment']['normalized_score'] == 11
    assert exact['status'] == 'NOT_LOW'
    assert exact['latest_assessment']['normalized_score'] == 12
    assert no_maximum['status'] == 'INSUFFICIENT_EVIDENCE'
    assert no_maximum['latest_assessment']['normalized_score'] is None
    assert no_date_verification['status'] == 'INSUFFICIENT_EVIDENCE'
    assert missing_observation['status'] == 'INSUFFICIENT_EVIDENCE'


def test_first_internal_scope_ignores_later_assessments_for_recovery_eligibility():
    policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy')
    fa1 = Mark(
        id=1,
        assessment=Assessment(id=10, title='FA-1', maximum_marks=25, maximum_marks_verified=True),
        score=14,
        recorded_at=datetime(2026, 8, 1),
    )
    fa2 = Mark(
        id=2,
        assessment=Assessment(id=11, title='FA-2', maximum_marks=25, maximum_marks_verified=True),
        score=8,
        recorded_at=datetime(2026, 10, 1),
    )

    latest_result = evaluate_subject_assessments([fa1, fa2], policy)
    first_internal_result = evaluate_subject_assessments(
        [fa1, fa2],
        policy,
        first_internal_only=True,
    )

    assert latest_result['status'] == 'LOW'
    assert latest_result['latest_assessment']['assessment'] == 'FA-2'
    assert first_internal_result['status'] == 'NOT_LOW'
    assert first_internal_result['latest_assessment']['assessment'] == 'FA-1'
    assert first_internal_result['evidence_scope'] == 'FIRST_INTERNAL'
    assert [item['assessment'] for item in first_internal_result['history']] == ['FA-2', 'FA-1']


@pytest.mark.parametrize('score', [-1, 51])
def test_invalid_marks_remain_insufficient_evidence(score):
    policy = SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy')
    assessment = Assessment(id=10, title='Quiz', maximum_marks=50, maximum_marks_verified=True)
    mark = Mark(id=1, assessment=assessment, score=score, recorded_at=datetime(2026, 10, 1))

    result = evaluate_subject_assessments([mark], policy)

    assert result['status'] == 'INSUFFICIENT_EVIDENCE'
    assert result['latest_assessment']['normalized_score'] is None


def test_teacher_and_student_subject_focus_use_scoped_persisted_evidence(client, teacher_token, student_token):
    factory = client.app.state.test_session_factory
    with factory() as db:
        teacher_user = db.query(User).filter_by(email='teacher@example.com').one()
        teacher = Teacher(user_id=teacher_user.id, department='Science')
        subject = Subject(code='BIO-101', name='Biology', department='Science')
        db.add_all([teacher, subject])
        db.flush()
        student = db.query(User).filter_by(email='student@example.com').one().student
        course = Course(name='Biology 101', code='BIO-101-A', section='A', subject=subject, teacher=teacher)
        db.add(course)
        db.flush()
        student.course_id = course.id
        db.add(SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy'))
        assessment = Assessment(
            course=course,
            title='FA-1',
            maximum_marks=40,
            maximum_marks_verified=True,
        )
        db.add(assessment)
        db.flush()
        db.add(Mark(student=student, assessment=assessment, score=22, official_result='FAIL', recorded_at=datetime(2026, 10, 2)))
        later_assessment = Assessment(
            course=course,
            title='FA-2',
            maximum_marks=25,
            maximum_marks_verified=True,
        )
        db.add(later_assessment)
        db.flush()
        db.add(Mark(
            student=student,
            assessment=later_assessment,
            score=8,
            recorded_at=datetime(2026, 10, 3),
        ))
        db.commit()
        student_id = student.id
        subject_id = subject.id

    teacher_result = client.get('/api/v1/teacher/subject-focus', headers=auth(teacher_token))
    assert teacher_result.status_code == 200
    biology = next(item for item in teacher_result.json()['subjects'] if item['id'] == subject_id)
    assert biology['students'][0]['id'] == student_id
    assert biology['students'][0]['status'] == 'LOW'
    assert biology['students'][0]['latest_assessment']['assessment'] == 'FA-2'
    assert biology['students'][0]['latest_assessment']['score'] == 8
    first_internal_result = client.get(
        '/api/v1/teacher/subject-focus?first_internal_only=true',
        headers=auth(teacher_token),
    )
    assert first_internal_result.status_code == 200
    first_internal_biology = next(
        item for item in first_internal_result.json()['subjects'] if item['id'] == subject_id
    )
    assert first_internal_biology['students'][0]['latest_assessment']['assessment'] == 'FA-1'
    assert first_internal_biology['students'][0]['status'] == 'NOT_LOW'
    assert first_internal_biology['students'][0]['official_failure'] is True
    assert first_internal_biology['students'][0]['support_required'] is True
    assert first_internal_biology['students'][0]['support_triggers'] == ['OFFICIAL_FAIL']
    assert first_internal_biology['students'][0]['latest_assessment']['score'] == 22
    assert first_internal_biology['students'][0]['latest_assessment']['maximum_marks'] == 40
    assert first_internal_biology['students'][0]['latest_assessment']['normalized_score'] == 13.75

    student_result = client.get('/api/v1/students/me/subject-focus', headers=auth(student_token))
    assert student_result.status_code == 200
    student_biology = next(item for item in student_result.json()['subjects'] if item['id'] == subject_id)
    assert student_biology['status'] == 'LOW'
    student_first_internal = client.get(
        '/api/v1/students/me/subject-focus?first_internal_only=true',
        headers=auth(student_token),
    )
    assert student_first_internal.status_code == 200
    student_first_biology = next(
        item for item in student_first_internal.json()['subjects'] if item['id'] == subject_id
    )
    assert student_first_biology['status'] == 'NOT_LOW'
    assert [item['assessment'] for item in student_first_biology['history']] == ['FA-2', 'FA-1']
    assert client.get('/api/v1/teacher/subject-focus', headers=auth(student_token)).status_code == 403


def test_teacher_subject_focus_requires_direct_course_assignment_within_department(client, teacher_token):
    factory = client.app.state.test_session_factory
    with factory() as db:
        teacher_user = db.query(User).filter_by(email='teacher@example.com').one()
        teacher = Teacher(user_id=teacher_user.id, department='Science')
        subject = Subject(code='BIO-SCOPE', name='Biology', department='Science')
        db.add_all([teacher, subject])
        db.flush()
        student = db.query(User).filter_by(email='student@example.com').one().student
        course = Course(
            name='Biology',
            code='BIO-SCOPE-A',
            section='A',
            subject_id=subject.id,
            teacher_id=teacher.id,
        )
        db.add(course)
        db.flush()
        student.course_id = course.id
        chemistry = Subject(code='CHEM-SCOPE', name='Chemistry', department='Science')
        db.add(chemistry)
        db.commit()
        chemistry_id = chemistry.id

    result = client.get('/api/v1/teacher/subject-focus', headers=auth(teacher_token))

    assert result.status_code == 200, result.text
    assert [item['code'] for item in result.json()['subjects']] == ['BIO-SCOPE']
    denied = client.get(
        f'/api/v1/teacher/subject-focus?subject_id={chemistry_id}',
        headers=auth(teacher_token),
    )
    assert denied.status_code == 404


@pytest.mark.parametrize('eligible_subject_count', [1, 2, 4, 5, 10])
def test_student_subject_focus_returns_every_eligible_subject_without_a_fixed_limit(
    client,
    student_token,
    monkeypatch,
    eligible_subject_count,
):
    factory = client.app.state.test_session_factory
    with factory() as db:
        student = db.query(User).filter_by(email='student@example.com').one().student
        other_student = db.query(User).filter_by(email='other@example.com').one().student
        db.add(SubjectFocusPolicy(
            version=1,
            threshold_score=12,
            threshold_maximum=25,
            change_reason='Project policy',
        ))
        subjects = [
            Subject(code=f'REC-{index:02d}', name=f'Recovery subject {index}', department='Science')
            for index in range(eligible_subject_count)
        ]
        db.add_all(subjects)
        db.flush()
        db.add_all([
            StudentSubjectAcademicRecord(
                student_id=student.id,
                subject_id=subject.id,
                academic_period='2026-S1',
            )
            for index, subject in enumerate(subjects)
        ])
        db.add(StudentSubjectAcademicRecord(
            student_id=other_student.id,
            subject_id=subjects[0].id,
            academic_period='2026-S1',
            fa1=11,
            fa1_observed_at=datetime(2026, 10, 20),
        ))
        expected_subject_ids = {subject.id for subject in subjects}
        student_id = student.id
        db.commit()

    verified_fa1_marks = {
        (student_id, subject_id): [Mark(
            id=index + 1,
            student_id=student_id,
            assessment=Assessment(
                id=index + 1,
                title=f'FA-1 verified assessment {index + 1}',
                maximum_marks=25,
                maximum_marks_verified=True,
            ),
            score=11,
            recorded_at=datetime(2026, 10, index + 1),
        )]
        for index, subject_id in enumerate(sorted(expected_subject_ids))
    }
    monkeypatch.setattr(
        'backend.app.services.subject_focus._load_marks_by_student_subject',
        lambda *_args, **_kwargs: verified_fa1_marks,
    )

    response = client.get(
        '/api/v1/students/me/subject-focus?first_internal_only=true',
        headers=auth(student_token),
    )

    assert response.status_code == 200, response.text
    returned_subjects = response.json()['subjects']
    assert {subject['id'] for subject in returned_subjects} == expected_subject_ids
    assert len(returned_subjects) == eligible_subject_count
    assert all(subject['status'] == 'LOW' for subject in returned_subjects)
    assert all(subject['support_triggers'] == ['LOW_SCORE'] for subject in returned_subjects)


def test_subject_focus_policy_changes_are_admin_only_versioned_and_audited(client, teacher_token):
    factory = client.app.state.test_session_factory
    with factory() as db:
        db.add(SubjectFocusPolicy(version=1, threshold_score=12, threshold_maximum=25, change_reason='Project policy'))
        admin = User(
            email='admin@example.com',
            full_name='Policy Administrator',
            role='ADMIN',
            password_hash=hash_password('admin-pass'),
        )
        db.add(admin)
        db.commit()
    admin_token = client.post('/api/v1/auth/login', json={
        'email': 'admin@example.com',
        'password': 'admin-pass',
    }).json()['access_token']

    denied = client.put('/api/v1/admin/subject-focus-policy', headers=auth(teacher_token), json={
        'expected_version': 1,
        'threshold_score': 11,
        'threshold_maximum': 25,
        'change_reason': 'Approved rule update',
    })
    assert denied.status_code == 403

    invalid_scale = client.put('/api/v1/admin/subject-focus-policy', headers=auth(admin_token), json={
        'expected_version': 1,
        'threshold_score': 12,
        'threshold_maximum': 20,
        'change_reason': 'Reject non-25 scale',
    })
    assert invalid_scale.status_code == 422

    updated = client.put('/api/v1/admin/subject-focus-policy', headers=auth(admin_token), json={
        'expected_version': 1,
        'threshold_score': 11,
        'threshold_maximum': 25,
        'change_reason': 'Approved rule update',
    })
    assert updated.status_code == 200
    assert updated.json()['version'] == 2
    history = client.get('/api/v1/admin/subject-focus-policy', headers=auth(admin_token))
    assert history.status_code == 200
    assert [item['version'] for item in history.json()['history']] == [2, 1]
    with factory() as db:
        audit = db.query(AuditLog).filter_by(action='subject_focus_policy.changed').one()
        assert audit.detail['new_threshold'] == {'score': 11, 'maximum': 25}
