from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import json
import logging
from pathlib import Path
import re
import time as time_module
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from ai.llm.provider import build_provider
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings
from backend.app.core.security import CurrentStudent, CurrentTeacher, CurrentUser
from backend.app.database import SessionLocal, get_db
from backend.app.models import (
    Assessment,
    Course,
    Mark,
    Notification,
    QuizAttempt,
    RecoveryGenerationJob,
    RecoveryGenerationRateEvent,
    RecoveryModuleLibrary,
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
from backend.app.services.learning_assessments import validate_topic_map_response
from backend.app.services.recovery import TASK_AVAILABLE, TASK_COMPLETED
from backend.app.services.recovery_quizzes import (
    aggregate_recovery_score,
    grade_quiz,
    is_backlog,
    schedule_topics,
    validate_recovery_quiz_response,
)

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

_FIRST_INTERNAL_ASSESSMENT = re.compile(
    r'\b(?:FA[\s_-]*1|FA[\s_-]*I|FIRST\s+(?:INTERNAL|ASSESSMENT)|INTERNAL\s+(?:ASSESSMENT\s*)?1|IA[\s_-]*1)\b',
    re.IGNORECASE,
)


class RecoveryPlanCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    selected_modules: list[int] = Field(default_factory=list, max_length=5)
    topics_per_day: Literal[1, 2, 3, 5] = 3

    @field_validator('selected_modules')
    @classmethod
    def validate_modules(cls, values: list[int]) -> list[int]:
        if any(value not in {1, 2, 3, 4, 5} for value in values) or len(set(values)) != len(values):
            raise ValueError('Select unique module numbers from 1 to 5.')
        return sorted(values)


class RecoveryPlanUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    selected_modules: list[int] | None = Field(default=None, max_length=5)
    topics_per_day: Literal[1, 2, 3, 5] | None = None

    @field_validator('selected_modules')
    @classmethod
    def validate_modules(cls, values: list[int] | None) -> list[int] | None:
        if values is not None and (
            any(value not in {1, 2, 3, 4, 5} for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError('Select unique module numbers from 1 to 5.')
        return sorted(values) if values is not None else None


class TopicDecisions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    topics: list[dict[str, Any]] = Field(min_length=1, max_length=300)

    @field_validator('topics')
    @classmethod
    def validate_topic_decisions(cls, topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = []
        for item in topics:
            if set(item) != {'topic_id', 'included'}:
                raise ValueError('Each topic decision must contain topic_id and included.')
            topic_id = item['topic_id']
            if isinstance(topic_id, bool) or not isinstance(topic_id, int) or topic_id < 1:
                raise ValueError('Topic IDs must be positive integers.')
            if not isinstance(item['included'], bool):
                raise ValueError('Topic inclusion must be a boolean.')
            ids.append(topic_id)
        if len(ids) != len(set(ids)):
            raise ValueError('Topic decisions must not repeat a topic.')
        return topics


class QuizSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid')

    answers: dict[str, str] = Field(min_length=5, max_length=5)


def _teacher(db: Session, user: User) -> Teacher:
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise HTTPException(status_code=403, detail='Teacher profile is not linked.')
    return teacher


def _teacher_subject(db: Session, teacher: Teacher, subject_id: int) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if subject is None:
        raise HTTPException(status_code=404, detail='Subject not found.')
    if db.query(Course.id).filter(
        Course.teacher_id == teacher.id,
        Course.subject_id == subject_id,
    ).first() is None:
        raise HTTPException(status_code=404, detail='Subject is not available in your classes.')
    return subject


def _teacher_student(db: Session, teacher: Teacher, student_id: int) -> Student:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found.')
    courses = db.query(Course.id, Course.section).filter(Course.teacher_id == teacher.id).all()
    course_ids = [course_id for course_id, _ in courses]
    sections = {section for _, section in courses if section}
    assigned = student.course_id in course_ids or (
        student.course_id is None and student.section in sections
    )
    if not assigned:
        raise HTTPException(status_code=404, detail='Student is not in one of your classes.')
    return student


def _teacher_student_subject(
    db: Session,
    teacher: Teacher,
    student: Student,
    subject_id: int,
) -> None:
    course_query = db.query(Course.id).filter(
        Course.teacher_id == teacher.id,
        Course.subject_id == subject_id,
    )
    if student.course_id is not None:
        course_query = course_query.filter(Course.id == student.course_id)
    elif student.section:
        course_query = course_query.filter(Course.section == student.section)
    else:
        course_query = course_query.filter(False)
    if course_query.first() is None:
        raise HTTPException(status_code=404, detail='Student is not assigned to this subject in your classes.')


def _teacher_scope(db: Session, teacher: Teacher) -> tuple[list[Course], list[Student]]:
    courses = db.query(Course).filter(Course.teacher_id == teacher.id).order_by(Course.id).all()
    course_ids = [course.id for course in courses]
    sections = {course.section for course in courses if course.section}
    assigned_students = [Student.course_id.in_(course_ids)] if course_ids else []
    if sections:
        assigned_students.append(and_(Student.course_id.is_(None), Student.section.in_(sections)))
    students = db.query(Student).filter(or_(*assigned_students)).order_by(Student.full_name, Student.id).all() if assigned_students else []
    return courses, students


def _fa1_values(
    db: Session,
    student_ids: list[int],
    subject_ids: list[int],
) -> dict[tuple[int, int], dict[str, Any]]:
    if not student_ids or not subject_ids:
        return {}
    values: dict[tuple[int, int], dict[str, Any]] = {}
    records = db.query(StudentSubjectAcademicRecord).filter(
        StudentSubjectAcademicRecord.student_id.in_(student_ids),
        StudentSubjectAcademicRecord.subject_id.in_(subject_ids),
        StudentSubjectAcademicRecord.fa1.is_not(None),
    ).order_by(
        StudentSubjectAcademicRecord.academic_period.desc(),
        StudentSubjectAcademicRecord.updated_at.desc(),
        StudentSubjectAcademicRecord.id.desc(),
    ).all()
    for record in records:
        key = (record.student_id, record.subject_id)
        values.setdefault(key, {
            'score': float(record.fa1),
            'maximum': record.fa1_maximum_marks,
            'period': record.academic_period,
            'recorded_at': record.fa1_observed_at.isoformat() if record.fa1_observed_at else None,
            'source': 'subject_academic_record',
            'maximum_verified': record.fa1_maximum_verified,
        })

    covered = set(values)
    marks = db.query(Mark, Course.subject_id).join(
        Assessment,
        Assessment.id == Mark.assessment_id,
    ).join(
        Course,
        Course.id == Assessment.course_id,
    ).filter(
        Mark.student_id.in_(student_ids),
        Course.subject_id.in_(subject_ids),
    ).order_by(Mark.recorded_at.desc(), Mark.id.desc()).all()
    for mark, subject_id in marks:
        key = (mark.student_id, subject_id)
        if key in covered or key in values or not mark.assessment or not _FIRST_INTERNAL_ASSESSMENT.search(mark.assessment.title):
            continue
        values[key] = {
            'score': float(mark.score),
            'maximum': mark.assessment.maximum_marks,
            'period': None,
            'recorded_at': mark.recorded_at.isoformat() if mark.recorded_at else None,
            'source': 'assessment_mark',
            'maximum_verified': mark.assessment.maximum_marks_verified,
        }
    return values


def _is_below_pass(value: dict[str, Any] | None) -> bool:
    if value is None:
        return False
    try:
        return is_backlog(
            value['score'],
            value['maximum'],
            pass_mark=settings.recovery_pass_mark,
            maximum_marks=settings.recovery_maximum_marks,
        )
    except ValueError:
        return False


def _backlog_aggregate(
    db: Session,
    applicable_students: dict[int, set[int]],
) -> tuple[int, int]:
    applicable_filters = [
        and_(
            StudentSubjectAcademicRecord.subject_id == subject_id,
            StudentSubjectAcademicRecord.student_id.in_(student_ids),
        )
        for subject_id, student_ids in applicable_students.items()
        if student_ids
    ]
    if not applicable_filters:
        return 0, 0
    record_scope = or_(*applicable_filters)
    ranked_records = db.query(
        StudentSubjectAcademicRecord.student_id.label('student_id'),
        StudentSubjectAcademicRecord.subject_id.label('subject_id'),
        StudentSubjectAcademicRecord.fa1.label('score'),
        func.coalesce(
            StudentSubjectAcademicRecord.fa1_maximum_marks,
            settings.recovery_maximum_marks,
        ).label('maximum'),
        func.row_number().over(
            partition_by=(
                StudentSubjectAcademicRecord.student_id,
                StudentSubjectAcademicRecord.subject_id,
            ),
            order_by=(
                StudentSubjectAcademicRecord.academic_period.desc(),
                StudentSubjectAcademicRecord.updated_at.desc(),
                StudentSubjectAcademicRecord.id.desc(),
            ),
        ).label('row_no'),
    ).filter(
        record_scope,
        StudentSubjectAcademicRecord.fa1.is_not(None),
    ).subquery()
    record_backlogs = db.query(
        ranked_records.c.student_id,
        ranked_records.c.subject_id,
    ).filter(
        ranked_records.c.row_no == 1,
        ranked_records.c.maximum > 0,
        ranked_records.c.score >= 0,
        ranked_records.c.score <= ranked_records.c.maximum,
        ranked_records.c.score * settings.recovery_maximum_marks / ranked_records.c.maximum
        < settings.recovery_pass_mark,
    ).subquery()

    record_exists = db.query(StudentSubjectAcademicRecord.id).filter(
        StudentSubjectAcademicRecord.student_id == Mark.student_id,
        StudentSubjectAcademicRecord.subject_id == Course.subject_id,
        StudentSubjectAcademicRecord.fa1.is_not(None),
    ).exists()
    mark_scope = or_(*[
        and_(
            Course.subject_id == subject_id,
            Mark.student_id.in_(student_ids),
        )
        for subject_id, student_ids in applicable_students.items()
        if student_ids
    ])
    ranked_marks = db.query(
        Mark.student_id.label('student_id'),
        Course.subject_id.label('subject_id'),
        Mark.score.label('score'),
        Assessment.maximum_marks.label('maximum'),
        func.row_number().over(
            partition_by=(Mark.student_id, Course.subject_id),
            order_by=(Mark.recorded_at.desc(), Mark.id.desc()),
        ).label('row_no'),
    ).join(Assessment, Assessment.id == Mark.assessment_id).join(
        Course,
        Course.id == Assessment.course_id,
    ).filter(
        mark_scope,
        or_(
            Assessment.title.ilike('%FA-1%'),
            Assessment.title.ilike('%FA 1%'),
            Assessment.title.ilike('%FA_1%'),
            Assessment.title.ilike('%FA1%'),
            Assessment.title.ilike('%FA-I%'),
            Assessment.title.ilike('FAI'),
            Assessment.title.ilike('FAI %'),
            Assessment.title.ilike('% FAI'),
            Assessment.title.ilike('% FAI %'),
            Assessment.title.ilike('%FA I%'),
            Assessment.title.ilike('%FA_I%'),
            Assessment.title.ilike('%First Internal%'),
            Assessment.title.ilike('%First Assessment%'),
            Assessment.title.ilike('%Internal Assessment 1%'),
            Assessment.title.ilike('%Internal 1%'),
            Assessment.title.ilike('%IA-1%'),
            Assessment.title.ilike('%IA 1%'),
            Assessment.title.ilike('%IA_1%'),
            Assessment.title.ilike('%IA1%'),
        ),
        ~record_exists,
    ).subquery()
    mark_backlogs = db.query(
        ranked_marks.c.student_id,
        ranked_marks.c.subject_id,
    ).filter(
        ranked_marks.c.row_no == 1,
        ranked_marks.c.maximum > 0,
        ranked_marks.c.score >= 0,
        ranked_marks.c.score <= ranked_marks.c.maximum,
        ranked_marks.c.score * settings.recovery_maximum_marks / ranked_marks.c.maximum
        < settings.recovery_pass_mark,
    ).subquery()
    all_backlogs = db.query(
        record_backlogs.c.student_id.label('student_id'),
        record_backlogs.c.subject_id.label('subject_id'),
    ).union_all(
        db.query(
            mark_backlogs.c.student_id.label('student_id'),
            mark_backlogs.c.subject_id.label('subject_id'),
        ),
    ).subquery()
    students_with_backlogs, backlog_subjects = db.query(
        func.count(func.distinct(all_backlogs.c.student_id)),
        func.count(),
    ).select_from(all_backlogs).one()
    return int(students_with_backlogs or 0), int(backlog_subjects or 0)


def _module_list(db: Session, subject_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    by_subject: dict[int, list[dict[str, Any]]] = {subject_id: [] for subject_id in subject_ids}
    if not subject_ids:
        return by_subject
    modules = db.query(RecoveryModuleLibrary).filter(
        RecoveryModuleLibrary.subject_id.in_(subject_ids),
    ).order_by(RecoveryModuleLibrary.subject_id, RecoveryModuleLibrary.module_no).all()
    for module in modules:
        by_subject[module.subject_id].append({
            'module_no': module.module_no,
            'status': module.status,
            'filename': module.original_filename,
            'uploaded': module.status == 'READY' and bool(module.extracted_text),
        })
    return by_subject


def _plan_data(db: Session, plan: RecoveryPlan) -> dict[str, Any]:
    topics = db.query(RecoveryTopic).filter(
        RecoveryTopic.plan_id == plan.id,
    ).order_by(RecoveryTopic.module_no, RecoveryTopic.id).all()
    jobs = db.query(RecoveryGenerationJob).filter(
        RecoveryGenerationJob.plan_id == plan.id,
    ).order_by(RecoveryGenerationJob.created_at.desc(), RecoveryGenerationJob.id.desc()).limit(5).all()
    quizzes = db.query(RecoveryQuiz).filter(
        RecoveryQuiz.plan_id == plan.id,
    ).order_by(RecoveryQuiz.topic_id.is_(None), RecoveryQuiz.topic_id, RecoveryQuiz.id).all()
    return {
        'id': plan.id,
        'student_id': plan.student_id,
        'subject_id': plan.subject_id,
        'source_exam': plan.source_exam,
        'marks': plan.marks,
        'selected_modules': plan.selected_modules or [],
        'topics_per_day': plan.topics_per_day,
        'status': plan.status,
        'start_date': plan.start_date.isoformat() if plan.start_date else None,
        'topics': [{
            'id': topic.id,
            'module_no': topic.module_no,
            'name': topic.name,
            'included': topic.included,
        } for topic in topics],
        'quizzes': [{
            'id': quiz.id,
            'topic_id': quiz.topic_id,
            'quiz_key': quiz.quiz_key,
            'max_score': quiz.max_score,
            'questions': quiz.questions,
        } for quiz in quizzes],
        'jobs': [_job_data(job) for job in jobs],
    }


def _attempt_aggregate(
    db: Session,
    applicable_students: dict[int, set[int]],
) -> tuple[int, float | None]:
    if not any(applicable_students.values()):
        return 0, None
    plan_scope = or_(*[
        and_(
            RecoveryPlan.subject_id == subject_id,
            RecoveryPlan.student_id.in_(student_ids),
        )
        for subject_id, student_ids in applicable_students.items()
        if student_ids
    ])
    assigned_quizzes = db.query(func.count(RecoveryQuiz.id)).join(
        RecoveryPlan,
        RecoveryPlan.id == RecoveryQuiz.plan_id,
    ).filter(
        RecoveryPlan.status == 'assigned',
        plan_scope,
    ).scalar() or 0
    task_scope = or_(*[
        and_(
            RecoveryTask.subject_id == subject_id,
            RecoveryTask.student_id.in_(student_ids),
        )
        for subject_id, student_ids in applicable_students.items()
        if student_ids
    ])
    totals = db.query(
        func.sum(QuizAttempt.score),
        func.sum(QuizAttempt.max_score),
    ).join(RecoveryTask, RecoveryTask.id == QuizAttempt.task_id).filter(
        task_scope,
        RecoveryTask.recovery_plan_id.is_not(None),
    ).one()
    average = (
        round(float(totals[0]) / float(totals[1]) * 100, 1)
        if totals[0] is not None and totals[1]
        else None
    )
    return int(assigned_quizzes), average


@router.get('/teacher/recovery/overview')
def teacher_recovery_overview(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    teacher = _teacher(db, user)
    courses, students = _teacher_scope(db, teacher)
    student_ids = [student.id for student in students]
    subject_ids = sorted({course.subject_id for course in courses if course.subject_id is not None})
    subjects = db.query(Subject).filter(Subject.id.in_(subject_ids)).order_by(Subject.name).all() if subject_ids else []
    subject_by_id = {subject.id: subject for subject in subjects}
    values = _fa1_values(db, student_ids, subject_ids)
    student_subjects: dict[int, list[dict[str, Any]]] = {student.id: [] for student in students}
    applicable_students: dict[int, set[int]] = {subject_id: set() for subject_id in subject_ids}
    for course in courses:
        if course.subject_id is None:
            continue
        for student in students:
            if student.course_id == course.id or (
                student.course_id is None and course.section and student.section == course.section
            ):
                applicable_students[course.subject_id].add(student.id)
    plan_rows = db.query(RecoveryPlan).filter(
        RecoveryPlan.student_id.in_(student_ids),
        RecoveryPlan.subject_id.in_(subject_ids),
        RecoveryPlan.source_exam == 'FA-1',
    ).all() if student_ids and subject_ids else []
    plans = {(plan.student_id, plan.subject_id): plan for plan in plan_rows}
    for subject_id in subject_ids:
        subject = subject_by_id[subject_id]
        for student_id in applicable_students[subject_id]:
            value = values.get((student_id, subject_id))
            plan = plans.get((student_id, subject_id))
            student_subjects[student_id].append({
                'id': subject_id,
                'name': subject.name,
                'code': subject.code,
                'fa1': value['score'] if value else None,
                'maximum': value['maximum'] or settings.recovery_maximum_marks if value else settings.recovery_maximum_marks,
                'academic_period': value['period'] if value else None,
                'backlog': _is_below_pass(value),
                'evidence_source': value['source'] if value else None,
                'plan': _plan_data(db, plan) if plan else None,
            })
    for student in students:
        student_subjects[student.id].sort(key=lambda item: item['name'].casefold())
    students_with_backlogs, backlog_subjects = _backlog_aggregate(db, applicable_students)
    quizzes_assigned, average_score = _attempt_aggregate(db, applicable_students)
    modules = _module_list(db, subject_ids)
    return {
        'stats': {
            'students_with_backlogs': students_with_backlogs,
            'backlog_subjects': backlog_subjects,
            'quizzes_assigned': quizzes_assigned,
            'average_recovery_score': average_score,
        },
        'pass_mark': settings.recovery_pass_mark,
        'maximum_marks': settings.recovery_maximum_marks,
        'subjects': [{
            'id': subject.id,
            'name': subject.name,
            'code': subject.code,
            'modules': modules[subject.id],
        } for subject in subjects],
        'students': [{
            'id': student.id,
            'student_id': student.student_id,
            'name': student.full_name,
            'section': student.section,
            'course_name': student.course.name if student.course else None,
            'subjects': student_subjects[student.id],
        } for student in students],
    }


def _validate_selected_modules(
    db: Session,
    subject_id: int,
    module_numbers: list[int],
) -> list[RecoveryModuleLibrary]:
    if not module_numbers:
        return []
    modules = db.query(RecoveryModuleLibrary).filter(
        RecoveryModuleLibrary.subject_id == subject_id,
        RecoveryModuleLibrary.module_no.in_(module_numbers),
        RecoveryModuleLibrary.status == 'READY',
    ).order_by(RecoveryModuleLibrary.module_no).all()
    if {module.module_no for module in modules} != set(module_numbers) or any(
        not module.extracted_text for module in modules
    ):
        raise HTTPException(status_code=422, detail='Only uploaded, readable modules can be selected.')
    return modules


def _student_backlog(db: Session, student_id: int, subject_id: int) -> float:
    value = _fa1_values(db, [student_id], [subject_id]).get((student_id, subject_id))
    if value is None:
        raise HTTPException(status_code=409, detail='No real FA-1 mark is available for this student and subject.')
    if not _is_below_pass(value):
        raise HTTPException(status_code=409, detail='This FA-1 result is not below the configured pass mark.')
    return float(value['score'])


@router.post('/teacher/recovery/students/{student_id}/subjects/{subject_id}/plans')
def create_recovery_plan(
    student_id: int,
    subject_id: int,
    payload: RecoveryPlanCreate,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    teacher = _teacher(db, user)
    student = _teacher_student(db, teacher, student_id)
    _teacher_subject(db, teacher, subject_id)
    _teacher_student_subject(db, teacher, student, subject_id)
    marks = _student_backlog(db, student_id, subject_id)
    _validate_selected_modules(db, subject_id, payload.selected_modules)
    plan = db.query(RecoveryPlan).filter_by(
        student_id=student_id,
        subject_id=subject_id,
        source_exam='FA-1',
    ).first()
    if plan is None:
        plan = RecoveryPlan(
            student_id=student_id,
            subject_id=subject_id,
            source_exam='FA-1',
            marks=marks,
            selected_modules=payload.selected_modules,
            topics_per_day=payload.topics_per_day,
            status='draft',
            created_by=user.id,
        )
        db.add(plan)
    elif plan.status != 'assigned':
        plan.marks = marks
        plan.selected_modules = payload.selected_modules
        plan.topics_per_day = payload.topics_per_day
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail='A recovery plan already exists for this FA-1 result.') from exc
    db.refresh(plan)
    return _plan_data(db, plan)


def _plan_access(db: Session, user: User, plan_id: int) -> RecoveryPlan:
    plan = db.query(RecoveryPlan).filter(RecoveryPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail='Recovery plan not found.')
    teacher = _teacher(db, user)
    student = _teacher_student(db, teacher, plan.student_id)
    _teacher_subject(db, teacher, plan.subject_id)
    _teacher_student_subject(db, teacher, student, plan.subject_id)
    return plan


@router.patch('/teacher/recovery/plans/{plan_id}')
def update_recovery_plan(
    plan_id: int,
    payload: RecoveryPlanUpdate,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = _plan_access(db, user, plan_id)
    if plan.status == 'assigned':
        raise HTTPException(status_code=409, detail='Module selection is locked after assignment.')
    if payload.selected_modules is None and payload.topics_per_day is None:
        raise HTTPException(status_code=422, detail='Provide a module selection or topics-per-day value.')
    if payload.selected_modules is not None and payload.selected_modules != (plan.selected_modules or []):
        active_job = db.query(RecoveryGenerationJob.id).filter(
            RecoveryGenerationJob.plan_id == plan.id,
            RecoveryGenerationJob.status.in_(['queued', 'running']),
        ).first()
        if active_job:
            raise HTTPException(status_code=409, detail='Wait for the current AI job before changing modules.')
        _validate_selected_modules(db, plan.subject_id, payload.selected_modules)
        plan.selected_modules = payload.selected_modules
        db.query(RecoveryTopic).filter(RecoveryTopic.plan_id == plan.id).delete(synchronize_session=False)
        db.query(RecoveryQuiz).filter(RecoveryQuiz.plan_id == plan.id).delete(synchronize_session=False)
        plan.status = 'draft'
    if payload.topics_per_day is not None:
        plan.topics_per_day = payload.topics_per_day
    db.commit()
    db.refresh(plan)
    return _plan_data(db, plan)


@router.patch('/teacher/recovery/plans/{plan_id}/topics')
def update_recovery_topics(
    plan_id: int,
    payload: TopicDecisions,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = _plan_access(db, user, plan_id)
    if plan.status not in {'topics_ready', 'quizzes_ready'}:
        raise HTTPException(status_code=409, detail='Analyze module text before reviewing topics.')
    rows = {
        topic.id: topic
        for topic in db.query(RecoveryTopic).filter(RecoveryTopic.plan_id == plan.id).all()
    }
    if {item['topic_id'] for item in payload.topics} != set(rows):
        raise HTTPException(status_code=422, detail='Review every current topic exactly once.')
    changed = any(rows[item['topic_id']].included != item['included'] for item in payload.topics)
    if plan.status == 'quizzes_ready' and changed:
        db.query(RecoveryQuiz).filter(RecoveryQuiz.plan_id == plan.id).delete(synchronize_session=False)
        plan.status = 'topics_ready'
    for item in payload.topics:
        rows[item['topic_id']].included = item['included']
    if not any(topic.included for topic in rows.values()):
        raise HTTPException(status_code=422, detail='Include at least one topic before quiz generation.')
    db.commit()
    db.refresh(plan)
    return _plan_data(db, plan)


def _current_modules(db: Session, plan: RecoveryPlan) -> list[RecoveryModuleLibrary]:
    modules = _validate_selected_modules(db, plan.subject_id, plan.selected_modules or [])
    if not modules:
        raise HTTPException(status_code=409, detail='Select at least one uploaded module.')
    return modules


def _enqueue_job(
    db: Session,
    user: User,
    plan: RecoveryPlan,
    kind: Literal['topics', 'quizzes'],
    modules: list[RecoveryModuleLibrary],
    background_tasks: BackgroundTasks,
    session_factory: sessionmaker,
) -> dict[str, Any]:
    selection = {
        'plan': plan.id,
        'kind': kind,
        'modules': [(module.module_no, hashlib.sha256((module.extracted_text or '').encode()).hexdigest()) for module in modules],
    }
    if kind == 'quizzes':
        topics = db.query(RecoveryTopic).filter(
            RecoveryTopic.plan_id == plan.id,
        ).order_by(RecoveryTopic.id).all()
        selection['topics'] = [
            {
                'id': topic.id,
                'module_no': topic.module_no,
                'name': topic.name,
                'included': topic.included,
                'updated_at': topic.updated_at.isoformat(),
            }
            for topic in topics
        ]
    idempotency_key = hashlib.sha256(
        json.dumps(selection, sort_keys=True).encode(),
    ).hexdigest()
    existing = db.query(RecoveryGenerationJob).filter_by(idempotency_key=idempotency_key).first()
    if existing is not None and existing.status != 'failed':
        return {'job': _job_data(existing), 'cached': existing.status == 'completed'}
    since = datetime.utcnow() - timedelta(hours=1)
    request_count = db.query(func.count(RecoveryGenerationRateEvent.id)).filter(
        RecoveryGenerationRateEvent.user_id == user.id,
        RecoveryGenerationRateEvent.created_at >= since,
    ).scalar() or 0
    if request_count >= settings.recovery_ai_rate_limit_per_hour:
        raise HTTPException(status_code=429, detail='AI generation rate limit reached. Try again later.')
    if existing is None:
        job = RecoveryGenerationJob(
            plan_id=plan.id,
            kind=kind,
            status='queued',
            attempts=0,
            idempotency_key=idempotency_key,
            created_by=user.id,
        )
        db.add(job)
        db.flush()
    else:
        job = existing
        job.status = 'queued'
        job.attempts = 0
        job.error_message = None
    db.add(RecoveryGenerationRateEvent(user_id=user.id, job_id=job.id))
    db.commit()
    db.refresh(job)
    background_tasks.add_task(_run_generation_job, job.id, session_factory)
    return {'job': _job_data(job), 'cached': False}


def _job_data(job: RecoveryGenerationJob) -> dict[str, Any]:
    return {
        'id': job.id,
        'plan_id': job.plan_id,
        'kind': job.kind,
        'status': job.status,
        'attempts': job.attempts,
        'error_message': job.error_message,
        'retryable': job.status == 'failed' or (
            job.status in {'queued', 'running'}
            and job.updated_at < datetime.utcnow() - timedelta(
                seconds=settings.llm_timeout_seconds * 3 + 30,
            )
        ),
    }


def _session_factory_for(request: Request) -> sessionmaker:
    return getattr(request.app.state, 'test_session_factory', SessionLocal)


@router.post('/teacher/recovery/plans/{plan_id}/analyze')
def analyze_recovery_modules(
    plan_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = _plan_access(db, user, plan_id)
    if plan.status == 'assigned':
        raise HTTPException(status_code=409, detail='An assigned plan cannot be analyzed again.')
    if db.query(RecoveryTopic.id).filter(RecoveryTopic.plan_id == plan.id).first():
        return {'job': None, 'cached': True, 'plan': _plan_data(db, plan)}
    modules = _current_modules(db, plan)
    queued = _enqueue_job(db, user, plan, 'topics', modules, background_tasks, _session_factory_for(request))
    return {**queued, 'plan': _plan_data(db, plan)}


@router.post('/teacher/recovery/plans/{plan_id}/generate-quizzes')
def generate_recovery_quizzes(
    plan_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = _plan_access(db, user, plan_id)
    if plan.status == 'assigned':
        raise HTTPException(status_code=409, detail='An assigned plan cannot be regenerated.')
    if plan.status not in {'topics_ready', 'quizzes_ready'}:
        raise HTTPException(status_code=409, detail='Review extracted topics before generating quizzes.')
    if db.query(RecoveryQuiz.id).filter(RecoveryQuiz.plan_id == plan.id).first():
        return {'job': None, 'cached': True, 'plan': _plan_data(db, plan)}
    if not db.query(RecoveryTopic.id).filter(
        RecoveryTopic.plan_id == plan.id,
        RecoveryTopic.included.is_(True),
    ).first():
        raise HTTPException(status_code=409, detail='Include at least one topic before quiz generation.')
    modules = _current_modules(db, plan)
    queued = _enqueue_job(db, user, plan, 'quizzes', modules, background_tasks, _session_factory_for(request))
    return {**queued, 'plan': _plan_data(db, plan)}


@router.get('/teacher/recovery/jobs/{job_id}')
def get_recovery_job(
    job_id: int,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = db.query(RecoveryGenerationJob).filter(
        RecoveryGenerationJob.id == job_id,
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail='Recovery job not found.')
    _plan_access(db, user, job.plan_id)
    return _job_data(job)


@router.post('/teacher/recovery/jobs/{job_id}/retry')
def retry_recovery_job(
    job_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = db.query(RecoveryGenerationJob).filter(
        RecoveryGenerationJob.id == job_id,
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail='Recovery job not found.')
    plan = _plan_access(db, user, job.plan_id)
    if not _job_data(job)['retryable'] or plan.status == 'assigned':
        raise HTTPException(status_code=409, detail='Only failed or stalled, unassigned jobs can be retried.')
    since = datetime.utcnow() - timedelta(hours=1)
    request_count = db.query(func.count(RecoveryGenerationRateEvent.id)).filter(
        RecoveryGenerationRateEvent.user_id == user.id,
        RecoveryGenerationRateEvent.created_at >= since,
    ).scalar() or 0
    if request_count >= settings.recovery_ai_rate_limit_per_hour:
        raise HTTPException(status_code=429, detail='AI generation rate limit reached. Try again later.')
    job.status = 'queued'
    job.error_message = None
    job.attempts = 0
    job.created_by = user.id
    db.add(RecoveryGenerationRateEvent(user_id=user.id, job_id=job.id))
    db.commit()
    background_tasks.add_task(_run_generation_job, job.id, _session_factory_for(request))
    return _job_data(job)


def _flatten_topics(topics: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for topic in topics:
        result.append(topic['title'])
        result.extend(_flatten_topics(topic.get('children', [])))
    return result


def _run_generation_job(job_id: int, session_factory: sessionmaker) -> None:
    with session_factory() as db:
        job = db.query(RecoveryGenerationJob).filter(
            RecoveryGenerationJob.id == job_id,
        ).first()
        if job is None:
            logger.error('Recovery generation job %s disappeared before execution.', job_id)
            return
        for attempt in range(1, 4):
            try:
                job.status = 'running'
                job.attempts = attempt
                db.commit()
                plan = db.query(RecoveryPlan).filter(RecoveryPlan.id == job.plan_id).first()
                if plan is None or plan.status == 'assigned':
                    raise ValueError('The recovery plan is no longer available for generation.')
                modules = _current_modules(db, plan)
                provider = build_provider(settings)
                if job.kind == 'topics':
                    if db.query(RecoveryTopic.id).filter(RecoveryTopic.plan_id == plan.id).first():
                        job.status = 'completed'
                        job.error_message = None
                        db.commit()
                        return
                    subject = db.query(Subject).filter(Subject.id == plan.subject_id).one()
                    extracted: list[tuple[int, str]] = []
                    for module in modules:
                        source_text = module.extracted_text or ''
                        if len(source_text) > 60_000:
                            raise ValueError(
                                f'Module {module.module_no} exceeds the 60,000-character AI context limit. '
                                'Upload a smaller, text-searchable PDF.'
                            )
                        context = {
                            'subject': {'id': subject.id, 'name': subject.name},
                            'module': {
                                'id': module.id,
                                'number': module.module_no,
                                'title': f'Module {module.module_no}',
                            },
                            'source': {
                                'resource_id': module.id,
                                'filename': module.original_filename,
                                'file_type': 'PDF',
                                'available_locator_types': {'PDF': ['page']},
                                'sha256': hashlib.sha256(source_text.encode()).hexdigest(),
                            },
                            'source_text': source_text,
                            'truncated': False,
                        }
                        raw = provider.generate_topic_map(context)
                        topics, issues, limitation = validate_topic_map_response(raw, source_text, context)
                        if issues or not topics:
                            raise ValueError(
                                'Topic extraction needs a valid, fully source-grounded result. '
                                + ' '.join(issues or [limitation or 'No topics were supported by the source.'])
                            )
                        extracted.extend(
                            (module.module_no, topic_name)
                            for topic_name in _flatten_topics(topics)
                        )
                    if not extracted:
                        raise ValueError('No topics were found in the selected modules.')
                    for module_no, topic_name in extracted:
                        db.add(RecoveryTopic(
                            plan_id=plan.id,
                            module_no=module_no,
                            name=topic_name,
                            included=True,
                        ))
                    plan.status = 'topics_ready'
                elif job.kind == 'quizzes':
                    if db.query(RecoveryQuiz.id).filter(RecoveryQuiz.plan_id == plan.id).first():
                        job.status = 'completed'
                        job.error_message = None
                        db.commit()
                        return
                    topics = db.query(RecoveryTopic).filter(
                        RecoveryTopic.plan_id == plan.id,
                        RecoveryTopic.included.is_(True),
                    ).order_by(RecoveryTopic.module_no, RecoveryTopic.id).all()
                    if not topics:
                        raise ValueError('No included topics are available for quiz generation.')
                    selected_module_numbers = {module.module_no for module in modules}
                    topic_modules = {topic.id: topic.module_no for topic in topics}
                    if any(module_no not in selected_module_numbers for module_no in topic_modules.values()):
                        raise ValueError('A reviewed topic no longer belongs to a selected module.')
                    source_text_by_module = {
                        module.module_no: module.extracted_text or ''
                        for module in modules
                    }
                    if any(len(text) > 60_000 for text in source_text_by_module.values()):
                        raise ValueError('A selected module exceeds the 60,000-character AI context limit.')
                    raw = provider.generate_recovery_quiz({
                        'subject': db.query(Subject.name).filter(Subject.id == plan.subject_id).scalar(),
                        'modules': [{
                            'module_no': module.module_no,
                            'text': source_text_by_module[module.module_no],
                        } for module in modules],
                        'topics': [{
                            'topic_id': topic.id,
                            'module_no': topic.module_no,
                            'name': topic.name,
                        } for topic in topics],
                    })
                    generated = validate_recovery_quiz_response(
                        raw,
                        topic_ids=set(topic_modules),
                        source_text_by_module=source_text_by_module,
                        topic_modules=topic_modules,
                    )
                    for item in generated['topic_quizzes']:
                        db.add(RecoveryQuiz(
                            plan_id=plan.id,
                            topic_id=item['topic_id'],
                            quiz_key=f"topic:{item['topic_id']}",
                            questions=item['questions'],
                            max_score=10,
                        ))
                    db.add(RecoveryQuiz(
                        plan_id=plan.id,
                        topic_id=None,
                        quiz_key='one-line',
                        questions=generated['one_line_questions'],
                        max_score=10,
                    ))
                    plan.status = 'quizzes_ready'
                else:
                    raise ValueError('Unsupported recovery generation job.')
                job.status = 'completed'
                job.error_message = None
                db.commit()
                return
            except Exception as exc:
                db.rollback()
                job = db.query(RecoveryGenerationJob).filter(
                    RecoveryGenerationJob.id == job_id,
                ).first()
                if job is None:
                    logger.exception('Recovery generation job %s failed after its database row was removed.', job_id)
                    return
                job.attempts = attempt
                job.error_message = str(exc)[:2000]
                if attempt < 3:
                    job.status = 'queued'
                    db.commit()
                    time_module.sleep(min(2 ** (attempt - 1), 4))
                    continue
                job.status = 'failed'
                db.commit()
                logger.exception('Recovery generation job %s failed after %s attempts.', job_id, attempt)
                return


def _extract_pdf_text(content: bytes) -> str:
    if not content.startswith(b'%PDF-'):
        raise HTTPException(status_code=415, detail='The uploaded file is not a valid PDF.')
    from pypdf import PdfReader
    from io import BytesIO

    try:
        pages = [page.extract_text() or '' for page in PdfReader(BytesIO(content)).pages]
    except Exception as exc:
        raise HTTPException(status_code=422, detail='The PDF could not be read.') from exc
    text = '\n\n'.join(page.strip() for page in pages if page.strip())
    if not text:
        raise HTTPException(status_code=422, detail='No readable text could be extracted from this PDF.')
    return text


async def _read_pdf(file: UploadFile) -> tuple[str, bytes]:
    filename = Path(file.filename or '').name
    if Path(filename).suffix.lower() != '.pdf' or file.content_type not in {
        'application/pdf',
        'application/octet-stream',
    }:
        raise HTTPException(status_code=415, detail='Only PDF files are accepted.')
    content = await file.read(settings.max_upload_size + 1)
    await file.close()
    if len(content) > settings.max_upload_size:
        raise HTTPException(status_code=413, detail=f'File exceeds the {settings.max_upload_size}-byte upload limit.')
    if not content:
        raise HTTPException(status_code=422, detail='The uploaded PDF is empty.')
    return filename, content


def _validate_module_replacement(db: Session, subject_id: int, module_no: int) -> None:
    plan_rows = db.query(RecoveryPlan).filter(
        RecoveryPlan.subject_id == subject_id,
        RecoveryPlan.status != 'draft',
    ).all()
    if any(module_no in (plan.selected_modules or []) for plan in plan_rows):
        raise HTTPException(
            status_code=409,
            detail='This module is used by a reviewed or assigned plan. Create a new draft before replacing it.',
        )


def _cleanup_module_files(paths: list[str]) -> None:
    for path in paths:
        try:
            file_path = Path(path)
            if file_path.is_file():
                file_path.unlink()
        except OSError:
            logger.exception('Could not remove a recovery module file at %s.', path)


def _store_module(
    db: Session,
    *,
    subject_id: int,
    module_no: int,
    teacher_id: int,
    filename: str,
    content: bytes,
    extracted_text: str,
) -> RecoveryModuleLibrary:
    _validate_module_replacement(db, subject_id, module_no)
    storage_root = (settings.upload_directory / 'recovery-modules' / str(subject_id)).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    filename_path = storage_root / f'module-{module_no}-{uuid4().hex}.pdf'
    filename_path.write_bytes(content)
    current = db.query(RecoveryModuleLibrary).filter_by(
        subject_id=subject_id,
        module_no=module_no,
    ).first()
    if current is None:
        current = RecoveryModuleLibrary(
            subject_id=subject_id,
            module_no=module_no,
            original_filename=filename[:255],
            file_path=str(filename_path),
            extracted_text=extracted_text,
            status='READY',
            uploaded_by=teacher_id,
        )
        db.add(current)
    else:
        current.original_filename = filename[:255]
        current.file_path = str(filename_path)
        current.extracted_text = extracted_text
        current.status = 'READY'
        current.uploaded_by = teacher_id
    return current


@router.post('/teacher/recovery/subjects/{subject_id}/modules/{module_no}/upload')
async def upload_recovery_module(
    subject_id: int,
    module_no: int,
    user: CurrentTeacher,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if module_no not in {1, 2, 3, 4, 5}:
        raise HTTPException(status_code=422, detail='Module number must be between 1 and 5.')
    teacher = _teacher(db, user)
    _teacher_subject(db, teacher, subject_id)
    filename, content = await _read_pdf(file)
    extracted_text = _extract_pdf_text(content)
    previous = db.query(RecoveryModuleLibrary.file_path).filter_by(
        subject_id=subject_id,
        module_no=module_no,
    ).first()
    resource = _store_module(
        db,
        subject_id=subject_id,
        module_no=module_no,
        teacher_id=teacher.id,
        filename=filename,
        content=content,
        extracted_text=extracted_text,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        _cleanup_module_files([resource.file_path])
        raise
    if previous and previous[0] != resource.file_path:
        _cleanup_module_files([previous[0]])
    db.refresh(resource)
    return {
        'module_no': resource.module_no,
        'filename': resource.original_filename,
        'status': resource.status,
        'uploaded_at': resource.updated_at.isoformat(),
    }


@router.post('/teacher/recovery/subjects/{subject_id}/modules/upload-all')
async def upload_all_recovery_modules(
    subject_id: int,
    user: CurrentTeacher,
    files: list[UploadFile] = File(...),
    module_numbers: list[int] = Form(...),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    if not files or len(files) > 5 or len(module_numbers) != len(files):
        raise HTTPException(status_code=422, detail='Upload between one and five PDF files.')
    if any(number not in {1, 2, 3, 4, 5} for number in module_numbers) or len(set(module_numbers)) != len(module_numbers):
        raise HTTPException(status_code=422, detail='Each PDF must map to a unique module number from 1 to 5.')
    teacher = _teacher(db, user)
    _teacher_subject(db, teacher, subject_id)
    prepared = []
    for file, module_no in zip(files, module_numbers, strict=True):
        filename, content = await _read_pdf(file)
        prepared.append((module_no, filename, content, _extract_pdf_text(content)))
    for module_no, _, _, _ in prepared:
        _validate_module_replacement(db, subject_id, module_no)
    previous_paths = {
        module_no: path
        for module_no, path in db.query(
            RecoveryModuleLibrary.module_no,
            RecoveryModuleLibrary.file_path,
        ).filter(
            RecoveryModuleLibrary.subject_id == subject_id,
            RecoveryModuleLibrary.module_no.in_(module_numbers),
        ).all()
    }
    records = []
    try:
        for module_no, filename, content, extracted_text in prepared:
            records.append(_store_module(
                db,
                subject_id=subject_id,
                module_no=module_no,
                teacher_id=teacher.id,
                filename=filename,
                content=content,
                extracted_text=extracted_text,
            ))
        db.commit()
    except Exception:
        db.rollback()
        _cleanup_module_files([record.file_path for record in records])
        raise
    _cleanup_module_files([
        previous_paths[record.module_no]
        for record in records
        if record.module_no in previous_paths and previous_paths[record.module_no] != record.file_path
    ])
    return [{
        'module_no': record.module_no,
        'filename': record.original_filename,
        'status': record.status,
        'uploaded_at': record.updated_at.isoformat(),
    } for record in records]


@router.post('/teacher/recovery/plans/{plan_id}/assign')
def assign_recovery_plan(
    plan_id: int,
    user: CurrentTeacher,
    start_date: date | None = None,
    topics_per_day: int | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = _plan_access(db, user, plan_id)
    if plan.status == 'assigned':
        return _plan_data(db, plan)
    if plan.status != 'quizzes_ready':
        raise HTTPException(status_code=409, detail='Generate and review quizzes before assigning this plan.')
    student = db.query(Student).filter(Student.id == plan.student_id).one()
    if student.user_id is None:
        raise HTTPException(status_code=409, detail='The student needs a linked account to receive recovery notifications.')
    subject = db.query(Subject).filter(Subject.id == plan.subject_id).one()
    included_topics = db.query(RecoveryTopic).filter(
        RecoveryTopic.plan_id == plan.id,
        RecoveryTopic.included.is_(True),
    ).order_by(RecoveryTopic.module_no, RecoveryTopic.id).all()
    quizzes = db.query(RecoveryQuiz).filter(RecoveryQuiz.plan_id == plan.id).all()
    quizzes_by_topic = {quiz.topic_id: quiz for quiz in quizzes if quiz.topic_id is not None}
    one_line_quiz = next((quiz for quiz in quizzes if quiz.topic_id is None and quiz.quiz_key == 'one-line'), None)
    if not included_topics or one_line_quiz is None or any(topic.id not in quizzes_by_topic for topic in included_topics):
        raise HTTPException(status_code=409, detail='Every included topic and the plan review need a generated quiz.')
    if topics_per_day is not None and topics_per_day not in {1, 2, 3, 5}:
        raise HTTPException(status_code=422, detail='topics_per_day must be 1, 2, 3 or 5.')
    tasks_per_day = topics_per_day or plan.topics_per_day
    first_day = start_date or datetime.now(ZoneInfo(settings.institutional_timezone)).date()
    schedule = schedule_topics(
        included_topics,
        topics_per_day=tasks_per_day,
        start_date=first_day,
    )
    try:
        current_version = db.query(func.max(SupportPlanVersion.version)).filter(
            SupportPlanVersion.student_id == plan.student_id,
        ).scalar() or 0
        roadmap = SupportPlanVersion(
            student_id=plan.student_id,
            subject_id=plan.subject_id,
            subject_scope_review_required=False,
            teacher_id=_teacher(db, user).id,
            version=current_version + 1,
            summary=f'FA-1 quiz recovery plan for {subject.name}.',
            plan_json={'recovery_plan_id': plan.id},
            status='PUBLISHED',
            evidence_json={'source_exam': plan.source_exam, 'marks': plan.marks},
            approved_by_user_id=user.id,
            approved_at=datetime.utcnow(),
            published_at=datetime.utcnow(),
        )
        db.add(roadmap)
        db.flush()
        plan.support_plan_version_id = roadmap.id
        plan.start_date = first_day
        plan.topics_per_day = tasks_per_day
        for item in schedule:
            topic = item['topic']
            quiz = quizzes_by_topic[topic.id]
            task_date = item['scheduled_date']
            db.add(RecoveryTask(
                roadmap_version_id=roadmap.id,
                student_id=plan.student_id,
                subject_id=plan.subject_id,
                recovery_plan_id=plan.id,
                recovery_topic_id=topic.id,
                recovery_quiz_id=quiz.id,
                day_no=item['day_no'],
                subject_scope_review_required=False,
                scheduled_date=task_date,
                task_key=f'recovery-quiz-{quiz.id}',
                subject=subject.name,
                topic=topic.name,
                learning_objective=f'Practice the source-grounded topic: {topic.name}.',
                title=f'{subject.name} · {topic.name}',
                instructions='Complete the quiz using the module material shared by your teacher.',
                activity_type='QUIZ',
                priority='MEDIUM',
                source='AI_PRACTICE',
                scheduled_at=datetime.combine(task_date, time.min),
                estimated_minutes=10,
                resources=[],
                completion_criteria='Submit all quiz answers.',
                status=TASK_AVAILABLE,
                progress_percent=0,
            ))
        final_day = max(item['day_no'] for item in schedule)
        db.add(RecoveryTask(
            roadmap_version_id=roadmap.id,
            student_id=plan.student_id,
            subject_id=plan.subject_id,
            recovery_plan_id=plan.id,
            recovery_quiz_id=one_line_quiz.id,
            day_no=final_day,
            subject_scope_review_required=False,
            scheduled_date=first_day + timedelta(days=final_day - 1),
            task_key=f'recovery-quiz-{one_line_quiz.id}',
            subject=subject.name,
            topic='One-line review',
            learning_objective='Recall key facts from the selected modules.',
            title=f'{subject.name} · One-line review',
            instructions='Answer each short question using the module material shared by your teacher.',
            activity_type='QUIZ',
            priority='MEDIUM',
            source='AI_PRACTICE',
            scheduled_at=datetime.combine(first_day + timedelta(days=final_day - 1), time.min),
            estimated_minutes=10,
            resources=[],
            completion_criteria='Submit all five one-line answers.',
            status=TASK_AVAILABLE,
            progress_percent=0,
        ))
        db.add(Notification(
            user_id=student.user_id,
            kind='recovery_plan_assigned',
            title='New subject recovery quizzes',
            body=f'{subject.name} recovery quizzes start on {first_day.isoformat()}.',
            metadata_json={'recovery_plan_id': plan.id, 'subject_id': plan.subject_id},
        ))
        plan.status = 'assigned'
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail='This recovery plan could not be assigned concurrently.') from exc
    db.refresh(plan)
    return _plan_data(db, plan)


@router.get('/teacher/recovery/students/{student_id}/performance')
def recovery_student_performance(
    student_id: int,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    teacher = _teacher(db, user)
    student = _teacher_student(db, teacher, student_id)
    course_query = db.query(Course.subject_id).filter(
        Course.teacher_id == teacher.id,
        Course.subject_id.is_not(None),
    )
    if student.course_id is not None:
        course_query = course_query.filter(Course.id == student.course_id)
    elif student.section:
        course_query = course_query.filter(Course.section == student.section)
    else:
        return {'items': []}
    subject_ids = [subject_id for (subject_id,) in course_query.distinct().all()]
    if not subject_ids:
        return {'items': []}
    rows = db.query(
        func.date(QuizAttempt.submitted_at).label('attempt_date'),
        func.sum(QuizAttempt.score).label('score'),
        func.sum(QuizAttempt.max_score).label('max_score'),
        func.count(QuizAttempt.id).label('attempt_count'),
    ).join(RecoveryTask, RecoveryTask.id == QuizAttempt.task_id).filter(
        QuizAttempt.student_id == student.id,
        RecoveryTask.recovery_plan_id.is_not(None),
        RecoveryTask.subject_id.in_(subject_ids),
    ).group_by(func.date(QuizAttempt.submitted_at)).order_by(func.date(QuizAttempt.submitted_at)).all()
    return {
        'items': [{
            'date': str(row.attempt_date),
            'score': float(row.score or 0),
            'max_score': float(row.max_score or 0),
            'percent': round(float(row.score or 0) / float(row.max_score or 1) * 100, 1),
            'attempts': int(row.attempt_count),
        } for row in rows],
    }


def _student_task(db: Session, student_id: int, task_id: int) -> tuple[RecoveryTask, RecoveryQuiz]:
    task = db.query(RecoveryTask).filter(
        RecoveryTask.id == task_id,
        RecoveryTask.student_id == student_id,
        RecoveryTask.recovery_plan_id.is_not(None),
    ).first()
    if task is None:
        raise HTTPException(status_code=404, detail='Recovery quiz not found.')
    plan = db.query(RecoveryPlan).filter(
        RecoveryPlan.id == task.recovery_plan_id,
        RecoveryPlan.student_id == student_id,
        RecoveryPlan.status == 'assigned',
    ).first()
    quiz = db.query(RecoveryQuiz).filter(RecoveryQuiz.id == task.recovery_quiz_id).first()
    if plan is None or quiz is None:
        raise HTTPException(status_code=404, detail='Recovery quiz not found.')
    return task, quiz


@router.get('/students/me/recovery-quizzes/today')
def student_today_recovery_quizzes(
    student: CurrentStudent,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    today = datetime.now(ZoneInfo(settings.institutional_timezone)).date()
    tasks = db.query(RecoveryTask).filter(
        RecoveryTask.student_id == student.id,
        RecoveryTask.recovery_plan_id.is_not(None),
        RecoveryTask.scheduled_date <= today,
        RecoveryTask.status != TASK_COMPLETED,
    ).order_by(RecoveryTask.scheduled_date, RecoveryTask.day_no, RecoveryTask.id).all()
    items = []
    for task in tasks:
        task, quiz = _student_task(db, student.id, task.id)
        plan = db.query(RecoveryPlan).filter(RecoveryPlan.id == task.recovery_plan_id).one()
        subject = db.query(Subject).filter(Subject.id == plan.subject_id).one()
        items.append({
            'task_id': task.id,
            'plan_id': plan.id,
            'subject': subject.name,
            'topic': task.topic,
            'day_no': task.day_no,
            'scheduled_date': task.scheduled_date.isoformat() if task.scheduled_date else None,
            'title': task.title,
            'max_score': quiz.max_score,
            'questions': [{
                'question': question['question'],
                'question_type': question['question_type'],
                'options': question['options'],
                'marks': question['marks'],
            } for question in quiz.questions],
        })
    return {'date': today.isoformat(), 'items': items}


@router.post('/students/me/recovery-quizzes/{task_id}/attempts')
def submit_recovery_quiz(
    task_id: int,
    payload: QuizSubmission,
    student: CurrentStudent,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    task, quiz = _student_task(db, student.id, task_id)
    try:
        score, results = grade_quiz(quiz.questions, payload.answers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = db.query(QuizAttempt).filter_by(task_id=task.id, student_id=student.id).first()
    if existing is not None:
        if existing.answers != payload.answers:
            raise HTTPException(status_code=409, detail='This quiz already has a submitted attempt.')
        return {
            'score': existing.score,
            'max_score': existing.max_score,
            'percent': round(existing.score / existing.max_score * 100, 1),
            'results': results,
        }
    attempt = QuizAttempt(
        task_id=task.id,
        student_id=student.id,
        score=score,
        max_score=quiz.max_score,
        answers=payload.answers,
        submitted_at=datetime.utcnow(),
    )
    task.status = TASK_COMPLETED
    task.progress_percent = 100
    task.assessed_score = score
    task.completed_at = datetime.utcnow()
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail='This quiz was submitted concurrently.') from exc
    return {
        'score': score,
        'max_score': quiz.max_score,
        'percent': round(score / quiz.max_score * 100, 1),
        'results': results,
    }
