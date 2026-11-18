import json

import pytest
from backend.app.database import session
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from types import SimpleNamespace

from backend.app.database import get_db
from backend.app.database.session import create_database_engine
from backend.app.main import app
from backend.app.core.security import get_current_teacher


def test_engine_options_are_dialect_aware(monkeypatch, tmp_path):
    captured = {}
    engines = []

    def fake_create_engine(url, **options):
        captured['url'] = url
        captured['options'] = options
        if url.startswith('sqlite:'):
            engine = create_engine(url, **options)
            engines.append(engine)
            return engine
        return object()

    monkeypatch.setattr(session, 'create_engine', fake_create_engine)
    session.create_database_engine('postgresql+psycopg://user:pass@localhost/example')
    assert 'connect_args' not in captured['options']
    assert captured['options']['pool_pre_ping'] is True

    database_path = tmp_path / 'nested' / 'local.db'
    sqlite_engine = session.create_database_engine(f'sqlite:///{database_path.as_posix()}')
    assert captured['options']['connect_args'] == {'check_same_thread': False}
    assert database_path.parent.is_dir()
    with sqlite_engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1
    for engine in engines:
        engine.dispose()


def test_academic_import_migration_applies_to_sqlite_without_touching_project_db(monkeypatch, tmp_path):
    database_path = tmp_path / 'migration-check.db'
    database_url = f'sqlite:///{database_path.as_posix()}'
    monkeypatch.setattr('backend.app.config.get_settings', lambda: SimpleNamespace(database_url=database_url))
    configuration = Config('alembic.ini')

    command.upgrade(configuration, 'head')

    inspector = inspect(create_engine(database_url))
    assert {'academic_data_import_batches', 'academic_data_import_rows'} <= set(inspector.get_table_names())
    assert 'student_subject_academic_records' in inspector.get_table_names()
    assert 'student_subject_academic_record_history' in inspector.get_table_names()
    assert 'academic_risk_outcomes' in inspector.get_table_names()
    subject_record_columns = {column['name'] for column in inspector.get_columns('student_subject_academic_records')}
    assert {'academic_period', 'fa1_observed_at', 'fa2_observed_at', 'assignment_observed_at', 'attendance_observed_at'} <= subject_record_columns
    assert 'academic_record_id' in {column['name'] for column in inspector.get_columns('academic_data_import_rows')}
    assert 'subject_id' in {column['name'] for column in inspector.get_columns('assignments')}
    assert {'page_number', 'page_count'} <= {column['name'] for column in inspector.get_columns('submission_versions')}
    assert 'page_count' in {column['name'] for column in inspector.get_columns('submissions')}
    with create_engine(database_url).connect() as connection:
        subjects = set(connection.execute(__import__('sqlalchemy').text('SELECT code FROM subjects')).scalars())
    assert {'EVS', 'CN', 'TOC', 'RM', 'SE'} <= subjects
    assert 'import_batch_id' in {column['name'] for column in inspector.get_columns('academic_momentum')}
    registry_columns = {column['name'] for column in inspector.get_columns('model_registry')}
    assert {'rejected_by_user_id', 'rejected_at', 'rejection_reason'} <= registry_columns
    assert {'model_deployment_state', 'model_deployment_events'} <= set(inspector.get_table_names())
    deployment_event_columns = {column['name'] for column in inspector.get_columns('model_deployment_events')}
    assert {
        'event', 'previous_version', 'new_version', 'artifact_hash', 'actor_user_id',
        'actor_role', 'occurred_at', 'reason', 'status', 'rollback_of_event_id',
        'dataset_hash', 'feature_contract', 'evaluation_reference', 'previous_state', 'new_state',
    } <= deployment_event_columns
    with create_engine(database_url).connect() as connection:
        deployment_triggers = set(connection.execute(__import__('sqlalchemy').text(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'model_deployment_events'"
        )).scalars())
    assert deployment_triggers == {'trg_model_deployment_events_no_update', 'trg_model_deployment_events_no_delete'}
    assert {
        'created_by_user_id', 'dataset_id', 'dataset_version', 'dataset_fingerprint',
        'target_definition', 'aggregation_strategy', 'training_periods',
        'validation_periods', 'test_periods', 'algorithm', 'hyperparameters',
        'random_seed', 'validation_status', 'approval_status', 'approved_by_user_id',
        'approved_at', 'previous_production_model_version', 'promoted_at', 'retired_at',
    } <= registry_columns
    for table_name in ('attendance', 'assessments', 'marks', 'learning_activities'):
        assert 'import_batch_id' in {column['name'] for column in inspector.get_columns(table_name)}


