import math
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend.app.models import (
    AcademicDataImportBatch,
    Assessment,
    Course,
    Mark,
    Student,
    StudentSubjectAcademicRecord,
    Subject,
    SubjectFocusPolicy,
    Teacher,
    TopicLearningAssignment,
    User,
)

_FIRST_INTERNAL_ASSESSMENT = re.compile(
    r'\b(?:FA[\s_-]*1|FA[\s_-]*I|FIRST\s+(?:INTERNAL|ASSESSMENT)|INTERNAL\s+(?:ASSESSMENT\s*)?1|IA[\s_-]*1)\b',
    re.IGNORECASE,
)


def current_subject_focus_policy(db: Session) -> SubjectFocusPolicy | None:
    return db.query(SubjectFocusPolicy).order_by(SubjectFocusPolicy.version.desc()).first()


def serialize_subject_focus_policy(policy: SubjectFocusPolicy | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {
        'id': policy.id,
        'version': policy.version,
        'threshold_score': policy.threshold_score,
        'threshold_maximum': policy.threshold_maximum,
        'strictly_below': True,
        'active_for_classification': (
            policy.threshold_maximum == 25
            and 0 <= policy.threshold_score <= 25
        ),
        'created_at': policy.created_at.isoformat(),
        'change_reason': policy.change_reason,
    }


def _mark_event(mark: Mark) -> dict[str, Any]:
    assessment = mark.assessment
    return {
        'evidence_id': f'mark:{mark.id}',
        'mark_id': mark.id,
        'student_id': mark.student_id,
        'assessment_id': assessment.id if assessment else None,
        'assessment': assessment.title if assessment else 'Unknown assessment',
        'score': mark.score,
        'maximum_marks': assessment.maximum_marks if assessment else None,
        'maximum_marks_verified': bool(assessment and assessment.maximum_marks_verified),
        'temporal_evidence_verified': None,
        'recorded_at': mark.recorded_at,
        'official_result': mark.official_result,
        'source': 'Recorded assessment',
        'mark_import_batch_id': mark.import_batch_id,
        'assessment_import_batch_id': assessment.import_batch_id if assessment else None,
        'academic_period': None,
    }


def _subject_record_events(
    record: StudentSubjectAcademicRecord,
    *,
    first_internal_only: bool = False,
) -> list[dict[str, Any]]:
    fields = [('fa1', 'FA-1', record.fa1, record.fa1_observed_at)]
    if not first_internal_only:
        fields.extend((
            ('fa2', 'FA-2', record.fa2, record.fa2_observed_at),
            ('assignment_mark', 'Assignment', record.assignment_mark, record.assignment_observed_at),
        ))
    return [{
        'evidence_id': f'academic-record:{record.id}:{field}',
        'assessment_id': None,
        'assessment': label,
        'score': score,
        'maximum_marks': record.fa1_maximum_marks if field == 'fa1' else None,
        'maximum_marks_verified': bool(record.fa1_maximum_verified) if field == 'fa1' else False,
        'temporal_evidence_verified': bool(record.temporal_evidence_verified),
        'recorded_at': observed_at,
        'official_result': None,
        'source': 'Subject academic record',
        'academic_period': record.academic_period,
    } for field, label, score, observed_at in fields if score is not None]


def _assessment_evidence(
    event: dict[str, Any],
    *,
    threshold_score: float | None,
    threshold_maximum: float | None,
) -> dict[str, Any]:
    maximum = event.get('maximum_marks')
    score = event.get('score')
    normalized: float | None = None
    below_threshold: bool | None = None
    reason = None
    valid_score = isinstance(score, (int, float)) and math.isfinite(score) and score >= 0
    valid_maximum = isinstance(maximum, (int, float)) and math.isfinite(maximum) and maximum > 0

    if not valid_score:
        reason = 'The recorded score is missing or invalid.'
    elif event.get('source') == 'Subject academic record' and not event.get('recorded_at'):
        reason = 'The FA-1 observation date is missing.'
    elif (
        event.get('source') == 'Subject academic record'
        and event.get('temporal_evidence_verified') is not True
    ):
        reason = 'The FA-1 observation date/source has not been temporally verified.'
    elif not valid_maximum:
        reason = 'The assessment maximum is missing or invalid.'
    elif not event.get('maximum_marks_verified'):
        reason = 'The assessment maximum has not been verified from an explicit source.'
    elif score > maximum:
        reason = 'The recorded score exceeds the assessment maximum.'
    elif (
        threshold_maximum != 25
        or threshold_score is None
        or not 0 <= threshold_score <= 25
    ):
        reason = 'The active subject-focus policy is not configured on the required 25-mark scale.'
    else:
        normalized_decimal = Decimal(str(score)) * Decimal('25') / Decimal(str(maximum))
        normalized = float(round(normalized_decimal, 6))
        below_threshold = normalized_decimal < Decimal(str(threshold_score))

    return {
        'evidence_id': event['evidence_id'],
        'mark_id': event.get('mark_id'),
        'student_id': event.get('student_id'),
        'assessment_id': event.get('assessment_id'),
        'assessment': event['assessment'],
        'score': float(score) if valid_score else None,
        'maximum_marks': float(maximum) if valid_maximum else None,
        'maximum_marks_verified': bool(event.get('maximum_marks_verified')),
        'temporal_evidence_verified': event.get('temporal_evidence_verified'),
        'normalized_score': normalized,
        'below_threshold': below_threshold,
        'recorded_at': event['recorded_at'].isoformat() if event.get('recorded_at') else None,
        'official_result': event.get('official_result'),
        'source': event['source'],
        'mark_import_batch_id': event.get('mark_import_batch_id'),
        'assessment_import_batch_id': event.get('assessment_import_batch_id'),
        'academic_period': event.get('academic_period'),
        'evidence_status': 'VERIFIED' if reason is None else 'INSUFFICIENT_EVIDENCE',
        'reason': reason,
    }


def evaluate_subject_assessments(
    marks: list[Mark],
    policy: SubjectFocusPolicy | None,
    subject_records: list[StudentSubjectAcademicRecord] | None = None,
    *,
    first_internal_only: bool = False,
) -> dict[str, Any]:
    threshold_score = policy.threshold_score if policy else None
    threshold_maximum = policy.threshold_maximum if policy else None
    all_events = [_mark_event(mark) for mark in marks]
    for record in subject_records or []:
        all_events.extend(_subject_record_events(record))
    history = [
        _assessment_evidence(event, threshold_score=threshold_score, threshold_maximum=threshold_maximum)
        for event in sorted(
            all_events,
            key=lambda item: (item.get('recorded_at') or datetime.min, item['evidence_id']),
            reverse=True,
        )
    ]
    relevant_marks = [
        mark for mark in marks
        if not first_internal_only
        or (
            mark.assessment is not None
            and bool(_FIRST_INTERNAL_ASSESSMENT.search(mark.assessment.title))
        )
    ]
    events = [_mark_event(mark) for mark in relevant_marks]
    for record in subject_records or []:
        events.extend(_subject_record_events(record, first_internal_only=first_internal_only))
    evidence = [
        _assessment_evidence(event, threshold_score=threshold_score, threshold_maximum=threshold_maximum)
        for event in sorted(
            events,
            key=lambda item: (item.get('recorded_at') or datetime.min, item['evidence_id']),
            reverse=True,
        )
    ]

    if not evidence:
        return {
            'status': 'INSUFFICIENT_EVIDENCE',
            'support_required': None,
            'support_triggers': [],
            'reason': (
                'No first-internal or FA-1 evidence is available for this subject.'
                if first_internal_only
                else 'No persisted assessment marks are available for this subject.'
            ),
            'latest_assessment': None,
            'latest_assessments': [],
            'official_failure': None,
            'official_failure_status': None,
            'evidence_scope': 'FIRST_INTERNAL' if first_internal_only else 'LATEST_ASSESSMENT',
            'history': history,
        }

    undated_evidence = [item for item in evidence if item['recorded_at'] is None]
    latest_timestamp = max((event['recorded_at'] for event in events if event.get('recorded_at')), default=None)
    latest_evidence = [
        item for item in evidence
        if item['recorded_at'] == latest_timestamp.isoformat()
    ] if latest_timestamp else []
    official_results = [item['official_result'] for item in latest_evidence if item['official_result']]
    official_failure = True if 'FAIL' in official_results else False if latest_evidence and all(result == 'PASS' for result in official_results) and len(official_results) == len(latest_evidence) else None

    if undated_evidence:
        status = 'INSUFFICIENT_EVIDENCE'
        reason = 'One or more relevant assessment records have no observation date, so the latest assessment cannot be determined.'
        latest = None
        latest_evidence = undated_evidence
    elif latest_timestamp is None:
        status = 'INSUFFICIENT_EVIDENCE'
        reason = 'Assessment records do not have a usable recorded date.'
        latest = None
    elif len(latest_evidence) != 1:
        status = 'INSUFFICIENT_EVIDENCE'
        reason = 'Multiple assessments share the latest recorded time, so the current assessment is ambiguous.'
        latest = None
    else:
        latest = latest_evidence[0]
        if latest['evidence_status'] != 'VERIFIED':
            status = 'INSUFFICIENT_EVIDENCE'
            reason = latest['reason']
        else:
            status = 'LOW' if latest['below_threshold'] else 'NOT_LOW'
            reason = (
                f"Latest recorded result is {latest['score']} / {latest['maximum_marks']} "
                f"({latest['normalized_score']:.2f} / {threshold_maximum} normalized), "
                f"{'strictly below' if status == 'LOW' else 'not below'} "
                f"the configured {threshold_score} / {threshold_maximum} support threshold."
            )

    return {
        'status': status,
        'support_required': (
            True if status == 'LOW' or official_failure is True
            else None if status == 'INSUFFICIENT_EVIDENCE'
            else False
        ),
        'support_triggers': [
            trigger for trigger, active in (
                ('LOW_SCORE', status == 'LOW'),
                ('OFFICIAL_FAIL', official_failure is True),
            ) if active
        ],
        'reason': reason,
        'latest_assessment': latest,
        'latest_assessments': latest_evidence,
        'official_failure': official_failure,
        'official_failure_status': (
            'FAIL' if official_failure is True
            else 'PASS' if official_failure is False
            else None
        ),
        'evidence_scope': 'FIRST_INTERNAL' if first_internal_only else 'LATEST_ASSESSMENT',
        'history': history,
    }


def _teacher_scope(db: Session, user: User) -> tuple[Teacher, list[Course], list[Student]]:
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise PermissionError('Teacher profile is not linked.')
    courses = db.query(Course).filter(Course.teacher_id == teacher.id).order_by(Course.id).all()
    if not courses:
        return teacher, [], []
    course_ids = [course.id for course in courses]
    sections = {course.section for course in courses if course.section}
    assigned_filter = or_(
        Student.course_id.in_(course_ids),
        and_(Student.course_id.is_(None), Student.section.in_(sections)) if sections else False,
    )
    students = db.query(Student).filter(assigned_filter).order_by(Student.full_name, Student.id).all()
    return teacher, courses, students


def _load_marks_by_student_subject(
    db: Session,
    *,
    course_ids: list[int],
    student_ids: list[int],
) -> dict[tuple[int, int], list[Mark]]:
    grouped: dict[tuple[int, int], list[Mark]] = defaultdict(list)
    if not course_ids or not student_ids:
        return grouped
    rows = (
        db.query(Mark, Course.subject_id)
        .join(Assessment, Assessment.id == Mark.assessment_id)
        .join(Course, Course.id == Assessment.course_id)
        .filter(
            Course.id.in_(course_ids),
            Course.subject_id.is_not(None),
            Mark.student_id.in_(student_ids),
        )
        .order_by(Mark.recorded_at.desc(), Mark.id.desc())
        .all()
    )
    for mark, subject_id in rows:
        grouped[(mark.student_id, subject_id)].append(mark)
    return grouped


def build_teacher_subject_focus(
    db: Session,
    user: User,
    *,
    subject_id: int | None = None,
    student_id: int | None = None,
    first_internal_only: bool = False,
) -> dict[str, Any]:
    teacher, courses, students = _teacher_scope(db, user)
    student_by_id = {student.id: student for student in students}
    if student_id is not None and student_id not in student_by_id:
        raise LookupError('Student not found in the teacher’s assigned classes.')

    subject_ids = {course.subject_id for course in courses if course.subject_id is not None}
    if subject_id is not None:
        if subject_id not in subject_ids:
            raise LookupError('Subject not available to this teacher.')
        subject_ids = {subject_id}
    subjects = db.query(Subject).filter(Subject.id.in_(subject_ids)).order_by(Subject.name, Subject.code).all() if subject_ids else []
    policy = current_subject_focus_policy(db)
    policy_out = serialize_subject_focus_policy(policy)
    scoped_course_ids = [
        course.id for course in courses if course.subject_id in subject_ids
    ]
    marks_by_key = _load_marks_by_student_subject(
        db,
        course_ids=scoped_course_ids,
        student_ids=[student.id for student in students],
    )
    import_batch_ids = {
        batch_id
        for marks in marks_by_key.values()
        for mark in marks
        for batch_id in (
            mark.import_batch_id,
            mark.assessment.import_batch_id if mark.assessment else None,
        )
        if batch_id is not None
    }
    import_filenames = {
        batch.id: batch.source_filename
        for batch in db.query(AcademicDataImportBatch).filter(
            AcademicDataImportBatch.id.in_(import_batch_ids),
        ).all()
    } if import_batch_ids else {}

    result_subjects = []
    for subject in subjects:
        subject_courses = [course for course in courses if course.subject_id == subject.id]
        subject_course_ids = [course.id for course in subject_courses]
        subject_sections = {course.section for course in subject_courses if course.section}
        if student_id is not None:
            roster = [student_by_id[student_id]]
        elif not subject_courses:
            roster = students
        else:
            roster = [
                student for student in students
                if student.course_id in subject_course_ids
                or (student.course_id is None and student.section in subject_sections)
            ]
        if student_id is not None:
            roster = [student for student in roster if student.id == student_id]
            if not roster:
                raise LookupError('Student is not assigned to this subject in the teacher’s classes.')
        records_by_key: dict[tuple[int, int], list[StudentSubjectAcademicRecord]] = defaultdict(list)
        if roster:
            for record in db.query(StudentSubjectAcademicRecord).filter(
                StudentSubjectAcademicRecord.subject_id == subject.id,
                StudentSubjectAcademicRecord.student_id.in_([student.id for student in roster]),
            ).all():
                records_by_key[(record.student_id, record.subject_id)].append(record)
        rows = []
        for student in roster:
            evaluation = evaluate_subject_assessments(
                marks_by_key.get((student.id, subject.id), []),
                policy,
                records_by_key.get((student.id, subject.id), []),
                first_internal_only=first_internal_only,
            )
            for item in evaluation['history'] + evaluation['latest_assessments']:
                item['source_filenames'] = list(dict.fromkeys(
                    import_filenames[batch_id]
                    for batch_id in (
                        item.get('mark_import_batch_id'),
                        item.get('assessment_import_batch_id'),
                    )
                    if batch_id in import_filenames
                ))
            records = records_by_key.get((student.id, subject.id), [])
            latest_attendance = max(
                (
                    record for record in records
                    if record.attendance_rate is not None and record.attendance_observed_at is not None
                ),
                key=lambda record: record.attendance_observed_at,
                default=None,
            )
            rows.append({
                'id': student.id,
                'student_id': student.student_id,
                'full_name': student.full_name,
                'section': student.section,
                'attendance_rate': latest_attendance.attendance_rate if latest_attendance else None,
                'attendance_observed_at': (
                    latest_attendance.attendance_observed_at.isoformat()
                    if latest_attendance else None
                ),
                'attendance_academic_period': latest_attendance.academic_period if latest_attendance else None,
                **evaluation,
            })
        status_order = {'LOW': 0, 'INSUFFICIENT_EVIDENCE': 1, 'NOT_LOW': 2}
        rows.sort(key=lambda row: (
            status_order[row['status']],
            row['latest_assessment']['normalized_score'] if row['latest_assessment'] else float('inf'),
            row['full_name'].casefold(),
        ))
        result_subjects.append({
            'id': subject.id,
            'code': subject.code,
            'name': subject.name,
            'students': rows,
            'counts': {
                status: sum(row['status'] == status for row in rows)
                for status in ('LOW', 'NOT_LOW', 'INSUFFICIENT_EVIDENCE')
            },
        })
    return {
        'policy': policy_out,
        'evidence_scope': 'FIRST_INTERNAL' if first_internal_only else 'LATEST_ASSESSMENT',
        'subjects': result_subjects,
    }


def build_student_subject_focus(
    db: Session,
    student: Student,
    *,
    first_internal_only: bool = False,
) -> dict[str, Any]:
    subject_ids: set[int] = set()
    if student.course_id:
        course = db.query(Course).filter(Course.id == student.course_id).first()
        if course and course.subject_id:
            subject_ids.add(course.subject_id)
    subject_ids.update(
        row[0] for row in db.query(TopicLearningAssignment.subject_id)
        .filter(TopicLearningAssignment.student_id == student.id)
        .distinct().all()
    )
    subject_ids.update(
        row[0] for row in db.query(StudentSubjectAcademicRecord.subject_id)
        .filter(StudentSubjectAcademicRecord.student_id == student.id)
        .distinct().all()
    )
    subjects = db.query(Subject).filter(Subject.id.in_(subject_ids)).order_by(Subject.name, Subject.code).all() if subject_ids else []
    policy = current_subject_focus_policy(db)
    course_ids = [student.course_id] if student.course_id else []
    marks_by_key = _load_marks_by_student_subject(db, course_ids=course_ids, student_ids=[student.id])
    records_by_key: dict[int, list[StudentSubjectAcademicRecord]] = defaultdict(list)
    for record in db.query(StudentSubjectAcademicRecord).filter(
        StudentSubjectAcademicRecord.student_id == student.id,
    ).all():
        records_by_key[record.subject_id].append(record)
    return {
        'policy': serialize_subject_focus_policy(policy),
        'subjects': [{
            'id': subject.id,
            'code': subject.code,
            'name': subject.name,
            **evaluate_subject_assessments(
                marks_by_key.get((student.id, subject.id), []),
                policy,
                records_by_key.get(subject.id, []),
                first_internal_only=first_internal_only,
            ),
        } for subject in subjects],
        'evidence_scope': 'FIRST_INTERNAL' if first_internal_only else 'LATEST_ASSESSMENT',
    }
