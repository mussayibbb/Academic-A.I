import math
import re
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import (
    AcademicDataImportBatch,
    AcademicRiskOutcome,
    AuditLog,
    Student,
    StudentSubjectAcademicRecord,
    Subject,
)

SUBJECTS = {
    'EVS': 'Environmental Studies',
    'CN': 'Computer Networks',
    'TOC': 'Theory of Computation',
    'RM': 'Research Methodology',
    'SE': 'Software Engineering',
}
MARK_FIELDS = ('fa1', 'fa2', 'assignment_mark')
ACADEMIC_PERIOD_PATTERN = re.compile(r'^\d{4}-S[12]$')


def normalize_academic_period(value: str) -> str:
    normalized = value.strip().upper()
    if not ACADEMIC_PERIOD_PATTERN.fullmatch(normalized):
        raise ValueError('academic_period must use the YYYY-S1 or YYYY-S2 format.')
    return normalized


def normalized_fa1_mark(record: StudentSubjectAcademicRecord | None) -> float | None:
    if record is None or record.fa1 is None or record.fa1_maximum_marks is None:
        return None
    if (
        not record.fa1_maximum_verified
        or not math.isfinite(record.fa1)
        or not math.isfinite(record.fa1_maximum_marks)
        or record.fa1_maximum_marks <= 0
        or record.fa1 > record.fa1_maximum_marks
    ):
        return None
    return record.fa1 * 25 / record.fa1_maximum_marks


def internal_mark(record: StudentSubjectAcademicRecord | None) -> float | None:
    if record is None or any(getattr(record, field) is None for field in MARK_FIELDS):
        return None
    if record.fa1_maximum_marks is None:
        fa1_for_register = record.fa1 if math.isfinite(record.fa1) else None
    else:
        fa1_for_register = normalized_fa1_mark(record)
    if fa1_for_register is None:
        return None
    return (fa1_for_register + record.fa2) / 2 + record.assignment_mark


def academic_totals(records: dict[str, StudentSubjectAcademicRecord]) -> dict[str, float | None]:
    internal_marks = {code: internal_mark(records.get(code)) for code in SUBJECTS}
    if any(value is None for value in internal_marks.values()):
        return {'total_internal': None, 'average_internal': None}
    total = sum(value for value in internal_marks.values() if value is not None)
    return {'total_internal': total, 'average_internal': total / len(SUBJECTS)}


def serialize_record(record: StudentSubjectAcademicRecord | None) -> dict[str, Any]:
    fa1_for_average = None
    if record and record.fa1_maximum_marks is None and record.fa1 is not None:
        fa1_for_average = record.fa1 if math.isfinite(record.fa1) else None
    elif record:
        fa1_for_average = normalized_fa1_mark(record)
    return {
        'record_id': record.id if record else None,
        'fa1': record.fa1 if record else None,
        'fa1_observed_at': record.fa1_observed_at.isoformat() if record and record.fa1_observed_at else None,
        'fa1_maximum_marks': record.fa1_maximum_marks if record else None,
        'fa1_maximum_verified': bool(record and record.fa1_maximum_verified),
        'fa2': record.fa2 if record else None,
        'fa2_observed_at': record.fa2_observed_at.isoformat() if record and record.fa2_observed_at else None,
        'assignment_mark': record.assignment_mark if record else None,
        'assignment_observed_at': record.assignment_observed_at.isoformat() if record and record.assignment_observed_at else None,
        'fa1_normalized': normalized_fa1_mark(record),
        'fa_average': (
            (fa1_for_average + record.fa2) / 2
            if record and fa1_for_average is not None and record.fa2 is not None
            else None
        ),
        'internal_mark': internal_mark(record),
        'attendance_rate': record.attendance_rate if record else None,
        'attendance_observed_at': record.attendance_observed_at.isoformat() if record and record.attendance_observed_at else None,
        'import_batch_id': record.import_batch_id if record else None,
    }


