
import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import (
    AcademicDataImportBatch,
    AcademicDataImportRow,
    InternalAssessmentMark,
    Student,
    Subject,
)
from backend.app.services.academic_imports import ImportValidationError


DATASET_TYPE = "student_cohort_internal"

BRANCH_SUBJECTS = {
    "AI&ML": {
        "cn": "CN",
        "evs": "EVS",
        "ir": "IR",
        "research_methodology": "RM",
        "software_engineering": "SE",
        "toc": "TOC",
    },
    "CSE": {
        "ai": "AI",
        "evs": "EVS",
        "network": "CN",
        "research_methodology": "RM",
        "software_engineering": "SE",
        "toc": "TOC",
    },
    "Data Science": {
        "cn": "CN",
        "computer_vision": "CV",
        "evs": "EVS",
        "research_methodology": "RM",
        "software_engineering": "SE",
        "toc": "TOC",
    },
}

NEW_SUBJECTS = {
    "AI": "Artificial Intelligence",
    "CV": "Computer Vision",
    "IR": "Information Retrieval",
}

REQUIRED_HEADERS = {
    "student_id",
    "usn",
    "name",
    "department",
    "course",
    "branch",
    "semester",
    "previous_cgpa",
    "current_cgpa",
}


def _number(value: str, field: str, row_number: int,
            minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"Row {row_number}: {field} must be a number."
        )

    if not minimum <= number <= maximum:
        raise ValueError(
            f"Row {row_number}: {field} must be between "
            f"{minimum} and {maximum}."
        )

    return number