def test_twenty_five_mark_policy_migration_preserves_policy_and_mark_history(monkeypatch, tmp_path):
    from datetime import datetime

    from backend.app.models import Assessment, Course, Mark, Student, Subject, SubjectFocusPolicy

    database_path = tmp_path / 'subject-policy.db'
    database_url = f'sqlite:///{database_path.as_posix()}'
    monkeypatch.setattr('backend.app.config.get_settings', lambda: SimpleNamespace(database_url=database_url))
    configuration = Config('alembic.ini')
    command.upgrade(configuration, 'h1i2j3k4l5')
    engine = create_database_engine(database_url)

    with Session(engine) as db:
        subject = db.query(Subject).filter_by(code='EVS').one()
        course = Course(name='Policy Test', code='POLICY-TEST', subject=subject)
        student = Student(student_id='policy-001', full_name='Policy Student', risk_level='HIGH')
        assessment = Assessment(
            course=course,
            title='Historical FA',
            maximum_marks=25,
            maximum_marks_verified=True,
        )
        mark = Mark(
            student=student,
            assessment=assessment,
            score=11,
            official_result='FAIL',
            recorded_at=datetime(2026, 10, 1),
        )
        db.add_all([
            course,
            student,
            assessment,
            mark,
            SubjectFocusPolicy(
                version=2,
                threshold_score=10,
                threshold_maximum=25,
                change_reason='Earlier policy version for migration regression test',
            ),
        ])
        db.commit()
        mark_id = mark.id

    command.upgrade(configuration, 'i2j3k4l5m6')

    with Session(engine) as db:
        policies = db.query(SubjectFocusPolicy).order_by(SubjectFocusPolicy.version).all()
        historical_mark = db.get(Mark, mark_id)
        assert [(policy.version, policy.threshold_score, policy.threshold_maximum) for policy in policies] == [
            (1, 12, 20),
            (2, 10, 25),
            (3, 12, 25),
        ]
        assert historical_mark.score == 11
        assert historical_mark.official_result == 'FAIL'
        assert historical_mark.assessment.maximum_marks == 25
        assert historical_mark.assessment.maximum_marks_verified is True

    engine.dispose()