def academic_periods(db: Session) -> list[str]:
    record_periods = db.query(StudentSubjectAcademicRecord.academic_period).distinct().all()
    outcome_periods = db.query(AcademicRiskOutcome.academic_period).distinct().all()
    return sorted({row[0] for row in (*record_periods, *outcome_periods) if row[0] != 'UNSPECIFIED'}, reverse=True)


def selected_academic_period(db: Session, academic_period: str | None) -> str | None:
    if academic_period:
        return normalize_academic_period(academic_period)
    periods = academic_periods(db)
    return periods[0] if periods else None


def subject_records_by_student(db: Session, student_ids: list[int], academic_period: str | None = None) -> dict[int, dict[str, StudentSubjectAcademicRecord]]:
    if not student_ids:
        return {}
    result: dict[int, dict[str, StudentSubjectAcademicRecord]] = {}
    query = (
        db.query(StudentSubjectAcademicRecord)
        .join(Subject)
        .filter(StudentSubjectAcademicRecord.student_id.in_(student_ids), Subject.code.in_(SUBJECTS))
    )
    if academic_period:
        query = query.filter(StudentSubjectAcademicRecord.academic_period == academic_period)
    rows = query.all()
    for record in rows:
        result.setdefault(record.student_id, {})[record.subject.code] = record
    return result


def subject_record_evidence(
    db: Session,
    student_id: int,
    academic_period: str | None,
) -> list[dict[str, Any]]:
    query = (
        db.query(StudentSubjectAcademicRecord, Subject, AcademicDataImportBatch)
        .join(Subject, Subject.id == StudentSubjectAcademicRecord.subject_id)
        .outerjoin(
            AcademicDataImportBatch,
            AcademicDataImportBatch.id == StudentSubjectAcademicRecord.import_batch_id,
        )
        .filter(StudentSubjectAcademicRecord.student_id == student_id)
    )
    if academic_period is not None:
        query = query.filter(StudentSubjectAcademicRecord.academic_period == academic_period)
    rows = (
        query
        .order_by(Subject.name, Subject.code, StudentSubjectAcademicRecord.id)
        .all()
    )
    record_ids = [record.id for record, _, _ in rows]
    reviewed_sources: dict[int, str] = {}
    if record_ids:
        audits = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == 'academic_subject_record.fa1_maximum_verified',
                AuditLog.resource_type == 'student_subject_academic_record',
                AuditLog.resource_id.in_([str(record_id) for record_id in record_ids]),
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .all()
        )
        for audit in audits:
            record_id = int(audit.resource_id)
            reviewed_sources.setdefault(record_id, audit.detail.get('source_reference', ''))
    return [{
        'record_id': record.id,
        'student_id': record.student_id,
        'subject_id': subject.id,
        'code': subject.code,
        'name': subject.name,
        'academic_period': record.academic_period,
        'fa1': record.fa1,
        'fa1_observed_at': record.fa1_observed_at.isoformat() if record.fa1_observed_at else None,
        'temporal_evidence_verified': bool(record.temporal_evidence_verified),
        'fa1_maximum_marks': record.fa1_maximum_marks,
        'fa1_maximum_verified': bool(record.fa1_maximum_verified),
        'fa1_maximum_source_reference': reviewed_sources.get(record.id) or None,
        'import_batch_id': record.import_batch_id,
        'source_filename': import_batch.source_filename if import_batch else None,
    } for record, subject, import_batch in rows]


def build_register_row(student: Student, records: dict[str, StudentSubjectAcademicRecord], academic_period: str | None) -> dict[str, Any]:
    subjects = {code: serialize_record(records.get(code)) for code in SUBJECTS}
    return {
        'id': student.id,
        'student_id': student.student_id,
        'full_name': student.full_name,
        'department': student.department,
        'program': student.program,
        'semester': student.semester,
        'section': student.section,
        'data_origin': student.data_origin,
        'academic_period': academic_period,
        'subjects': subjects,
        **academic_totals(records),
    }