def import_student_cohort(
    db: Session,
    *,
    user_id: int,
    filename: str,
    content: bytes,
) -> dict[str, Any]:

    if not filename.lower().endswith(".csv"):
        raise ImportValidationError("Only CSV files are supported.")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportValidationError(
            "CSV must use UTF-8 encoding."
        ) from exc

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise ImportValidationError("CSV file is empty.")

    headers = [h.strip().lower() for h in reader.fieldnames]

    if len(headers) != len(set(headers)):
        raise ImportValidationError("Duplicate CSV headers found.")

    missing = sorted(REQUIRED_HEADERS - set(headers))
    if missing:
        raise ImportValidationError(
            f"Missing required columns: {', '.join(missing)}."
        )

    rows = [
        {
            str(key).strip().lower(): (value or "").strip()
            for key, value in row.items()
            if key is not None
        }
        for row in reader
    ]

    if not rows:
        raise ImportValidationError("CSV contains no student rows.")

    if len(rows) > 20000:
        raise ImportValidationError("CSV exceeds the 20,000-row limit.")

    checksum = hashlib.sha256(content).hexdigest()

    previous_batch = db.query(AcademicDataImportBatch).filter_by(
        dataset_type=DATASET_TYPE,
        file_checksum=checksum,
    ).first()

    if previous_batch:
        return {
            "batch_id": previous_batch.id,
            "dataset_type": DATASET_TYPE,
            "source_filename": previous_batch.source_filename,
            "imported_rows": 0,
            "rejected_rows": 0,
            "duplicate_rows": len(rows),
            "duplicate_import": True,
        }

    existing_ids = {
        value.casefold()
        for (value,) in db.query(Student.student_id).all()
        if value
    }
    existing_usns = {
        value.casefold()
        for (value,) in db.query(Student.usn).all()
        if value
    }

    seen_ids = set()
    seen_usns = set()
    validated = []
    errors = []

    for index, row in enumerate(rows, start=2):
        try:
            student_id = row.get("student_id", "").strip()
            usn = row.get("usn", "").strip()
            name = row.get("name", "").strip()
            branch = row.get("branch", "").strip()

            if not all((student_id, usn, name, branch)):
                raise ValueError(
                    f"Row {index}: student_id, usn, name and branch "
                    "are required."
                )

            if branch not in BRANCH_SUBJECTS:
                raise ValueError(
                    f"Row {index}: unsupported branch '{branch}'."
                )

            id_key = student_id.casefold()
            usn_key = usn.casefold()

            if id_key in existing_ids or id_key in seen_ids:
                raise ValueError(
                    f"Row {index}: duplicate student_id '{student_id}'."
                )

            if usn_key in existing_usns or usn_key in seen_usns:
                raise ValueError(
                    f"Row {index}: duplicate USN '{usn}'."
                )

            previous_cgpa = _number(
                row.get("previous_cgpa", ""),
                "previous_cgpa", index, 0, 10,
            )
            current_cgpa = _number(
                row.get("current_cgpa", ""),
                "current_cgpa", index, 0, 10,
            )

            marks = {}

            for subject, code in BRANCH_SUBJECTS[branch].items():
                for stage in (1, 2):
                    field = (
                        f"internal_{stage}_{subject}_marks_out_of_25"
                    )
                    raw = row.get(field, "").strip()

                    marks[(code, stage)] = (
                        _number(raw, field, index, 0, 25)
                        if raw else None
                    )

            validated.append({
                "row": row,
                "student_id": student_id,
                "usn": usn,
                "name": name,
                "branch": branch,
                "previous_cgpa": previous_cgpa,
                "current_cgpa": current_cgpa,
                "marks": marks,
                "row_number": index,
            })

            seen_ids.add(id_key)
            seen_usns.add(usn_key)

        except ValueError as exc:
            errors.append({
                "row": index,
                "errors": [str(exc)],
            })

    if errors:
        raise ImportValidationError(
            f"CSV validation failed with {len(errors)} error(s). "
            "No records were imported.",
            errors[:100],
        )

    required_codes = {
        code
        for item in validated
        for subjects in [BRANCH_SUBJECTS[item["branch"]]]
        for code in subjects.values()
    }

    subjects = {
        subject.code: subject
        for subject in db.query(Subject).all()
    }

    missing_existing = required_codes - set(subjects) - set(NEW_SUBJECTS)

    if missing_existing:
        raise ImportValidationError(
            "Required existing subjects are missing: "
            + ", ".join(sorted(missing_existing))
        )

    batch = None

    try:
        batch = AcademicDataImportBatch(
            dataset_type=DATASET_TYPE,
            source_filename=Path(filename).name[:255],
            file_checksum=checksum,
            imported_by_user_id=user_id,
            imported_rows=0,
            rejected_rows=0,
            duplicate_rows=0,
            validation_errors=[],
            source_provenance={
                "origin": "SYNTHETIC DEMO - NOT ACTUAL COLLEGE MARKS",
                "description": "150-student, three-branch, fifth-semester cohort",
                "internal_marks_scale": "0-25",
                "blank_marks": "NULL; not interpreted as zero",
                "risk_model_features_imported": False,
            },
        )

        db.add(batch)
        db.flush()

        created_subjects = []
        for code, name in NEW_SUBJECTS.items():
            if code in required_codes and code not in subjects:
                subject = Subject(
                    code=code,
                    name=name,
                    department=None,
                )
                db.add(subject)
                subjects[code] = subject
                created_subjects.append(code)

        db.flush()

        imported = 0

        for item in validated:
            row = item["row"]

            student = Student(
                student_id=item["student_id"],
                usn=item["usn"],
                full_name=item["name"],
                department=row["department"],
                program=row["course"],
                branch=item["branch"],
                semester=row["semester"],
                previous_cgpa=item["previous_cgpa"],
                current_cgpa=item["current_cgpa"],
                data_origin="SYNTHETIC DEMO",
                course_id=None,
            )

            db.add(student)
            db.flush()

            row_checksum = hashlib.sha256(
                json.dumps(
                    row,
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()

            db.add(AcademicDataImportRow(
                batch_id=batch.id,
                dataset_type=DATASET_TYPE,
                row_checksum=row_checksum,
                row_number=item["row_number"],
                status='imported',
                student_id=student.id,
                academic_record_id=None,
                errors=[],
            ))

            for (code, stage), value in item["marks"].items():
                db.add(InternalAssessmentMark(
                    student_id=student.id,
                    subject_id=subjects[code].id,
                    assessment_stage=f"INTERNAL_{stage}",
                    marks=value,
                    import_batch_id=batch.id,
                ))

            imported += 1

        batch.imported_rows = imported
        db.flush()

        return {
            "batch_id": batch.id,
            "dataset_type": DATASET_TYPE,
            "source_filename": batch.source_filename,
            "imported_rows": imported,
            "rejected_rows": 0,
            "duplicate_rows": 0,
            "duplicate_import": False,
            "internal_mark_records": len(validated) * 12,
            "created_subjects": sorted(created_subjects),
        }

    except Exception:
        db.rollback()
        raise



