from datetime import date, datetime
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.models import (
    Course,
    QuizAttempt,
    RecoveryGenerationJob,
    RecoveryPlan,
    RecoveryQuiz,
    RecoveryTask,
    RecoveryTopic,
    Student,
    StudentSubjectAcademicRecord,
    Subject,
    SupportPlanVersion,
    Teacher,
    User,
)
from backend.app.services.auth import hash_password
from backend.app.services.recovery_quizzes import (
    aggregate_recovery_score,
    is_backlog,
    schedule_topics,
    validate_recovery_quiz_response,
)


def auth(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def add_recovery_class(client, *, student_id: int | None = None, code: str = 'BIO-REC') -> tuple[int, int]:
    factory = client.app.state.test_session_factory
    with factory() as db:
        teacher_user = db.query(User).filter_by(email='teacher@example.com').one()
        teacher = Teacher(user_id=teacher_user.id, department='Science')
        subject = Subject(name='Biology', code=code, department='Science')
        db.add_all([teacher, subject])
        db.flush()
        course = Course(
            name='Biology A',
            code=f'{code}-A',
            section='A',
            subject_id=subject.id,
            teacher_id=teacher.id,
        )
        db.add(course)
        db.flush()
        student = db.query(Student).filter_by(
            id=student_id or client.app.state.test_student_id,
        ).one()
        student.course_id = course.id
        db.add(StudentSubjectAcademicRecord(
            student_id=student.id,
            subject_id=subject.id,
            academic_period='2026-S1',
            fa1=11,
            fa1_maximum_marks=25,
            fa1_maximum_verified=True,
        ))
        db.commit()
        return student.id, subject.id


def test_backlog_detection_normalizes_to_configured_scale():
    assert is_backlog(23.99, 50, pass_mark=12, maximum_marks=25)
    assert not is_backlog(24, 50, pass_mark=12, maximum_marks=25)
    assert not is_backlog(12, None, pass_mark=12, maximum_marks=25)
    with pytest.raises(ValueError):
        is_backlog(26, 25, pass_mark=12, maximum_marks=25)


def test_recovery_pass_mark_cannot_exceed_the_configured_maximum():
    with pytest.raises(ValueError, match='RECOVERY_PASS_MARK'):
        Settings(recovery_pass_mark=26, recovery_maximum_marks=25)


def test_recovery_settings_have_safe_defaults_without_env_file(monkeypatch):
    for name in (
        'RECOVERY_PASS_MARK',
        'RECOVERY_MAXIMUM_MARKS',
        'RECOVERY_AI_RATE_LIMIT_PER_HOUR',
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.recovery_pass_mark == 12
    assert settings.recovery_maximum_marks == 25
    assert settings.recovery_ai_rate_limit_per_hour == 10


def test_overview_uses_every_fa1_subject_for_marks_backlogs_and_library(client, teacher_token):
    student_id, biology_id = add_recovery_class(client)
    factory = client.app.state.test_session_factory
    with factory() as db:
        db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student_id,
            subject_id=biology_id,
        ).one().fa1 = 9
        chemistry = Subject(name='Chemistry', code='CHEM-REC', department='Science')
        physics = Subject(name='Physics', code='PHYS-REC', department='Science')
        db.add_all([chemistry, physics])
        db.flush()
        db.add_all([
            StudentSubjectAcademicRecord(
                student_id=student_id,
                subject_id=chemistry.id,
                academic_period='2026-S1',
                fa1=14,
                fa1_maximum_marks=25,
                fa1_maximum_verified=True,
            ),
            StudentSubjectAcademicRecord(
                student_id=student_id,
                subject_id=physics.id,
                academic_period='2026-S1',
                fa1=11,
                fa1_maximum_marks=25,
                fa1_maximum_verified=True,
            ),
        ])
        db.commit()
        physics_id = physics.id

    response = client.get('/api/v1/teacher/recovery/overview', headers=auth(teacher_token))
    assert response.status_code == 200
    data = response.json()
    assert {subject['code'] for subject in data['subjects']} == {
        'BIO-REC',
        'CHEM-REC',
        'PHYS-REC',
    }
    assert all(
        [module['module_no'] for module in subject['modules']] == [1, 2, 3, 4, 5]
        for subject in data['subjects']
    )
    assert all(not module['uploaded'] for subject in data['subjects'] for module in subject['modules'])

    assert len(data['students']) == 1
    subjects = {subject['code']: subject for subject in data['students'][0]['subjects']}
    assert {
        code: (subject['fa1'], subject['maximum'], subject['backlog'])
        for code, subject in subjects.items()
    } == {
        'BIO-REC': (9, 25, True),
        'CHEM-REC': (14, 25, False),
        'PHYS-REC': (11, 25, True),
    }
    assert data['stats'] == {
        'students_with_backlogs': 1,
        'backlog_subjects': 2,
        'quizzes_assigned': 0,
        'average_recovery_score': 0.0,
    }

    plan_response = client.post(
        f'/api/v1/teacher/recovery/students/{student_id}/subjects/{physics_id}/plans',
        json={},
        headers=auth(teacher_token),
    )
    assert plan_response.status_code == 200
    assert plan_response.json()['marks'] == 11


def test_freshly_migrated_empty_database_returns_zero_stats_and_no_students(tmp_path, monkeypatch):
    database_path = tmp_path / 'empty-recovery.sqlite'
    database_url = f'sqlite:///{database_path.as_posix()}'
    monkeypatch.setenv('DATABASE_URL', database_url)
    config_path = Path(__file__).resolve().parents[1] / 'alembic.ini'
    command.upgrade(Config(str(config_path)), 'head')

    engine = create_engine(database_url, connect_args={'check_same_thread': False})
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        teacher_user = User(
            email='maya@example.com',
            full_name='Dr. Maya Chen',
            role='TEACHER',
            password_hash=hash_password('local-test-password'),
        )
        db.add(teacher_user)
        db.flush()
        db.add(Teacher(user_id=teacher_user.id, department='Science'))
        db.commit()

    original_overrides = app.dependency_overrides.copy()

    def override_get_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            login = client.post(
                '/api/v1/auth/login',
                json={'email': 'maya@example.com', 'password': 'local-test-password'},
            )
            assert login.status_code == 200
            response = client.get(
                '/api/v1/teacher/recovery/overview',
                headers=auth(login.json()['access_token']),
            )
        assert response.status_code == 200
        assert response.json()['stats'] == {
            'students_with_backlogs': 0,
            'backlog_subjects': 0,
            'quizzes_assigned': 0,
            'average_recovery_score': 0.0,
        }
        assert response.json()['subjects'] == []
        assert response.json()['students'] == []
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        engine.dispose()


def test_recovery_score_aggregates_using_attempt_maxima():
    assert aggregate_recovery_score([(5, 10), (6, 20)]) == 36.7
    assert aggregate_recovery_score([]) is None


def test_generated_quizzes_must_be_grounded_in_the_selected_module():
    source_text = 'The nucleus stores genetic information. Mitochondria release energy in the cell.'

    def question(text: str, *, question_type: str = 'SHORT_ANSWER', answer: str = 'The nucleus') -> dict:
        return {
            'question': text,
            'module_no': 1,
            'question_type': question_type,
            'options': ['The nucleus', 'A cell wall', 'A ribosome', 'A membrane'] if question_type == 'MCQ' else [],
            'correct_answer': 'A' if question_type == 'MCQ' else answer,
            'explanation': 'The source text describes the nucleus.',
            'marks': 2,
            'source_excerpt': 'The nucleus stores genetic information.',
        }

    response = {
        'topic_quizzes': [{
            'topic_id': 9,
            'questions': [
                question(f'Question {index}: which structure is described?', question_type='MCQ')
                for index in range(5)
            ],
        }],
        'one_line_questions': [
            question(f'Question {index}: what does the nucleus store?')
            for index in range(5)
        ],
    }
    valid = validate_recovery_quiz_response(
        json.dumps(response),
        topic_ids={9},
        source_text_by_module={1: source_text},
        topic_modules={9: 1},
    )
    assert len(valid['topic_quizzes'][0]['questions']) == 5
    response['one_line_questions'][0]['correct_answer'] = 'An invented fact'
    with pytest.raises(ValueError, match='not supported'):
        validate_recovery_quiz_response(
            json.dumps(response),
            topic_ids={9},
            source_text_by_module={1: source_text},
            topic_modules={9: 1},
        )


def test_topic_schedule_groups_by_configured_daily_capacity():
    topics = ['one', 'two', 'three', 'four', 'five']
    schedule = schedule_topics(topics, topics_per_day=2, start_date=date(2026, 11, 18))
    assert [(item['topic'], item['day_no'], item['scheduled_date']) for item in schedule] == [
        ('one', 1, date(2026, 11, 18)),
        ('two', 1, date(2026, 11, 18)),
        ('three', 2, date(2026, 11, 19)),
        ('four', 2, date(2026, 11, 19)),
        ('five', 3, date(2026, 11, 20)),
    ]


def test_teacher_can_create_only_an_assigned_student_subject_plan(client, teacher_token):
    student_id, subject_id = add_recovery_class(client)
    response = client.post(
        f'/api/v1/teacher/recovery/students/{student_id}/subjects/{subject_id}/plans',
        json={'topics_per_day': 2},
        headers=auth(teacher_token),
    )
    assert response.status_code == 200
    assert response.json()['marks'] == 11
    assert response.json()['selected_modules'] == []
    assert response.json()['status'] == 'draft'
    assert client.post(
        f'/api/v1/teacher/recovery/students/{client.app.state.test_other_student_id}/subjects/{subject_id}/plans',
        json={},
        headers=auth(teacher_token),
    ).status_code == 404


def test_unchanged_topic_review_keeps_quizzes_but_changed_selection_invalidates_them(client, teacher_token):
    student_id, subject_id = add_recovery_class(client)
    with client.app.state.test_session_factory() as db:
        user = db.query(User).filter_by(email='teacher@example.com').one()
        plan = RecoveryPlan(
            student_id=student_id,
            subject_id=subject_id,
            source_exam='FA-1',
            marks=11,
            selected_modules=[1],
            status='quizzes_ready',
            created_by=user.id,
        )
        db.add(plan)
        db.flush()
        topics = [
            RecoveryTopic(plan_id=plan.id, module_no=1, name=f'Cell structure {index}', included=True)
            for index in range(2)
        ]
        db.add_all(topics)
        db.flush()
        for topic in topics:
            db.add(RecoveryQuiz(
                plan_id=plan.id,
                topic_id=topic.id,
                quiz_key=f'topic:{topic.id}',
                max_score=10,
                questions=[],
            ))
        db.add(RecoveryQuiz(
            plan_id=plan.id,
            topic_id=None,
            quiz_key='one-line',
            max_score=10,
            questions=[],
        ))
        db.commit()
        plan_id = plan.id
        topic_ids = [topic.id for topic in topics]

    same_selection = client.patch(
        f'/api/v1/teacher/recovery/plans/{plan_id}/topics',
        json={'topics': [{'topic_id': topic_id, 'included': True} for topic_id in topic_ids]},
        headers=auth(teacher_token),
    )
    assert same_selection.status_code == 200
    assert same_selection.json()['status'] == 'quizzes_ready'
    assert len(same_selection.json()['quizzes']) == 3

    changed_selection = client.patch(
        f'/api/v1/teacher/recovery/plans/{plan_id}/topics',
        json={
            'topics': [
                {'topic_id': topic_ids[0], 'included': False},
                {'topic_id': topic_ids[1], 'included': True},
            ],
        },
        headers=auth(teacher_token),
    )
    assert changed_selection.status_code == 200
    assert changed_selection.json()['status'] == 'topics_ready'
    assert changed_selection.json()['quizzes'] == []


def test_teacher_overview_aggregates_real_backlogs_and_quiz_attempts(client, teacher_token):
    student_id, subject_id = add_recovery_class(client)
    with client.app.state.test_session_factory() as db:
        plan = RecoveryPlan(
            student_id=student_id,
            subject_id=subject_id,
            source_exam='FA-1',
            marks=11,
            selected_modules=[1],
            topics_per_day=1,
            status='assigned',
            created_by=db.query(User).filter_by(email='teacher@example.com').one().id,
        )
        db.add(plan)
        db.flush()
        topic = RecoveryTopic(plan_id=plan.id, module_no=1, name='Cell structure', included=True)
        db.add(topic)
        db.flush()
        quiz = RecoveryQuiz(
            plan_id=plan.id,
            topic_id=topic.id,
            quiz_key=f'topic:{topic.id}',
            max_score=10,
            questions=[],
        )
        db.add(quiz)
        db.flush()
        roadmap = SupportPlanVersion(
            student_id=student_id,
            subject_id=subject_id,
            version=1,
            summary='Test recovery plan',
            plan_json={},
            status='PUBLISHED',
        )
        db.add(roadmap)
        db.flush()
        task = RecoveryTask(
            roadmap_version_id=roadmap.id,
            student_id=student_id,
            subject_id=subject_id,
            recovery_plan_id=plan.id,
            recovery_topic_id=topic.id,
            recovery_quiz_id=quiz.id,
            day_no=1,
            task_key=f'test-quiz-{quiz.id}',
            learning_objective='Practice cell structure.',
            title='Cell structure',
            instructions='Answer the questions.',
            activity_type='QUIZ',
            estimated_minutes=10,
            completion_criteria='Submit the quiz.',
            status='COMPLETED',
        )
        db.add(task)
        db.flush()
        db.add(QuizAttempt(
            task_id=task.id,
            student_id=student_id,
            score=8,
            max_score=10,
            answers={},
            submitted_at=datetime(2026, 11, 18, 12),
        ))
        db.commit()

    response = client.get('/api/v1/teacher/recovery/overview', headers=auth(teacher_token))
    assert response.status_code == 200
    assert response.json()['stats'] == {
        'students_with_backlogs': 1,
        'backlog_subjects': 1,
        'quizzes_assigned': 1,
        'average_recovery_score': 80,
    }


def test_backlog_aggregates_all_fa1_subjects_for_students_in_teacher_classes(client, teacher_token):
    student_id, biology_id = add_recovery_class(client)
    other_student_id = client.app.state.test_other_student_id
    with client.app.state.test_session_factory() as db:
        teacher = db.query(Teacher).join(User).filter(User.email == 'teacher@example.com').one()
        other_subject = Subject(name='Chemistry', code='CHEM-REC', department='Science')
        db.add(other_subject)
        db.flush()
        course = Course(
            name='Chemistry B',
            code='CHEM-REC-B',
            section='B',
            subject_id=other_subject.id,
            teacher_id=teacher.id,
        )
        db.add(course)
        db.flush()
        db.query(Student).filter_by(id=other_student_id).one().course_id = course.id
        records = [
            StudentSubjectAcademicRecord(
                student_id=student_id,
                subject_id=other_subject.id,
                academic_period='2026-S1',
                fa1=8,
                fa1_maximum_marks=25,
            ),
            StudentSubjectAcademicRecord(
                student_id=other_student_id,
                subject_id=biology_id,
                academic_period='2026-S1',
                fa1=5,
                fa1_maximum_marks=25,
            ),
            StudentSubjectAcademicRecord(
                student_id=other_student_id,
                subject_id=other_subject.id,
                academic_period='2026-S1',
                fa1=18,
                fa1_maximum_marks=25,
            ),
        ]
        db.add_all(records)
        cross_course_plan = RecoveryPlan(
            student_id=student_id,
            subject_id=other_subject.id,
            source_exam='FA-1',
            marks=8,
            selected_modules=[1],
            status='assigned',
            created_by=teacher.user_id,
        )
        db.add(cross_course_plan)
        db.flush()
        db.add(RecoveryQuiz(
            plan_id=cross_course_plan.id,
            topic_id=None,
            quiz_key='one-line',
            max_score=10,
            questions=[],
        ))
        db.commit()

    response = client.get('/api/v1/teacher/recovery/overview', headers=auth(teacher_token))
    assert response.status_code == 200
    assert response.json()['stats']['students_with_backlogs'] == 2
    assert response.json()['stats']['backlog_subjects'] == 3
    assert response.json()['stats']['quizzes_assigned'] == 1


def test_student_cannot_update_teacher_recovery_plan(client, teacher_token, student_token):
    student_id, subject_id = add_recovery_class(client)
    with client.app.state.test_session_factory() as db:
        plan = RecoveryPlan(
            student_id=student_id,
            subject_id=subject_id,
            source_exam='FA-1',
            marks=11,
            selected_modules=[],
            status='assigned',
            created_by=db.query(User).filter_by(email='teacher@example.com').one().id,
        )
        db.add(plan)
        db.commit()
        plan_id = plan.id
    assert client.get(
        f'/api/v1/teacher/recovery/jobs/9999',
        headers=auth(teacher_token),
    ).status_code == 404
    assert client.patch(
        f'/api/v1/teacher/recovery/plans/{plan_id}',
        json={'topics_per_day': 2},
        headers=auth(student_token),
    ).status_code == 403


def test_stalled_generation_job_is_exposed_as_retryable_to_its_teacher(client, teacher_token):
    student_id, subject_id = add_recovery_class(client)
    with client.app.state.test_session_factory() as db:
        user = db.query(User).filter_by(email='teacher@example.com').one()
        plan = RecoveryPlan(
            student_id=student_id,
            subject_id=subject_id,
            source_exam='FA-1',
            marks=11,
            selected_modules=[1],
            status='draft',
            created_by=user.id,
        )
        db.add(plan)
        db.flush()
        job = RecoveryGenerationJob(
            plan_id=plan.id,
            kind='topics',
            status='running',
            idempotency_key='stalled-recovery-job',
            created_by=user.id,
            created_at=datetime(2026, 11, 18, 8),
            updated_at=datetime(2026, 11, 18, 8),
        )
        db.add(job)
        db.commit()
        job_id = job.id

    response = client.get(
        f'/api/v1/teacher/recovery/jobs/{job_id}',
        headers=auth(teacher_token),
    )
    assert response.status_code == 200
    assert response.json()['retryable'] is True


def test_teacher_performance_excludes_another_teachers_subject_quizzes(client, teacher_token):
    student_id, _ = add_recovery_class(client)
    with client.app.state.test_session_factory() as db:
        other_user = User(
            email='second-teacher@example.com',
            full_name='Second Teacher',
            role='TEACHER',
            password_hash='unused',
        )
        db.add(other_user)
        db.flush()
        other_teacher = Teacher(user_id=other_user.id, department='Science')
        other_subject = Subject(name='Chemistry', code='CHEM-PRIVATE', department='Science')
        db.add_all([other_teacher, other_subject])
        db.flush()
        db.add(Course(
            name='Chemistry B',
            code='CHEM-PRIVATE-B',
            section='B',
            subject_id=other_subject.id,
            teacher_id=other_teacher.id,
        ))
        plan = RecoveryPlan(
            student_id=student_id,
            subject_id=other_subject.id,
            source_exam='FA-1',
            marks=11,
            selected_modules=[1],
            status='assigned',
            created_by=other_user.id,
        )
        db.add(plan)
        db.flush()
        quiz = RecoveryQuiz(
            plan_id=plan.id,
            topic_id=None,
            quiz_key='one-line',
            max_score=10,
            questions=[],
        )
        roadmap = SupportPlanVersion(
            student_id=student_id,
            subject_id=other_subject.id,
            teacher_id=other_teacher.id,
            version=1,
            summary='Private chemistry recovery plan',
            plan_json={},
            status='PUBLISHED',
        )
        db.add_all([quiz, roadmap])
        db.flush()
        plan.support_plan_version_id = roadmap.id
        task = RecoveryTask(
            roadmap_version_id=roadmap.id,
            student_id=student_id,
            subject_id=other_subject.id,
            recovery_plan_id=plan.id,
            recovery_quiz_id=quiz.id,
            day_no=1,
            task_key='private-chemistry-quiz',
            learning_objective='Practice chemistry.',
            title='Private chemistry quiz',
            instructions='Answer the questions.',
            activity_type='QUIZ',
            estimated_minutes=10,
            completion_criteria='Submit the quiz.',
            status='COMPLETED',
        )
        db.add(task)
        db.flush()
        db.add(QuizAttempt(
            task_id=task.id,
            student_id=student_id,
            score=10,
            max_score=10,
            answers={},
            submitted_at=datetime(2026, 11, 18, 12),
        ))
        db.commit()

    response = client.get(
        f'/api/v1/teacher/recovery/students/{student_id}/performance',
        headers=auth(teacher_token),
    )
    assert response.status_code == 200
    assert response.json()['items'] == []


def test_assignment_schedules_topic_and_plan_quizzes_then_locks_modules(client, teacher_token):
    student_id, subject_id = add_recovery_class(client)
    with client.app.state.test_session_factory() as db:
        teacher_user = db.query(User).filter_by(email='teacher@example.com').one()
        plan = RecoveryPlan(
            student_id=student_id,
            subject_id=subject_id,
            source_exam='FA-1',
            marks=11,
            selected_modules=[1],
            topics_per_day=2,
            status='quizzes_ready',
            created_by=teacher_user.id,
        )
        db.add(plan)
        db.flush()
        topics = [
            RecoveryTopic(plan_id=plan.id, module_no=1, name=f'Cell structure {index}', included=True)
            for index in range(3)
        ]
        db.add_all(topics)
        db.flush()
        quizzes = [
            RecoveryQuiz(
                plan_id=plan.id,
                topic_id=topic.id,
                quiz_key=f'topic:{topic.id}',
                max_score=10,
                questions=[],
            )
            for topic in topics
        ]
        quizzes.append(RecoveryQuiz(
            plan_id=plan.id,
            topic_id=None,
            quiz_key='one-line',
            max_score=10,
            questions=[],
        ))
        db.add_all(quizzes)
        db.commit()
        plan_id = plan.id

    assigned = client.post(
        f'/api/v1/teacher/recovery/plans/{plan_id}/assign?start_date=2026-11-18&topics_per_day=2',
        headers=auth(teacher_token),
    )
    assert assigned.status_code == 200, assigned.text
    with client.app.state.test_session_factory() as db:
        tasks = db.query(RecoveryTask).filter(
            RecoveryTask.recovery_plan_id == plan_id,
        ).order_by(RecoveryTask.day_no, RecoveryTask.id).all()
        assert [(task.day_no, task.scheduled_date.isoformat()) for task in tasks] == [
            (1, '2026-11-18'),
            (1, '2026-11-18'),
            (2, '2026-11-19'),
            (2, '2026-11-19'),
        ]
    assert client.patch(
        f'/api/v1/teacher/recovery/plans/{plan_id}',
        json={'selected_modules': []},
        headers=auth(teacher_token),
    ).status_code == 409