def test_subject_scoped_recovery_migration_preserves_legacy_records(monkeypatch, tmp_path):
    from datetime import datetime

    from backend.app.models import Assessment, Course, Mark, Student

    database_path = tmp_path / 'legacy-recovery.db'
    database_url = f'sqlite:///{database_path.as_posix()}'
    monkeypatch.setattr('backend.app.config.get_settings', lambda: SimpleNamespace(database_url=database_url))
    configuration = Config('alembic.ini')

    command.upgrade(configuration, 'i2j3k4l5m6')
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        subject_id = connection.execute(text(
            "SELECT id FROM subjects WHERE code = 'EVS'"
        )).scalar_one()
        now = '2026-10-21 12:00:00'
        connection.execute(text(
            'INSERT INTO students '
            '(student_id, full_name, risk_level, risk_probability, previous_gpa, current_gpa, '
            'attendance, assessment, assignments_completed, momentum, created_at, updated_at) '
            'VALUES (:student_id, :full_name, :risk_level, :risk_probability, :previous_gpa, '
            ':current_gpa, :attendance, :assessment, :assignments_completed, :momentum, :created_at, :updated_at)'
        ), {
            'student_id': 'legacy-001',
            'full_name': 'Legacy Student',
            'risk_level': 'HIGH',
            'risk_probability': 0.8,
            'previous_gpa': 2.0,
            'current_gpa': 1.8,
            'attendance': 70.0,
            'assessment': 45.0,
            'assignments_completed': 50.0,
            'momentum': -0.2,
            'created_at': now,
            'updated_at': now,
        })
        student_id = connection.execute(text(
            "SELECT id FROM students WHERE student_id = 'legacy-001'"
        )).scalar_one()
        teacher_user_id = connection.execute(text(
            'INSERT INTO users (email, full_name, role, password_hash, created_at, updated_at) '
            'VALUES (:email, :full_name, :role, :password_hash, :created_at, :updated_at)'
        ), {
            'email': 'migration-teacher@example.com',
            'full_name': 'Migration Teacher',
            'role': 'TEACHER',
            'password_hash': 'unused-test-hash',
            'created_at': now,
            'updated_at': now,
        }).lastrowid
        teacher_id = connection.execute(text(
            'INSERT INTO teachers (user_id, department, created_at, updated_at) '
            'VALUES (:user_id, :department, :created_at, :updated_at)'
        ), {
            'user_id': teacher_user_id,
            'department': 'Science',
            'created_at': now,
            'updated_at': now,
        }).lastrowid
        course_id = connection.execute(text(
            'INSERT INTO courses (name, code, section, subject_id, teacher_id, created_at, updated_at) '
            'VALUES (:name, :code, :section, :subject_id, :teacher_id, :created_at, :updated_at)'
        ), {
            'name': 'Environmental Studies',
            'code': 'MIGRATION-EVS-A',
            'section': 'A',
            'subject_id': subject_id,
            'teacher_id': teacher_id,
            'created_at': now,
            'updated_at': now,
        }).lastrowid
        connection.execute(
            text('UPDATE students SET course_id = :course_id WHERE id = :student_id'),
            {'course_id': course_id, 'student_id': student_id},
        )
        definitions = [
            {'task_key': f'subject-task-{number}', 'subject_id': subject_id}
            for number in range(1, 6)
        ]
        connection.execute(text(
            'INSERT INTO support_plan_versions '
            '(student_id, version, summary, plan_json, created_at, updated_at, status, evidence_json) '
            'VALUES (:student_id, 1, :summary, :plan_json, :created_at, :updated_at, :status, :evidence_json)'
        ), {
            'student_id': student_id,
            'summary': 'Legacy roadmap with one unresolved task',
            'plan_json': json.dumps({'tasks': definitions}),
            'created_at': now,
            'updated_at': now,
            'status': 'PUBLISHED',
            'evidence_json': '{"source":"legacy"}',
        })
        plan_id = connection.execute(text(
            'SELECT id FROM support_plan_versions WHERE student_id = :student_id'
        ), {'student_id': student_id}).scalar_one()
        task_ids = {}
        task_insert = text(
            'INSERT INTO recovery_tasks '
            '(roadmap_version_id, student_id, task_key, learning_objective, title, instructions, '
            'activity_type, estimated_minutes, completion_criteria, created_at, updated_at) '
            'VALUES (:plan_id, :student_id, :task_key, :objective, :title, :instructions, '
            ':activity_type, :minutes, :criteria, :created_at, :updated_at)'
        )
        for task_key in [definition['task_key'] for definition in definitions] + ['legacy-task-1']:
            connection.execute(task_insert, {
                'plan_id': plan_id,
                'student_id': student_id,
                'task_key': task_key,
                'objective': 'Review the recorded lesson.',
                'title': task_key,
                'instructions': 'Use the existing course material.',
                'activity_type': 'REVIEW',
                'minutes': 20,
                'criteria': 'Submit a response.',
                'created_at': now,
                'updated_at': now,
            })
            task_ids[task_key] = connection.execute(
                text('SELECT id FROM recovery_tasks WHERE task_key = :task_key'),
                {'task_key': task_key},
            ).scalar_one()

        event_insert = text(
            'INSERT INTO recovery_task_events '
            '(task_id, student_id, event_type, detail, idempotency_key, created_at) '
            'VALUES (:task_id, :student_id, :event_type, :detail, :key, :created_at)'
        )
        for event_number in range(1, 11):
            task_key = f'subject-task-{(event_number - 1) % 5 + 1}'
            connection.execute(event_insert, {
                'task_id': task_ids[task_key],
                'student_id': student_id,
                'event_type': 'STARTED' if event_number == 1 else 'PROGRESS',
                'detail': json.dumps({'legacy_event': event_number}),
                'key': f'scoped-event-{event_number}',
                'created_at': now,
            })
        for event_number in range(1, 4):
            connection.execute(event_insert, {
                'task_id': task_ids['legacy-task-1'],
                'student_id': student_id,
                'event_type': 'STARTED' if event_number == 1 else 'PROGRESS',
                'detail': json.dumps({'ambiguous_event': event_number}),
                'key': f'ambiguous-event-{event_number}',
                'created_at': now,
            })

    with Session(engine) as db:
        assessment_course = db.get(Course, course_id)
        assessed_student = db.get(Student, student_id)
        historical_assessment = Assessment(
            course_id=assessment_course.id,
            title='Historical FA',
            maximum_marks=25,
            maximum_marks_verified=True,
        )
        db.add(historical_assessment)
        db.flush()
        historical_mark = Mark(
            student_id=assessed_student.id,
            assessment_id=historical_assessment.id,
            score=11,
            official_result='FAIL',
            recorded_at=datetime(2026, 10, 1),
        )
        db.add(historical_mark)
        db.commit()
        historical_mark_id = historical_mark.id

    def override_db():
        with Session(engine) as db:
            yield db

    def override_teacher():
        return SimpleNamespace(id=teacher_user_id, role='TEACHER')

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_teacher] = override_teacher
    test_client = TestClient(app, raise_server_exceptions=False)
    try:
        legacy_response = test_client.get('/api/v1/teacher/recovery-monitoring')
        assert legacy_response.status_code == 500
    finally:
        app.dependency_overrides.clear()
        test_client.close()

    command.upgrade(configuration, 'head')

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_teacher] = override_teacher
    test_client = TestClient(app)
    try:
        migrated_response = test_client.get('/api/v1/teacher/recovery-monitoring')
        assert migrated_response.status_code == 200, migrated_response.text
        monitoring = migrated_response.json()
    finally:
        app.dependency_overrides.clear()
        test_client.close()
    assert monitoring['students'][0]['id'] == student_id
    expected_task_ids = {task_ids[f'subject-task-{number}'] for number in range(1, 6)}
    assert {task['id'] for task in monitoring['tasks']} == expected_task_ids
    assert task_ids['legacy-task-1'] not in {task['id'] for task in monitoring['tasks']}

    with engine.connect() as connection:
        plan = connection.execute(text(
            'SELECT subject_id, subject_scope_review_required, is_blueprint_group, summary, plan_json '
            'FROM support_plan_versions WHERE id = :id'
        ), {'id': plan_id}).mappings().one()
        task = connection.execute(text(
            'SELECT subject_id, subject_scope_review_required, task_key, learning_objective '
            'FROM recovery_tasks WHERE id = :id'
        ), {'id': task_ids['legacy-task-1']}).mappings().one()
        event = connection.execute(text(
            'SELECT subject_id, event_type, detail FROM recovery_task_events WHERE idempotency_key = :key'
        ), {'key': 'ambiguous-event-1'}).mappings().one()
        scoped_tasks = connection.execute(text(
            'SELECT subject_id, subject_scope_review_required FROM recovery_tasks '
            'WHERE task_key LIKE :prefix'
        ), {'prefix': 'subject-task-%'}).mappings().all()
        scoped_event = connection.execute(text(
            'SELECT subject_id, detail FROM recovery_task_events WHERE idempotency_key = :key'
        ), {'key': 'scoped-event-1'}).mappings().one()
        counts = {
            table: connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar_one()
            for table in ('support_plan_versions', 'recovery_tasks', 'recovery_task_events')
        }
        ambiguous_event_counts = connection.execute(text(
            'SELECT COUNT(*), SUM(CASE WHEN subject_id IS NULL THEN 1 ELSE 0 END) '
            'FROM recovery_task_events WHERE idempotency_key LIKE :prefix'
        ), {'prefix': 'ambiguous-event-%'}).one()
        scoped_event_counts = connection.execute(text(
            'SELECT COUNT(*), SUM(CASE WHEN subject_id = :subject_id THEN 1 ELSE 0 END) '
            'FROM recovery_task_events WHERE idempotency_key LIKE :prefix'
        ), {'subject_id': subject_id, 'prefix': 'scoped-event-%'}).one()
        current_policy = connection.execute(text(
            'SELECT threshold_score, threshold_maximum FROM subject_focus_policies ORDER BY version DESC LIMIT 1'
        )).one()

    assert plan['subject_id'] == subject_id
    assert not bool(plan['subject_scope_review_required'])
    assert not bool(plan['is_blueprint_group'])
    assert plan['summary'] == 'Legacy roadmap with one unresolved task'
    assert task['subject_id'] is None
    assert bool(task['subject_scope_review_required'])
    assert task['task_key'] == 'legacy-task-1'
    assert task['learning_objective'] == 'Review the recorded lesson.'
    assert event['subject_id'] is None
    assert event['event_type'] == 'STARTED'
    assert event['detail'] == '{"ambiguous_event": 1}'
    assert len(scoped_tasks) == 5
    assert all(row['subject_id'] == subject_id for row in scoped_tasks)
    assert all(not bool(row['subject_scope_review_required']) for row in scoped_tasks)
    assert scoped_event['subject_id'] == subject_id
    assert scoped_event['detail'] == '{"legacy_event": 1}'
    assert counts == {'support_plan_versions': 1, 'recovery_tasks': 6, 'recovery_task_events': 13}
    assert tuple(ambiguous_event_counts) == (3, 3)
    assert tuple(scoped_event_counts) == (10, 10)
    assert tuple(current_policy) == (12.0, 25.0)
    with Session(engine) as db:
        persisted_mark = db.get(Mark, historical_mark_id)
        assert persisted_mark.score == 11
        assert persisted_mark.official_result == 'FAIL'
        assert persisted_mark.assessment.maximum_marks == 25
        assert persisted_mark.assessment.maximum_marks_verified is True

    inspector = inspect(engine)
    for table_name in ('support_plan_versions', 'recovery_tasks', 'recovery_task_events'):
        subject_fk = next(
            foreign_key for foreign_key in inspector.get_foreign_keys(table_name)
            if foreign_key['constrained_columns'] == ['subject_id']
        )
        assert subject_fk['options']['ondelete'] == 'RESTRICT'

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text('DELETE FROM subjects WHERE id = :subject_id'), {'subject_id': subject_id})
    with engine.connect() as connection:
        assert connection.execute(
            text('SELECT COUNT(*) FROM support_plan_versions WHERE subject_id = :subject_id'),
            {'subject_id': subject_id},
        ).scalar_one() == 1
    engine.dispose()


