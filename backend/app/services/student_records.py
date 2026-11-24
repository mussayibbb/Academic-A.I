import csv
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import AcademicDataImportBatch, AcademicDataImportRow, Course, Student


DATASET_TYPE = 'student_records'
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
ALLOWED_ORIGINS = {'DEMO DATA', 'INSTITUTIONAL IMPORT'}
_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_CSV_FIELDS = {
    'student_id',
    'full_name',
    'email',
    'program',
    'year',
    'semester',
    'section',
    'department',
    'course_code',
    'previous_gpa',
    'current_gpa',
    'attendance',
    'assessment',
    'assignments_completed',
}
_NUMERIC_FIELDS = {
    'previous_gpa': (0.0, 4.0),
    'current_gpa': (0.0, 4.0),
    'attendance': (0.0, 100.0),
    'assessment': (0.0, 100.0),
    'assignments_completed': (0.0, None),
}


class StudentRecordImportError(ValueError):
    pass


class StudentRecordConflict(RuntimeError):
    pass


def _normalise_header(value: str) -> str:
    return value.strip().lower().replace(' ', '_').replace('-', '_')


def _student_values(row: dict[str, Any], db: Session) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    student_id = str(row.get('student_id') or '').strip()
    full_name = str(row.get('full_name') or '').strip()
    if not student_id or len(student_id) > 40:
        errors.append('student_id is required and must be at most 40 characters.')
    if not full_name or len(full_name) > 160:
        errors.append('full_name is required and must be at most 160 characters.')

    email_value = str(row.get('email') or '').strip()
    email: str | None = None
    if email_value:
        try:
            email = str(_EMAIL_ADAPTER.validate_python(email_value)).lower()
        except ValidationError:
            errors.append('email must be a valid email address.')

    values: dict[str, Any] = {
        'student_id': student_id,
        'full_name': full_name,
        'email': email,
    }
    for field in ('program', 'year', 'semester', 'section', 'department'):
        value = str(row.get(field) or '').strip()
        values[field] = value or None

    field_limits = {'program': 160, 'year': 30, 'semester': 40, 'section': 40, 'department': 160}
    for field, limit in field_limits.items():
        if values[field] is not None and len(values[field]) > limit:
            errors.append(f'{field} must be at most {limit} characters.')

    course_code = str(row.get('course_code') or '').strip()
    if course_code:
        course = db.query(Course).filter(func.lower(Course.code) == course_code.lower()).first()
        if course is None:
            errors.append('course_code does not match an existing course.')
        else:
            values['course_id'] = course.id
    else:
        values['course_id'] = None

    for field, (minimum, maximum) in _NUMERIC_FIELDS.items():
        raw_value = str(row.get(field) or '').strip()
        if not raw_value:
            values[field] = 0.0
            continue
        try:
            number = float(raw_value)
        except ValueError:
            errors.append(f'{field} must be a number.')
            continue
        if not math.isfinite(number) or number < minimum or (maximum is not None and number > maximum):
            upper = f' and at most {maximum:g}' if maximum is not None else ''
            errors.append(f'{field} must be at least {minimum:g}{upper}.')
            continue
        values[field] = number

    if errors:
        return None, errors
    if db.query(Student.id).filter(func.lower(Student.student_id) == student_id.lower()).first():
        return None, ['student_id already exists.']
    if email and db.query(Student.id).filter(func.lower(Student.email) == email).first():
        return None, ['email already exists.']
    return values, []


