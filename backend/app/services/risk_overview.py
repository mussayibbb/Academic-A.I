from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

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
from backend.app.services.academic_records import normalize_academic_period

RISK_ORDER = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2, 'INSUFFICIENT_EVIDENCE': 3}


def build_teacher_risk_overview(
    db: Session,
    user: User,
    *,
    student_id: int | None = None,
    search: str | None = None,
    section: str | None = None,
    academic_period: str | None = None,
    risk_category: str | None = None,
) -> dict[str, Any]:
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise PermissionError('Teacher profile is not linked.')

    courses = db.query(Course).filter(Course.teacher_id == teacher.id).order_by(Course.id).all()
    course_by_id = {course.id: course for course in courses}
    course_ids = list(course_by_id)
    sections = sorted({course.section for course in courses if course.section})
    if not course_ids:
        return {
            'academic_period': None,
            'academic_periods': [],
            'classes': [],
            'counts': {category: 0 for category in RISK_ORDER},
            'items': [],
            'total': 0,
        }

    assigned_filter = or_(
        Student.course_id.in_(course_ids),
        and_(Student.course_id.is_(None), Student.section.in_(sections)) if sections else False,
    )
    students_query = db.query(Student).filter(assigned_filter)
    if student_id is not None:
        students_query = students_query.filter(Student.id == student_id)
    if section:
        students_query = students_query.filter(Student.section == section)
    if search and search.strip():
        needle = f'%{search.strip()}%'
        students_query = students_query.filter(
            Student.full_name.ilike(needle) | Student.student_id.ilike(needle),
        )
    students = students_query.order_by(Student.full_name, Student.id).all()
    if student_id is not None and not students:
        raise LookupError('Student not found in the teacher’s assigned classes.')

    student_ids = [student.id for student in students]
    if not student_ids:
        return {
            'academic_period': None,
            'academic_periods': [],
            'classes': sections,
            'counts': {category: 0 for category in RISK_ORDER},
            'items': [],
            'total': 0,
        }

    record_periods = (
        db.query(StudentSubjectAcademicRecord.academic_period)
        .filter(StudentSubjectAcademicRecord.student_id.in_(student_ids))
        .distinct()
        .all()
    )
    outcome_periods = (
        db.query(AcademicRiskOutcome.academic_period)
        .filter(AcademicRiskOutcome.student_id.in_(student_ids))
        .distinct()
        .all()
    )
    periods = sorted(
        {row[0] for row in (*record_periods, *outcome_periods) if row[0] != 'UNSPECIFIED'},
        reverse=True,
    )
    try:
        selected_period = normalize_academic_period(academic_period) if academic_period else (periods[0] if periods else None)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    subject_records: dict[int, list[dict[str, Any]]] = {}
    if selected_period:
        rows = (
            db.query(StudentSubjectAcademicRecord, Subject)
            .join(Subject, Subject.id == StudentSubjectAcademicRecord.subject_id)
            .outerjoin(
                AcademicDataImportBatch,
                AcademicDataImportBatch.id == StudentSubjectAcademicRecord.import_batch_id,
            )
            .add_entity(AcademicDataImportBatch)
            .filter(
                StudentSubjectAcademicRecord.student_id.in_(student_ids),
                StudentSubjectAcademicRecord.academic_period == selected_period,
            )
            .order_by(Subject.name, Subject.code)
            .all()
        )
        for record, subject, import_batch in rows:
            subject_records.setdefault(record.student_id, []).append({
                'record_id': record.id,
                'id': subject.id,
                'code': subject.code,
                'name': subject.name,
                'academic_period': record.academic_period,
                'fa1': record.fa1,
                'fa1_observed_at': record.fa1_observed_at,
                'temporal_evidence_verified': bool(record.temporal_evidence_verified),
                'import_batch_id': record.import_batch_id,
                'source_filename': import_batch.source_filename if import_batch else None,
                'fa2': record.fa2,
                'fa2_observed_at': record.fa2_observed_at,
                'assignment_mark': record.assignment_mark,
                'assignment_observed_at': record.assignment_observed_at,
                'attendance_rate': record.attendance_rate,
                'attendance_observed_at': record.attendance_observed_at,
            })

    outcomes: dict[int, AcademicRiskOutcome] = {}
    if selected_period:
        outcomes = {
            outcome.student_id: outcome
            for outcome in db.query(AcademicRiskOutcome).filter(
                AcademicRiskOutcome.student_id.in_(student_ids),
                AcademicRiskOutcome.academic_period == selected_period,
            ).all()
        }

    items: list[dict[str, Any]] = []
    for student in students:
        outcome = outcomes.get(student.id)
        category = outcome.risk_level.upper() if outcome else 'INSUFFICIENT_EVIDENCE'
        records = subject_records.get(student.id, [])
        indicators = []
        if outcome:
            indicators.append(f'Recorded {category.lower()} risk outcome from {outcome.outcome_source}.')
        else:
            indicators.append('No independently recorded risk outcome is available for this semester.')
        if not records:
            indicators.append('No subject academic records are available for this semester.')
        else:
            if not any(record['fa1'] is not None and record['fa1_observed_at'] for record in records):
                indicators.append('No dated FA-1 mark is available for this semester.')
            elif any(
                record['fa1'] is not None
                and record['fa1_observed_at'] is not None
                and not record['temporal_evidence_verified']
                for record in records
            ):
                indicators.append('Some FA-1 observation dates have not been temporally verified.')
            if not any(record['attendance_rate'] is not None and record['attendance_observed_at'] for record in records):
                indicators.append('No dated subject attendance record is available for this semester.')

        course = course_by_id.get(student.course_id) if student.course_id else next(
            (item for item in courses if student.section and item.section == student.section),
            None,
        )
        items.append({
            'id': student.id,
            'student_id': student.student_id,
            'full_name': student.full_name,
            'section': student.section,
            'course_name': course.name if course else None,
            'risk_category': category,
            'risk_outcome': {
                'risk_level': category,
                'observed_at': outcome.observed_at,
                'outcome_source': outcome.outcome_source,
                'academic_period': outcome.academic_period,
            } if outcome else None,
            'subjects': records,
            'indicators': indicators,
        })

    counts = {
        category: sum(item['risk_category'] == category for item in items)
        for category in RISK_ORDER
    }
    if risk_category:
        wanted = risk_category.strip().upper()
        if wanted not in {*RISK_ORDER, 'ALL'}:
            raise ValueError('risk_category must be HIGH, MEDIUM, LOW, INSUFFICIENT_EVIDENCE, or ALL.')
        if wanted != 'ALL':
            items = [item for item in items if item['risk_category'] == wanted]

    items.sort(key=lambda item: (
        RISK_ORDER[item['risk_category']],
        -(item['risk_outcome']['observed_at'].timestamp()) if item['risk_outcome'] else 0,
        item['full_name'].casefold(),
        item['id'],
    ))
    return {
        'academic_period': selected_period,
        'academic_periods': periods,
        'classes': sections,
        'counts': counts,
        'items': items,
        'total': len(items),
    }