def test_subject_scoped_recovery_migration_rejects_boolean_subject_ids(monkeypatch, tmp_path):
    database_path = tmp_path / 'boolean-subject-id.db'
    database_url = f'sqlite:///{database_path.as_posix()}'
    monkeypatch.setattr('backend.app.config.get_settings', lambda: SimpleNamespace(database_url=database_url))
    configuration = Config('alembic.ini')
    command.upgrade(configuration, 'i2j3k4l5m6')
    engine = create_database_engine(database_url)

    with engine.begin() as connection:
        subject_id = connection.execute(text(
            "SELECT id FROM subjects WHERE code = 'EVS'"
        )).scalar_one()
        assert subject_id == 1
        now = '2026-10-21 12:00:00'
        connection.execute(text(
            'INSERT INTO students '
            '(student_id, full_name, risk_level, risk_probability, previous_gpa, current_gpa, '
            'attendance, assessment, assignments_completed, momentum, created_at, updated_at) '
            'VALUES (:student_id, :full_name, :risk_level, :risk_probability, :previous_gpa, '
            ':current_gpa, :attendance, :assessment, :assignments_completed, :momentum, :created_at, :updated_at)'
        ), {
            'student_id': 'boolean-subject-001',
            'full_name': 'Boolean Subject Test Student',
            'risk_level': 'HIGH',
            'risk_probability': 0.8,
            'previous_gpa': 2.0,
            'current_gpa': 1.8,
            'attendance': 70.0,
            'assessment': 45.0,
            'assignments_completed': 50.0,
            'momentum': -0.2,
            'created_at': now,
            'updated_at': now,
        })
        student_id = connection.execute(text(
            "SELECT id FROM students WHERE student_id = 'boolean-subject-001'"
        )).scalar_one()
        definitions = [
            {'task_key': 'valid-subject-task', 'subject_id': subject_id},
            {'task_key': 'boolean-true-task', 'subject_id': True},
            {'task_key': 'boolean-false-task', 'subject_id': False},
            {'task_key': 'null-subject-task', 'subject_id': None},
            {'task_key': 'string-subject-task', 'subject_id': '1'},
            {'task_key': 'float-subject-task', 'subject_id': 1.0},
            {'task_key': 'zero-subject-task', 'subject_id': 0},
            {'task_key': 'negative-subject-task', 'subject_id': -1},
        ]
        connection.execute(text(
            'INSERT INTO support_plan_versions '
            '(student_id, version, summary, plan_json, created_at, updated_at, status, evidence_json) '
            'VALUES (:student_id, 1, :summary, :plan_json, :created_at, :updated_at, :status, :evidence_json)'
        ), {
            'student_id': student_id,
            'summary': 'Legacy plan with invalid task subject IDs',
            'plan_json': json.dumps({'tasks': definitions}),
            'created_at': now,
            'updated_at': now,
            'status': 'PUBLISHED',
            'evidence_json': '{}',
        })
        plan_id = connection.execute(text(
            'SELECT id FROM support_plan_versions WHERE student_id = :student_id'
        ), {'student_id': student_id}).scalar_one()
        task_insert = text(
            'INSERT INTO recovery_tasks '
            '(roadmap_version_id, student_id, task_key, learning_objective, title, instructions, '
            'activity_type, estimated_minutes, completion_criteria, created_at, updated_at) '
            'VALUES (:plan_id, :student_id, :task_key, :objective, :title, :instructions, '
            ':activity_type, :minutes, :criteria, :created_at, :updated_at)'
        )
        for definition in definitions:
            connection.execute(task_insert, {
                'plan_id': plan_id,
                'student_id': student_id,
                'task_key': definition['task_key'],
                'objective': 'Review the recorded lesson.',
                'title': definition['task_key'],
                'instructions': 'Use the existing course material.',
                'activity_type': 'REVIEW',
                'minutes': 20,
                'criteria': 'Submit a response.',
                'created_at': now,
                'updated_at': now,
            })
        boolean_task_id = connection.execute(text(
            "SELECT id FROM recovery_tasks WHERE task_key = 'boolean-true-task'"
        )).scalar_one()
        connection.execute(text(
            'INSERT INTO recovery_task_events '
            '(task_id, student_id, event_type, detail, idempotency_key, created_at) '
            'VALUES (:task_id, :student_id, :event_type, :detail, :key, :created_at)'
        ), {
            'task_id': boolean_task_id,
            'student_id': student_id,
            'event_type': 'PROGRESS',
            'detail': '{"legacy": true}',
            'key': 'boolean-subject-event',
            'created_at': now,
        })

    command.upgrade(configuration, 'head')

    with engine.connect() as connection:
        tasks = {
            row['task_key']: row
            for row in connection.execute(text(
                'SELECT task_key, subject_id, subject_scope_review_required FROM recovery_tasks '
                'WHERE roadmap_version_id = :plan_id'
            ), {'plan_id': plan_id}).mappings()
        }
        event_subject_id = connection.execute(text(
            'SELECT subject_id FROM recovery_task_events WHERE idempotency_key = :key'
        ), {'key': 'boolean-subject-event'}).scalar_one()
        counts = {
            table: connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar_one()
            for table in ('support_plan_versions', 'recovery_tasks', 'recovery_task_events')
        }

    assert tasks['valid-subject-task']['subject_id'] == subject_id
    assert not bool(tasks['valid-subject-task']['subject_scope_review_required'])
    for task_key in (
        'boolean-true-task',
        'boolean-false-task',
        'null-subject-task',
        'string-subject-task',
        'float-subject-task',
        'zero-subject-task',
        'negative-subject-task',
    ):
        assert tasks[task_key]['subject_id'] is None
        assert bool(tasks[task_key]['subject_scope_review_required'])
    assert event_subject_id is None
    assert counts == {'support_plan_versions': 1, 'recovery_tasks': 8, 'recovery_task_events': 1}
    engine.dispose()