def import_student_records(
    db: Session,
    *,
    user_id: int,
    file_name: str,
    content: bytes,
    data_origin: str,
    source_description: str,
    source_verified: bool,
) -> dict[str, Any]:
    if data_origin not in ALLOWED_ORIGINS:
        raise StudentRecordImportError('Choose either DEMO DATA or INSTITUTIONAL IMPORT as the CSV source.')
    source_description = source_description.strip()
    if data_origin == 'INSTITUTIONAL IMPORT' and (not source_verified or len(source_description) < 3):
        raise StudentRecordImportError('Institutional imports require a source description and explicit verification.')
    if len(source_description) > 200:
        raise StudentRecordImportError('The source description must be at most 200 characters.')
    if not content:
        raise StudentRecordImportError('The CSV file is empty.')
    if len(content) > MAX_FILE_BYTES:
        raise StudentRecordImportError('The CSV file must be 10 MB or smaller.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise StudentRecordImportError('The CSV file must use UTF-8 encoding.') from exc

    checksum = hashlib.sha256(content).hexdigest()
    existing_batch = db.query(AcademicDataImportBatch).filter(
        AcademicDataImportBatch.dataset_type == DATASET_TYPE,
        AcademicDataImportBatch.file_checksum == checksum,
    ).first()
    if existing_batch is not None:
        return {
            'imported': existing_batch.imported_rows,
            'rejected': existing_batch.rejected_rows,
            'duplicates': existing_batch.duplicate_rows,
            'batch_id': existing_batch.id,
            'duplicate_import': True,
            'errors': existing_batch.validation_errors or [],
            'source_provenance': existing_batch.source_provenance or {},
        }

    try:
        reader = csv.DictReader(io.StringIO(text, newline=''))
        if not reader.fieldnames:
            raise StudentRecordImportError('The CSV file must include a header row.')
        headers = [_normalise_header(header or '') for header in reader.fieldnames]
        if len(headers) != len(set(headers)):
            raise StudentRecordImportError('The CSV file contains duplicate column names.')
        missing = {'student_id', 'full_name'} - set(headers)
        unknown = set(headers) - _CSV_FIELDS
        if missing:
            raise StudentRecordImportError(f'Missing required columns: {", ".join(sorted(missing))}.')
        if unknown:
            raise StudentRecordImportError(f'Unsupported columns: {", ".join(sorted(unknown))}.')

        rows: list[tuple[int, dict[str, Any], list[str]]] = []
        for row_number, raw_row in enumerate(reader, start=2):
            if row_number > MAX_ROWS + 1:
                raise StudentRecordImportError(f'The CSV file cannot contain more than {MAX_ROWS} student rows.')
            extra_values = raw_row.pop(None, None)
            row = {
                headers[index]: value
                for index, value in enumerate(raw_row.values())
                if index < len(headers)
            }
            if extra_values:
                errors = ['The row has more values than the header.']
                rows.append((row_number, row, errors))
            else:
                rows.append((row_number, row, []))
    except csv.Error as exc:
        raise StudentRecordImportError('The CSV file could not be parsed.') from exc

    source_name = Path(file_name).name[:255] or 'student-records.csv'
    batch = AcademicDataImportBatch(
        dataset_type=DATASET_TYPE,
        source_filename=source_name,
        file_checksum=checksum,
        imported_by_user_id=user_id,
        imported_rows=0,
        rejected_rows=0,
        duplicate_rows=0,
        validation_errors=[],
        source_provenance={
            'origin': data_origin,
            'source_description': source_description or 'Explicitly selected DEMO DATA import',
            'source_verified': source_verified,
        },
    )
    db.add(batch)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise StudentRecordConflict('This CSV import was submitted concurrently; refresh the directory and retry.') from exc

    imported = rejected = duplicates = 0
    seen_student_ids: set[str] = set()
    seen_emails: set[str] = set()
    reported_errors: list[dict[str, Any]] = []
    for row_number, row, row_errors in rows:
        values: dict[str, Any] | None = None
        if not row_errors:
            values, row_errors = _student_values(row, db)
        student_id = str(row.get('student_id') or '').strip()
        email = str(row.get('email') or '').strip().lower()
        if not row_errors:
            folded_id = student_id.casefold()
            folded_email = email.casefold()
            if folded_id in seen_student_ids:
                row_errors = ['student_id is duplicated in this file.']
            elif email and folded_email in seen_emails:
                row_errors = ['email is duplicated in this file.']
            else:
                seen_student_ids.add(folded_id)
                if email:
                    seen_emails.add(folded_email)

        student: Student | None = None
        status = 'imported'
        if row_errors:
            status = 'duplicate' if any('already exists' in error or 'duplicated' in error for error in row_errors) else 'rejected'
            if status == 'duplicate':
                duplicates += 1
            else:
                rejected += 1
            if len(reported_errors) < 1000:
                reported_errors.append({'row': row_number, 'student_id': student_id or None, 'errors': row_errors})
        elif values is not None:
            student = Student(**values, data_origin=data_origin)
            db.add(student)
            try:
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise StudentRecordConflict('A student ID or email was added by another request; no rows from this import were saved.') from exc
            imported += 1

        row_content = json.dumps(row, sort_keys=True, ensure_ascii=True, default=str)
        row_checksum = hashlib.sha256(f'{checksum}:{row_number}:{row_content}'.encode('utf-8')).hexdigest()
        db.add(AcademicDataImportRow(
            batch_id=batch.id,
            dataset_type=DATASET_TYPE,
            row_checksum=row_checksum,
            row_number=row_number,
            status=status,
            student_id=student.id if student else None,
            errors=row_errors,
        ))

    batch.imported_rows = imported
    batch.rejected_rows = rejected
    batch.duplicate_rows = duplicates
    batch.validation_errors = reported_errors
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise StudentRecordConflict('A student ID or email was added by another request; no rows from this import were saved.') from exc
    return {
        'imported': imported,
        'rejected': rejected,
        'duplicates': duplicates,
        'batch_id': batch.id,
        'duplicate_import': False,
        'errors': reported_errors,
        'source_provenance': batch.source_provenance or {},
    }