def test_intervention_outcome_migration_matches_orm_and_preserves_legacy_rows(monkeypatch, tmp_path):
    database_path = tmp_path / 'intervention-outcome-migration.db'
    database_url = f'sqlite:///{database_path.as_posix()}'
    monkeypatch.setattr('backend.app.config.get_settings', lambda: SimpleNamespace(database_url=database_url))
    configuration = Config('alembic.ini')

    command.upgrade(configuration, 'j3k4l5m6n7')
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        now = '2026-10-20 12:00:00'
        connection.execute(text(
            'INSERT INTO students '
            '(student_id, full_name, risk_level, risk_probability, previous_gpa, current_gpa, '
            'attendance, assessment, assignments_completed, momentum, created_at, updated_at) '
            'VALUES (:student_id, :full_name, :risk_level, :risk_probability, :previous_gpa, '
            ':current_gpa, :attendance, :assessment, :assignments_completed, :momentum, :created_at, :updated_at)'
        ), {
            'student_id': 'intervention-migration-001',
            'full_name': 'Intervention Migration Student',
            'risk_level': 'HIGH',
            'risk_probability': 0.8,
            'previous_gpa': 2.0,
            'current_gpa': 1.8,
            'attendance': 70.0,
            'assessment': 45.0,
            'assignments_completed': 50.0,
            'momentum': -0.2,
            'created_at': now,
            'updated_at': now,
        })
        student_id = connection.execute(text(
            "SELECT id FROM students WHERE student_id = 'intervention-migration-001'"
        )).scalar_one()
        teacher_user_id = connection.execute(text(
            'INSERT INTO users (email, full_name, role, password_hash, created_at, updated_at) '
            'VALUES (:email, :full_name, :role, :password_hash, :created_at, :updated_at)'
        ), {
            'email': 'intervention-migration-teacher@example.com',
            'full_name': 'Migration Teacher',
            'role': 'TEACHER',
            'password_hash': 'unused-test-hash',
            'created_at': now,
            'updated_at': now,
        }).lastrowid
        teacher_id = connection.execute(text(
            'INSERT INTO teachers (user_id, department, created_at, updated_at) '
            'VALUES (:user_id, :department, :created_at, :updated_at)'
        ), {
            'user_id': teacher_user_id,
            'department': 'Science',
            'created_at': now,
            'updated_at': now,
        }).lastrowid
        intervention_id = connection.execute(text(
            'INSERT INTO interventions '
            '(student_id, teacher_id, title, body, status, sent_at, created_at, updated_at) '
            'VALUES (:student_id, :teacher_id, :title, :body, :status, :sent_at, :created_at, :updated_at)'
        ), {
            'student_id': student_id,
            'teacher_id': teacher_id,
            'title': 'Existing support check-in',
            'body': 'Existing support text',
            'status': 'sent',
            'sent_at': now,
            'created_at': now,
            'updated_at': now,
        }).lastrowid

    command.upgrade(configuration, 'head')

    from backend.app.database import Base

    inspector = inspect(engine)
    migrated_tables = set(inspector.get_table_names()) - {'alembic_version'}
    model_tables = set(Base.metadata.tables)
    assert migrated_tables == model_tables
    for table_name, table in Base.metadata.tables.items():
        assert {column['name'] for column in inspector.get_columns(table_name)} == {
            column.name for column in table.columns
        }, table_name

    with engine.connect() as connection:
        assert connection.execute(text(
            'SELECT version_num FROM alembic_version'
        )).scalar_one() == 'k2l3m4n5o6'
        assert connection.execute(text(
            'SELECT student_id, teacher_id, title, body, status FROM interventions WHERE id = :id'
        ), {'id': intervention_id}).one() == (
            student_id,
            teacher_id,
            'Existing support check-in',
            'Existing support text',
            'sent',
        )
        assert connection.exec_driver_sql('PRAGMA integrity_check').scalar_one() == 'ok'
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []

    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE interventions SET outcome = 'CONTACTED', outcome_notes = 'Follow-up recorded', "
            "observed_at = '2026-10-20 13:00:00' WHERE id = :id"
        ), {'id': intervention_id})
    with pytest.raises(RuntimeError, match='Cannot downgrade intervention outcome fields'):
        command.downgrade(configuration, 'j3k4l5m6n7')
    with engine.connect() as connection:
        assert connection.execute(text(
            'SELECT outcome, outcome_notes, observed_at FROM interventions WHERE id = :id'
        ), {'id': intervention_id}).one() == (
            'CONTACTED',
            'Follow-up recorded',
            '2026-10-20 13:00:00',
        )

    with engine.begin() as connection:
        connection.execute(text(
            'UPDATE interventions SET outcome = NULL, outcome_notes = NULL, observed_at = NULL WHERE id = :id'
        ), {'id': intervention_id})
    command.downgrade(configuration, 'j3k4l5m6n7')
    with engine.connect() as connection:
        assert connection.execute(text(
            'SELECT version_num FROM alembic_version'
        )).scalar_one() == 'j3k4l5m6n7'
        assert connection.execute(text(
            'SELECT title, body, status FROM interventions WHERE id = :id'
        ), {'id': intervention_id}).one() == (
            'Existing support check-in',
            'Existing support text',
            'sent',
        )
        intervention_columns = {column['name'] for column in inspect(connection).get_columns('interventions')}
        assert not {'outcome', 'outcome_notes', 'observed_at'} & intervention_columns
        assert connection.exec_driver_sql('PRAGMA integrity_check').scalar_one() == 'ok'
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
    engine.dispose()