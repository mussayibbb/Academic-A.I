import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ai.risk.academic_dataset import MIN_COMPLETE_ALIGNED_OBSERVATIONS, MIN_DISTINCT_ACADEMIC_PERIODS, MIN_UNIQUE_STUDENTS_PER_CLASS
from ai.risk.features import FEATURE_CONTRACT_VERSION, FEATURES
from backend.app.models import (
    AcademicDataImportBatch,
    AcademicDataImportRow,
    AcademicRiskOutcome,
    Phase3PolicyApproval,
    Student,
    StudentPeriodFeatureSnapshot,
    StudentSubjectAcademicRecord,
    StudentSubjectAcademicRecordHistory,
    Subject,
)

PHASE3_DATASET_VERSION = 'student-subject-period-v1'
PHASE3_FEATURE_CONTRACT_VERSION = 'academic-subject-observations-v1'
FEATURE_SNAPSHOT_DATASET_VERSION = 'student-period-production-features-v1'
FEATURE_SNAPSHOT_AGGREGATION_STRATEGY = 'production_feature_snapshot'
PHASE3_TARGET_DEFINITION = 'Independent AcademicRiskOutcome.risk_level for the same student and academic period; target scope is global student-period.'
PHASE3_AGGREGATION_STRATEGY: str | None = None
APPROVED_AGGREGATION_STRATEGY: str | None = None
APPROVED_CANDIDATE_FEATURE_CONTRACT: tuple[str, ...] | None = None
ACADEMIC_PERIOD_PATTERN = re.compile(r'^\d{4}-S[12]$')
OUTCOME_CLASSES = ('HIGH', 'MEDIUM', 'LOW')
OUTCOME_SOURCE_DENYLIST = (
    'model prediction', 'model-generated', 'model generated', 'shap', 'inference',
    'synthetic', 'ai-generated label', 'ai generated label', 'automated prediction',
    'classifier output', 'derived from marks',
    'demo', 'sample', 'test data', 'test-only', 'test only', 'test fixture',
    'isolated fixture', 'fixture data',
)
FIELD_LIMITS = {
    'fa1': (0.0, 25.0),
    'fa2': (0.0, 25.0),
    'assignment_mark': (0.0, 25.0),
    'attendance_rate': (0.0, 100.0),
}
FIELD_DATES = {
    'fa1': 'fa1_observed_at',
    'fa2': 'fa2_observed_at',
    'assignment_mark': 'assignment_observed_at',
    'attendance_rate': 'attendance_observed_at',
}
DATASET_FIELDS = (
    'dataset_id', 'dataset_version', 'dataset_fingerprint',
    'student_identifier', 'department', 'section', 'academic_period', 'subject_code', 'fa1', 'fa1_observed_at',
    'fa2', 'fa2_observed_at', 'assignment_mark', 'assignment_observed_at',
    'attendance_rate', 'attendance_observed_at', 'risk_target', 'risk_observed_at',
    'independent_outcome_id', 'outcome_source', 'outcome_batch_id', 'outcome_filename', 'outcome_checksum',
    'subject_or_aggregation', 'fa1_mean', 'fa2_mean', 'assignment_mean', 'attendance_mean',
    'source_record_ids', 'source_subjects', 'source_record_count', 'source_batch_ids', 'source_filenames', 'source_checksums',
    'temporally_aligned', 'candidate_eligible', 'source_batch_id', 'source_filename',
    'source_checksum', 'source_record_id', 'revision_count', 'exclusion_reasons',
    'feature_observed_at', 'source_row_number', 'source_row_checksum',
    *FEATURES,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _utc_instant(value: datetime | None, *, normalized_naive: bool = False) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        if not normalized_naive:
            return None
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _checked_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    instant = _utc_instant(value)
    if instant is None:
        raise ValueError('The Phase 3 evaluation cutoff must be a valid datetime.')
    return instant


def _is_prohibited_outcome_source(source: str) -> bool:
    normalized = re.sub(r'[^a-z0-9]+', ' ', source.casefold()).strip()
    return any(marker in normalized for marker in OUTCOME_SOURCE_DENYLIST)


def temporal_split(periods: list[str]) -> dict[str, list[str]]:
    ordered = sorted(set(periods))
    if any(not ACADEMIC_PERIOD_PATTERN.fullmatch(period) for period in ordered):
        raise ValueError('Temporal splitting requires academic periods in YYYY-S1 or YYYY-S2 format.')
    if len(ordered) < 3:
        raise ValueError('Insufficient distinct academic periods for temporal validation; at least 3 are required.')
    return {
        'training_periods': ordered[:-2],
        'validation_periods': ordered[-2:-1],
        'test_periods': ordered[-1:],
    }


def _rejected_import_rows(db: Session, batches: dict[int, AcademicDataImportBatch]) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    for row in db.query(AcademicDataImportRow).filter(AcademicDataImportRow.status == 'rejected').order_by(AcademicDataImportRow.id).all():
        batch = batches.get(row.batch_id)
        detail = next((item for item in (batch.validation_errors if batch else []) if item.get('row') == row.row_number), {})
        rejected.append({
            'row_number': row.row_number,
            'usn': detail.get('student_id'),
            'subject_code': detail.get('subject_code'),
            'academic_period': detail.get('academic_period'),
            'reason': list(row.errors or detail.get('errors') or ['Rejected during import validation.']),
            'source_filename': batch.source_filename if batch else None,
            'source_batch_id': batch.id if batch else row.batch_id,
        })
    return rejected


def _feature_snapshot_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None
    return _utc_instant(parsed)


def _build_feature_snapshot_dataset(
    db: Session,
    *,
    now: datetime | None,
    feature_contract: list[str] | None,
    approved_aggregation: str | None,
    approved_contract: tuple[str, ...] | None,
) -> dict[str, Any]:
    if feature_contract is not None and feature_contract != FEATURES:
        raise ValueError('Production feature snapshots require the exact ordered live production feature contract.')
    contract = list(FEATURES)
    aggregation_approved = approved_aggregation == FEATURE_SNAPSHOT_AGGREGATION_STRATEGY
    contract_approved = bool(approved_contract and tuple(contract) == approved_contract)
    checked_at = _checked_at(now)

    snapshots = db.query(StudentPeriodFeatureSnapshot).order_by(
        StudentPeriodFeatureSnapshot.student_id,
        StudentPeriodFeatureSnapshot.academic_period,
    ).all()
    students = {item.id: item for item in db.query(Student).all()}
    outcomes = db.query(AcademicRiskOutcome).order_by(
        AcademicRiskOutcome.student_id,
        AcademicRiskOutcome.academic_period,
    ).all()
    outcome_groups: dict[tuple[int, str], list[AcademicRiskOutcome]] = defaultdict(list)
    for outcome in outcomes:
        outcome_groups[(outcome.student_id, outcome.academic_period)].append(outcome)
    outcome_dates = {
        outcome.id: _utc_instant(outcome.observed_at, normalized_naive=outcome.temporal_evidence_verified)
        for outcome in outcomes
    }
    batches = {batch.id: batch for batch in db.query(AcademicDataImportBatch).all()}
    import_rows = {
        (row.batch_id, row.row_number): row
        for row in db.query(AcademicDataImportRow).filter(
            AcademicDataImportRow.dataset_type == 'production_feature_snapshots',
        ).all()
    }

    snapshot_sources = [{
        'student_identifier': students[item.student_id].student_id if item.student_id in students else None,
        'academic_period': item.academic_period,
        'feature_values': item.feature_values,
        'feature_observed_at': item.feature_observed_at,
        'source_row_number': item.source_row_number,
        'source_row_checksum': item.source_row_checksum,
        'source_filename': item.source_filename,
        'source_checksum': item.source_checksum,
        'recorded_by_user_id': item.recorded_by_user_id,
        'batch_checksum': batches[item.import_batch_id].file_checksum if item.import_batch_id in batches else None,
    } for item in snapshots]
    outcome_sources = [{
        'student_identifier': students[item.student_id].student_id if item.student_id in students else None,
        'academic_period': item.academic_period,
        'risk_level': item.risk_level,
        'observed_at': _iso(outcome_dates.get(item.id) or item.observed_at),
        'temporal_evidence_verified': item.temporal_evidence_verified,
        'outcome_source': item.outcome_source,
        'recorded_by_user_id': item.recorded_by_user_id,
        'source_batch_id': item.import_batch_id,
        'source_filename': batches[item.import_batch_id].source_filename if item.import_batch_id in batches else None,
        'source_checksum': batches[item.import_batch_id].file_checksum if item.import_batch_id in batches else None,
    } for item in outcomes]
    import_sources = [{
        'batch_checksum': batches[row.batch_id].file_checksum if row.batch_id in batches else None,
        'row_checksum': row.row_checksum,
        'row_number': row.row_number,
        'status': row.status,
        'errors': row.errors or [],
    } for row in db.query(AcademicDataImportRow).filter(
        AcademicDataImportRow.dataset_type.in_(['production_feature_snapshots', 'risk_outcomes']),
    ).order_by(AcademicDataImportRow.id).all()]
    fingerprint_input = json.dumps({
        'dataset_version': FEATURE_SNAPSHOT_DATASET_VERSION,
        'feature_contract_version': FEATURE_CONTRACT_VERSION,
        'feature_contract': contract,
        'target_definition': PHASE3_TARGET_DEFINITION,
        'snapshots': snapshot_sources,
        'outcomes': outcome_sources,
        'import_rows': import_sources,
    }, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    fingerprint = hashlib.sha256(fingerprint_input).hexdigest()

    rejected_rows = _rejected_import_rows(db, batches)
    conflicting_rows = db.query(AcademicDataImportRow).filter(
        AcademicDataImportRow.dataset_type == 'production_feature_snapshots',
        AcademicDataImportRow.status == 'rejected',
    ).all()
    conflict_count = sum(
        any('conflicting feature snapshot' in str(error).casefold() for error in (row.errors or []))
        for row in conflicting_rows
    )
    valid_outcomes = [
        outcome for outcome in outcomes
        if outcome.risk_level in OUTCOME_CLASSES
        and bool(outcome.outcome_source and outcome.outcome_source.strip())
        and not _is_prohibited_outcome_source(outcome.outcome_source)
        and not _is_prohibited_outcome_source(
            batches[outcome.import_batch_id].source_filename
            if outcome.import_batch_id in batches else ''
        )
        and outcome_dates.get(outcome.id) is not None
        and outcome_dates[outcome.id] <= checked_at
    ]
    counts: dict[str, int] = {
        'students': len(students),
        'distinct_students_with_records': len({item.student_id for item in snapshots}),
        'academic_records': len(snapshots),
        'feature_snapshots': len(snapshots),
        'complete_observations': 0,
        'temporally_aligned_observations': 0,
        'temporally_invalid_observations': 0,
        'missing_outcomes': 0,
        'invalid_outcomes': len(outcomes) - len(valid_outcomes),
        'future_observations': 0,
        'unverifiable_timestamps': sum(value is None for value in outcome_dates.values()),
        'unverifiable_feature_timestamps': 0,
        'records_without_observation_dates': 0,
        'invalid_mark_records': 0,
        'invalid_academic_periods': 0,
        'duplicate_outcome_groups': sum(len(group) > 1 for group in outcome_groups.values()),
        'conflicting_records': conflict_count,
        'unknown_students': sum(item.student_id not in students for item in snapshots),
        'independent_outcomes': len(valid_outcomes),
        'complete_provenance_records': 0,
        'complete_outcome_provenance': 0,
        'candidate_training_samples': 0,
        'prohibited_source_records': 0,
    }
    counts['complete_outcome_provenance'] = sum(
        bool((batches.get(outcome.import_batch_id) and batches[outcome.import_batch_id].source_filename
              and batches[outcome.import_batch_id].file_checksum) or outcome.recorded_by_user_id)
        for outcome in valid_outcomes
    )
    class_students: dict[str, set[str]] = {level: set() for level in OUTCOME_CLASSES}
    periods: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    invalid_feature_ranges = {
        'attendance': (0.0, 100.0),
        'assessment': (0.0, 100.0),
        'previous_gpa': (0.0, 4.0),
        'current_gpa': (0.0, 4.0),
        'assignments_completed': (0.0, None),
        'activity_per_week': (0.0, None),
        'missing_submissions': (0.0, None),
    }

    for snapshot in snapshots:
        student = students.get(snapshot.student_id)
        period = snapshot.academic_period
        outcome_group = outcome_groups.get((snapshot.student_id, period), [])
        outcome = outcome_group[0] if len(outcome_group) == 1 else None
        period_valid = bool(ACADEMIC_PERIOD_PATTERN.fullmatch(period))
        if not period_valid:
            counts['invalid_academic_periods'] += 1
        feature_values = snapshot.feature_values if isinstance(snapshot.feature_values, dict) else {}
        raw_dates = snapshot.feature_observed_at if isinstance(snapshot.feature_observed_at, dict) else {}
        missing_features = [name for name in contract if name not in feature_values]
        dates = {name: _feature_snapshot_datetime(raw_dates.get(name)) for name in contract}
        missing_dates = [name for name in contract if dates[name] is None]
        unverifiable_dates = [
            name for name in contract
            if raw_dates.get(name) not in (None, '') and dates[name] is None
        ]
        counts['unverifiable_feature_timestamps'] += len(unverifiable_dates)
        invalid_values = []
        normalized_values: dict[str, float] = {}
        for name in contract:
            value = feature_values.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                invalid_values.append(name)
                continue
            try:
                numeric = float(value)
            except (OverflowError, ValueError):
                invalid_values.append(name)
                continue
            if not math.isfinite(numeric):
                invalid_values.append(name)
                continue
            minimum, maximum = invalid_feature_ranges.get(name, (None, None))
            if (minimum is not None and numeric < minimum) or (maximum is not None and numeric > maximum):
                invalid_values.append(name)
                continue
            normalized_values[name] = numeric
        if missing_features or invalid_values or len(feature_values) != len(contract):
            counts['invalid_mark_records'] += 1
        complete = not missing_features and not invalid_values and len(normalized_values) == len(contract)
        if complete:
            counts['complete_observations'] += 1
        if missing_dates:
            counts['records_without_observation_dates'] += 1
        future_features = [name for name, observed in dates.items() if observed and observed > checked_at]
        outcome_at = outcome_dates.get(outcome.id) if outcome else None
        if outcome and outcome_at and outcome_at > checked_at:
            counts['future_observations'] += 1
        elif future_features:
            counts['future_observations'] += 1
        later_features = [
            name for name, observed in dates.items()
            if observed and outcome_at and observed > outcome_at
        ]
        outcome_source = outcome.outcome_source.strip() if outcome and outcome.outcome_source else ''
        outcome_batch = batches.get(outcome.import_batch_id) if outcome else None
        outcome_valid = bool(
            outcome and outcome.risk_level in OUTCOME_CLASSES and outcome_source
            and not _is_prohibited_outcome_source(outcome_source)
            and not _is_prohibited_outcome_source(outcome_batch.source_filename if outcome_batch else '')
            and outcome_at is not None
            and outcome_at <= checked_at
        )
        if outcome and outcome_at is None:
            outcome_valid = False
        if outcome is None:
            counts['missing_outcomes'] += 1
        if len(outcome_group) > 1:
            outcome_valid = False
        if future_features or (outcome_at and outcome_at > checked_at):
            counts['temporally_invalid_observations'] += 1
        if later_features:
            counts['temporally_invalid_observations'] += 1

        batch = batches.get(snapshot.import_batch_id)
        import_row = import_rows.get((snapshot.import_batch_id, snapshot.source_row_number))
        source_allowed = not _is_prohibited_outcome_source(snapshot.source_filename)
        if not source_allowed:
            counts['prohibited_source_records'] += 1
        provenance_complete = bool(
            batch and batch.dataset_type == 'production_feature_snapshots'
            and batch.source_filename and batch.file_checksum
            and batch.source_filename == snapshot.source_filename
            and batch.file_checksum == snapshot.source_checksum
            and batch.imported_by_user_id == snapshot.recorded_by_user_id
            and import_row and import_row.status == 'imported'
            and import_row.student_id == snapshot.student_id
            and import_row.row_checksum == snapshot.source_row_checksum
        )
        if provenance_complete:
            counts['complete_provenance_records'] += 1

        aligned = bool(
            student and period_valid and complete and not missing_dates
            and not future_features and not later_features and outcome_valid and outcome_at is not None
            and len(outcome_group) == 1
        )
        if aligned:
            counts['temporally_aligned_observations'] += 1
            class_students[outcome.risk_level].add(student.student_id)

        visible_values = {
            name: normalized_values.get(name)
            if dates[name] is not None and dates[name] <= checked_at
            and not (outcome_at and dates[name] > outcome_at)
            else None
            for name in contract
        }
        if student and period_valid:
            coverage = periods.setdefault(period, {
                'academic_period': period,
                'record_count': 0,
                'student_identifiers': set(),
                'aligned_count': 0,
                'risk_class_students': {level: set() for level in OUTCOME_CLASSES},
            })
            coverage['record_count'] += 1
            coverage['student_identifiers'].add(student.student_id)
            coverage['aligned_count'] += aligned
            if aligned:
                coverage['risk_class_students'][outcome.risk_level].add(student.student_id)

        row = {
            'dataset_id': f'p3-{fingerprint[:16]}',
            'dataset_version': FEATURE_SNAPSHOT_DATASET_VERSION,
            'dataset_fingerprint': fingerprint,
            'student_identifier': student.student_id if student else None,
            'department': student.department if student else None,
            'section': student.section if student else None,
            'academic_period': period,
            'subject_code': None,
            'subject_or_aggregation': FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
            **visible_values,
            'feature_observed_at': {
                name: dates[name].isoformat() if dates[name] else None
                for name in contract
            },
            'risk_target': outcome.risk_level if aligned else None,
            'risk_observed_at': _iso(outcome_at) if outcome and aligned else None,
            'independent_outcome_id': outcome.id if outcome and aligned else None,
            'outcome_source': outcome.outcome_source if outcome and aligned else None,
            'outcome_batch_id': outcome.import_batch_id if outcome and aligned else None,
            'outcome_filename': batches[outcome.import_batch_id].source_filename
            if outcome and outcome.import_batch_id in batches and aligned else None,
            'outcome_checksum': batches[outcome.import_batch_id].file_checksum
            if outcome and outcome.import_batch_id in batches and aligned else None,
            'source_record_id': snapshot.id,
            'source_record_ids': [snapshot.id],
            'source_record_count': 1,
            'source_subjects': [],
            'source_batch_id': snapshot.import_batch_id,
            'source_batch_ids': [snapshot.import_batch_id],
            'source_filename': snapshot.source_filename,
            'source_filenames': [snapshot.source_filename],
            'source_checksum': snapshot.source_checksum,
            'source_checksums': [snapshot.source_checksum],
            'source_row_number': snapshot.source_row_number,
            'source_row_checksum': snapshot.source_row_checksum,
            'revision_count': 0,
            'temporally_aligned': aligned,
            'candidate_eligible': bool(aligned and provenance_complete and source_allowed and aggregation_approved and contract_approved),
            'exclusion_reasons': [
                *(['unknown_student'] if not student else []),
                *(['invalid_academic_period'] if not period_valid else []),
                *([f'missing_feature:{name}' for name in missing_features]),
                *([f'invalid_feature:{name}' for name in invalid_values]),
                *([f'missing_observation_date:{name}' for name in missing_dates]),
                *([f'unverifiable_observation_timezone:{name}' for name in unverifiable_dates]),
                *([f'future_observation:{name}' for name in future_features]),
                *([f'{name}_observed_after_outcome' for name in later_features]),
                *(['missing_independent_outcome'] if outcome is None else []),
                *(['duplicate_period_outcomes'] if len(outcome_group) > 1 else []),
                *(['invalid_or_model_derived_outcome'] if outcome and not outcome_valid else []),
                *(['missing_record_provenance'] if not provenance_complete else []),
                *(['prohibited_test_or_demo_source'] if not source_allowed else []),
            ],
        }
        if aligned and provenance_complete and source_allowed and aggregation_approved and contract_approved:
            candidate_rows.append(row)
        rows.append(row)

    counts['candidate_training_samples'] = len(candidate_rows)
    academic_periods = sorted(periods)
    class_coverage = {
        level: {
            'aligned_records': sum(row['risk_target'] == level for row in candidate_rows),
            'distinct_students': len(class_students[level]),
        }
        for level in OUTCOME_CLASSES
    }
    blockers: list[str] = []
    if counts['independent_outcomes'] == 0:
        blockers.append('0 independent outcomes available.')
    if len(candidate_rows) < MIN_COMPLETE_ALIGNED_OBSERVATIONS:
        blockers.append(f'Only {len(candidate_rows)} complete temporally aligned feature snapshots; at least {MIN_COMPLETE_ALIGNED_OBSERVATIONS} are required.')
    if len(academic_periods) < MIN_DISTINCT_ACADEMIC_PERIODS:
        blockers.append(f'Only {len(academic_periods)} academic period(s) available; at least {MIN_DISTINCT_ACADEMIC_PERIODS} distinct periods are required for temporal validation.')
    for level in OUTCOME_CLASSES:
        if len(class_students[level]) < MIN_UNIQUE_STUDENTS_PER_CLASS:
            blockers.append(f'Insufficient {level} outcomes: {len(class_students[level])} distinct aligned students; at least {MIN_UNIQUE_STUDENTS_PER_CLASS} are required.')
    if counts['invalid_mark_records']:
        blockers.append(f'{counts["invalid_mark_records"]} feature snapshot(s) contain missing, non-finite, or out-of-range production features.')
    if counts['invalid_academic_periods']:
        blockers.append(f'{counts["invalid_academic_periods"]} feature snapshot(s) have invalid academic periods.')
    if counts['invalid_outcomes']:
        blockers.append(f'{counts["invalid_outcomes"]} outcome(s) have invalid labels, dates, or prohibited provenance.')
    if counts['temporally_invalid_observations']:
        blockers.append(f'{counts["temporally_invalid_observations"]} feature snapshot(s) contain future-dated or post-outcome predictor evidence.')
    if counts['future_observations']:
        blockers.append(f'{counts["future_observations"]} feature snapshot(s) or outcomes are dated in the future.')
    if counts['records_without_observation_dates']:
        blockers.append(f'{counts["records_without_observation_dates"]} feature snapshot(s) lack one or more source observation dates.')
    if counts['unverifiable_timestamps'] or counts['unverifiable_feature_timestamps']:
        blockers.append(
            f'{counts["unverifiable_timestamps"]} outcome timestamp(s) and '
            f'{counts["unverifiable_feature_timestamps"]} feature timestamp(s) are missing or unverifiable.'
        )
    if counts['duplicate_outcome_groups']:
        blockers.append('Duplicate student/period outcomes must be resolved.')
    if counts['conflicting_records']:
        blockers.append(f'{counts["conflicting_records"]} conflicting feature snapshot import row(s) require a governed source correction.')
    if counts['unknown_students']:
        blockers.append(f'{counts["unknown_students"]} feature snapshot(s) reference unknown students.')
    if counts['complete_provenance_records'] < len(snapshots):
        blockers.append(f'{len(snapshots) - counts["complete_provenance_records"]} feature snapshot(s) lack complete source/actor provenance.')
    if counts['prohibited_source_records']:
        blockers.append(f'{counts["prohibited_source_records"]} feature snapshot source(s) are explicitly labeled demo, sample, test, or synthetic data.')
    if counts['complete_outcome_provenance'] < counts['independent_outcomes']:
        blockers.append(f'{counts["independent_outcomes"] - counts["complete_outcome_provenance"]} independent outcome(s) lack complete source/actor provenance.')
    if not aggregation_approved:
        blockers.append('The production feature-snapshot methodology has not been explicitly approved.')
    if not contract_approved:
        blockers.append('The exact ordered production feature contract has not been explicitly approved.')

    for coverage in periods.values():
        coverage['student_count'] = len(coverage.pop('student_identifiers'))
        coverage['risk_class_students'] = {
            level: len(coverage['risk_class_students'][level])
            for level in OUTCOME_CLASSES
        }
    readiness = {
        'training_ready': not blockers,
        'blockers': blockers,
        'evidence': {
            'independent_outcomes': counts['independent_outcomes'],
            'aligned_observations': len(candidate_rows),
            'complete_observations': counts['complete_observations'],
            'distinct_academic_periods': academic_periods,
            'distinct_students_with_records': counts['distinct_students_with_records'],
            'students_per_risk_class': {level: len(class_students[level]) for level in OUTCOME_CLASSES},
            'candidate_training_samples': len(candidate_rows),
            'class_coverage': class_coverage,
            'invalid_mark_records': counts['invalid_mark_records'],
            'temporal_violations': counts['temporally_invalid_observations'],
            'duplicate_outcome_groups': counts['duplicate_outcome_groups'],
            'provenance_complete_records': counts['complete_provenance_records'],
            'prohibited_source_records': counts['prohibited_source_records'],
            'aggregation_strategy': FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
            'aggregation_approved': aggregation_approved,
            'feature_contract': contract,
            'feature_contract_approved': contract_approved,
            'dataset_fingerprint': fingerprint,
            'fingerprint_reproducible': True,
        },
        'thresholds': {
            'minimum_complete_aligned_observations': MIN_COMPLETE_ALIGNED_OBSERVATIONS,
            'minimum_distinct_academic_periods': MIN_DISTINCT_ACADEMIC_PERIODS,
            'minimum_students_per_class': MIN_UNIQUE_STUDENTS_PER_CLASS,
        },
    }
    return {
        'dataset_id': f'p3-{fingerprint[:16]}',
        'dataset_version': FEATURE_SNAPSHOT_DATASET_VERSION,
        'dataset_fingerprint': fingerprint,
        'created_at': checked_at.replace(tzinfo=timezone.utc).isoformat(),
        'feature_contract_version': FEATURE_CONTRACT_VERSION,
        'target_definition': PHASE3_TARGET_DEFINITION,
        'aggregation_strategy': FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
        'target_scope': 'global student-period; one point-in-time production feature snapshot per student-period',
        'training_eligible': readiness['training_ready'],
        'counts': counts,
        'coverage': {
            'by_academic_period': periods,
            'by_subject': {},
            'by_period_and_subject': {},
            'by_subgroup': {'department': {}, 'section': {}},
            'by_risk_class': class_coverage,
        },
        'readiness': readiness,
        'rows': rows,
        'candidate_rows': candidate_rows,
        'rejected_rows': rejected_rows,
        'duplicate_import_rows': sum(batch.duplicate_rows for batch in batches.values()),
    }


def build_phase3_dataset(
    db: Session,
    *,
    now: datetime | None = None,
    aggregation_strategy: str | None = None,
    feature_contract: list[str] | None = None,
) -> dict[str, Any]:
    policy = db.query(Phase3PolicyApproval).order_by(Phase3PolicyApproval.id.desc()).first()
    approved_aggregation = APPROVED_AGGREGATION_STRATEGY
    approved_contract = APPROVED_CANDIDATE_FEATURE_CONTRACT
    if policy is not None:
        approved_aggregation = policy.aggregation_strategy
        approved_contract = tuple(policy.feature_contract)
        if aggregation_strategy is None and feature_contract is None:
            aggregation_strategy = policy.aggregation_strategy
            feature_contract = list(policy.feature_contract)
    if aggregation_strategy == FEATURE_SNAPSHOT_AGGREGATION_STRATEGY:
        return _build_feature_snapshot_dataset(
            db,
            now=now,
            feature_contract=feature_contract,
            approved_aggregation=approved_aggregation,
            approved_contract=approved_contract,
        )
    if aggregation_strategy not in {None, 'equal_subject_mean'}:
        raise ValueError('Unsupported aggregation strategy; explicitly select equal_subject_mean, production_feature_snapshot, or leave it unavailable.')
    supported_contract = {'fa1_mean', 'fa2_mean', 'assignment_mean', 'attendance_mean'}
    if feature_contract is not None and (not feature_contract or len(feature_contract) != len(set(feature_contract)) or not set(feature_contract) <= supported_contract):
        raise ValueError('The candidate feature contract must explicitly list unique supported subject aggregate features.')
    aggregation_approved = bool(aggregation_strategy and aggregation_strategy == approved_aggregation)
    contract_approved = bool(feature_contract and approved_contract and tuple(feature_contract) == tuple(approved_contract))
    checked_at = _checked_at(now)
    records = db.query(StudentSubjectAcademicRecord).order_by(
        StudentSubjectAcademicRecord.student_id,
        StudentSubjectAcademicRecord.academic_period,
        StudentSubjectAcademicRecord.subject_id,
    ).all()
    students = {item.id: item for item in db.query(Student).all()}
    subjects = {item.id: item for item in db.query(Subject).all()}
    outcomes = db.query(AcademicRiskOutcome).order_by(AcademicRiskOutcome.student_id, AcademicRiskOutcome.academic_period).all()
    outcome_groups: dict[tuple[int, str], list[AcademicRiskOutcome]] = defaultdict(list)
    for outcome in outcomes:
        outcome_groups[(outcome.student_id, outcome.academic_period)].append(outcome)
    outcome_dates = {
        outcome.id: _utc_instant(outcome.observed_at, normalized_naive=outcome.temporal_evidence_verified)
        for outcome in outcomes
    }
    batches = {batch.id: batch for batch in db.query(AcademicDataImportBatch).all()}
    history_by_record: dict[int, list[StudentSubjectAcademicRecordHistory]] = defaultdict(list)
    for history in db.query(StudentSubjectAcademicRecordHistory).order_by(StudentSubjectAcademicRecordHistory.id).all():
        history_by_record[history.academic_record_id].append(history)
    revisions = {record_id: len(history) for record_id, history in history_by_record.items()}

    source_snapshot = []
    for record in records:
        student = students.get(record.student_id)
        subject = subjects.get(record.subject_id)
        batch = batches.get(record.import_batch_id)
        history_rows = history_by_record.get(record.id, [])
        source_snapshot.append({
            'student_identifier': student.student_id if student else None,
            'subject_code': subject.code if subject else None,
            'academic_period': record.academic_period,
            'values': {field: getattr(record, field) for field in FIELD_LIMITS},
            'observed_at': {field: _iso(getattr(record, field)) for field in FIELD_DATES.values()},
            'updated_by_user_id': record.updated_by_user_id,
            'source_batch_id': record.import_batch_id,
            'source_filename': batch.source_filename if batch else None,
            'source_checksum': batch.file_checksum if batch else None,
            'revision_count': revisions.get(record.id, 0),
            'temporal_evidence_verified': record.temporal_evidence_verified,
            'revision_values': [{
                'change_kind': history.change_kind,
                'import_batch_id': history.import_batch_id,
                'changed_by_user_id': history.changed_by_user_id,
                'values': {field: getattr(history, field) for field in FIELD_LIMITS},
                'observed_at': {field: _iso(getattr(history, field)) for field in FIELD_DATES.values()},
            } for history in history_rows],
        })
    outcome_snapshot = [{
        'student_identifier': students[item.student_id].student_id if item.student_id in students else None,
        'academic_period': item.academic_period,
        'risk_level': item.risk_level,
        'observed_at': _iso(outcome_dates.get(item.id) or item.observed_at),
        'temporal_evidence_verified': item.temporal_evidence_verified,
        'outcome_source': item.outcome_source,
        'recorded_by_user_id': item.recorded_by_user_id,
        'source_batch_id': item.import_batch_id,
        'source_filename': batches[item.import_batch_id].source_filename if item.import_batch_id in batches else None,
        'source_checksum': batches[item.import_batch_id].file_checksum if item.import_batch_id in batches else None,
    } for item in outcomes]
    import_snapshot = [{
        'dataset_type': row.dataset_type,
        'row_checksum': row.row_checksum,
        'status': row.status,
        'errors': row.errors or [],
        'row_number': row.row_number,
        'batch_checksum': batches[row.batch_id].file_checksum if row.batch_id in batches else None,
    } for row in db.query(AcademicDataImportRow).filter(
        AcademicDataImportRow.dataset_type.in_(['subject_records', 'risk_outcomes']),
    ).order_by(AcademicDataImportRow.id).all()]
    fingerprint_input = json.dumps(
        {
            'dataset_version': PHASE3_DATASET_VERSION,
            'feature_contract_version': PHASE3_FEATURE_CONTRACT_VERSION,
            'target_definition': PHASE3_TARGET_DEFINITION,
            'aggregation_strategy': aggregation_strategy,
            'feature_contract': feature_contract,
            'records': source_snapshot,
            'outcomes': outcome_snapshot,
            'import_rows': import_snapshot,
        },
        sort_keys=True, separators=(',', ':'), default=str,
    ).encode('utf-8')
    fingerprint = hashlib.sha256(fingerprint_input).hexdigest()

    counts: dict[str, int] = {
        'students': len(students), 'subjects': len(subjects), 'academic_records': len(records),
        'complete_observations': 0, 'temporally_aligned_observations': 0,
        'temporally_invalid_observations': 0, 'missing_outcomes': 0,
        'invalid_outcomes': 0, 'future_observations': 0,
            'unverifiable_timestamps': sum(value is None for value in outcome_dates.values()),
            'stale_observation_records_180d': 0,
        'records_without_observation_dates': 0, 'revised_records': 0,
        'duplicate_record_groups': 0,
        'duplicate_outcome_groups': sum(len(group) > 1 for group in outcome_groups.values()),
        'conflicting_records': 0, 'unknown_students': 0, 'unknown_subjects': 0,
        'invalid_academic_periods': 0, 'invalid_mark_records': 0,
        'independent_outcomes': 0, 'complete_provenance_records': 0,
        'complete_outcome_provenance': 0,
        'academic_values_with_source_dates': 0,
        'academic_values_missing_source_dates': 0,
    }
    valid_outcomes = [
        outcome for outcome in outcomes
        if outcome.risk_level in OUTCOME_CLASSES
        and bool(outcome.outcome_source and outcome.outcome_source.strip())
        and not _is_prohibited_outcome_source(outcome.outcome_source)
        and not _is_prohibited_outcome_source(
            batches[outcome.import_batch_id].source_filename
            if outcome.import_batch_id in batches else ''
        )
        and outcome_dates.get(outcome.id) is not None
        and outcome_dates[outcome.id] <= checked_at
    ]
    counts['independent_outcomes'] = len(valid_outcomes)
    counts['invalid_outcomes'] = len(outcomes) - len(valid_outcomes)
    counts['complete_outcome_provenance'] = sum(
        bool((batches.get(outcome.import_batch_id) and batches[outcome.import_batch_id].source_filename and batches[outcome.import_batch_id].file_checksum) or outcome.recorded_by_user_id)
        for outcome in valid_outcomes
    )
    periods: dict[str, dict[str, Any]] = {}
    subject_coverage: dict[str, dict[str, Any]] = {}
    period_subject: dict[str, dict[str, Any]] = {}
    subgroup_coverage: dict[str, dict[str, dict[str, Any]]] = {'department': {}, 'section': {}}
    class_students: dict[str, set[str]] = {level: set() for level in OUTCOME_CLASSES}
    class_aligned_records: dict[str, int] = {level: 0 for level in OUTCOME_CLASSES}
    rejected_rows = _rejected_import_rows(db, batches)
    counts['conflicting_records'] = sum(any('conflicting' in str(reason).casefold() for reason in issue['reason']) for issue in rejected_rows)
    known_student_ids = {student.student_id.strip().casefold() for student in students.values()}
    known_subject_codes = {subject.code.strip().casefold() for subject in subjects.values()}
    counts['unknown_students'] += sum(
        (bool(issue['usn']) and issue['usn'].strip().casefold() not in known_student_ids)
        or any('ambiguous student_id' in str(reason).casefold() for reason in issue['reason'])
        for issue in rejected_rows
    )
    counts['unknown_subjects'] += sum(
        bool(issue['subject_code']) and issue['subject_code'].strip().casefold() not in known_subject_codes
        for issue in rejected_rows
    )
    counts['invalid_academic_periods'] += sum(any('academic_period' in str(reason).casefold() for reason in issue['reason']) for issue in rejected_rows)
    counts['invalid_mark_records'] += sum(any(field in str(reason).casefold() and ('between' in str(reason).casefold() or 'range' in str(reason).casefold()) for reason in issue['reason'] for field in FIELD_LIMITS) for issue in rejected_rows)
    rejected_outcomes = db.query(AcademicDataImportRow.id).filter(
        AcademicDataImportRow.dataset_type == 'risk_outcomes',
        AcademicDataImportRow.status == 'rejected',
    ).count()
    counts['invalid_outcomes'] += rejected_outcomes
    duplicate_import_rows = sum(batch.duplicate_rows for batch in batches.values())
    rows: list[dict[str, Any]] = []

    for record in records:
        student = students.get(record.student_id)
        subject = subjects.get(record.subject_id)
        batch = batches.get(record.import_batch_id)
        student_identifier = student.student_id if student else None
        subject_code = subject.code if subject else None
        period = record.academic_period
        outcome_group = outcome_groups.get((record.student_id, period), [])
        outcome = outcome_group[0] if len(outcome_group) == 1 else None
        history_count = revisions.get(record.id, 0)
        if not student:
            counts['unknown_students'] += 1
        if not subject:
            counts['unknown_subjects'] += 1
        period_valid = bool(ACADEMIC_PERIOD_PATTERN.fullmatch(period))
        if not period_valid:
            counts['invalid_academic_periods'] += 1

        reasons: list[str] = []
        if not student:
            reasons.append('unknown_student')
        if not subject:
            reasons.append('unknown_subject')
        if not period_valid:
            reasons.append('invalid_academic_period')
        values = {field: getattr(record, field) for field in FIELD_LIMITS}
        raw_dates = {field: getattr(record, date_field) for field, date_field in FIELD_DATES.items()}
        dates = {
            field: _utc_instant(value, normalized_naive=record.temporal_evidence_verified)
            for field, value in raw_dates.items()
        }
        complete = all(value is not None for value in values.values())
        if complete:
            counts['complete_observations'] += 1
        invalid_fields = [field for field, value in values.items() if value is not None and not FIELD_LIMITS[field][0] <= value <= FIELD_LIMITS[field][1]]
        if invalid_fields:
            counts['invalid_mark_records'] += 1
            reasons.extend(f'{field}_outside_valid_range' for field in invalid_fields)
        missing_dates = [field for field, value in values.items() if value is not None and dates[field] is None]
        counts['academic_values_with_source_dates'] += sum(value is not None and dates[field] is not None for field, value in values.items())
        counts['academic_values_missing_source_dates'] += len(missing_dates)
        if missing_dates:
            counts['records_without_observation_dates'] += 1
            reasons.append('missing_source_observation_date')
        unverified_dates = sum(raw_dates[field] is not None and dates[field] is None for field in raw_dates)
        counts['unverifiable_timestamps'] += unverified_dates
        if unverified_dates:
            reasons.append('source_timestamp_timezone_unverifiable')
        future_fields = [field for field, value in values.items() if value is not None and dates[field] and dates[field] > checked_at]
        if future_fields:
            counts['future_observations'] += 1
            reasons.extend(f'{field}_observation_is_future' for field in future_fields)
        if any(value is not None and dates[field] and dates[field] < checked_at - timedelta(days=180) for field, value in values.items()):
            counts['stale_observation_records_180d'] += 1
        if history_count > 1:
            counts['revised_records'] += 1
            reasons.append('revised_record_as_of_value_unverified')
        if len(outcome_group) > 1:
            reasons.append('duplicate_period_outcomes')
        if outcome is None:
            counts['missing_outcomes'] += 1
            reasons.append('missing_independent_outcome')
        outcome_at = outcome_dates.get(outcome.id) if outcome else None
        if outcome and outcome_at is None:
            counts['unverifiable_timestamps'] += 1
            reasons.append('outcome_timestamp_timezone_unverifiable')
        source = outcome.outcome_source.strip() if outcome and outcome.outcome_source else ''
        outcome_batch = batches.get(outcome.import_batch_id) if outcome else None
        valid_outcome = bool(
            outcome and outcome.risk_level in OUTCOME_CLASSES and source
            and not _is_prohibited_outcome_source(source)
            and not _is_prohibited_outcome_source(outcome_batch.source_filename if outcome_batch else '')
            and outcome_at is not None
            and outcome_at <= checked_at
        )
        if outcome and not valid_outcome:
            reasons.append('invalid_or_model_derived_outcome')
        if outcome_at and outcome_at > checked_at:
            counts['future_observations'] += 1
            reasons.append('outcome_observation_is_future')
        later_fields = [field for field, value in values.items() if value is not None and dates[field] and outcome_at and dates[field] > outcome_at]
        if later_fields:
            counts['temporally_invalid_observations'] += 1
            reasons.extend(f'{field}_observed_after_outcome' for field in later_fields)
        temporal_inputs_valid = bool(
            complete and all(dates[field] is not None for field in values)
            and not invalid_fields and not missing_dates and not future_fields and not later_fields
            and period_valid and student and subject and history_count <= 1
        )
        aligned = bool(temporal_inputs_valid and valid_outcome and len(outcome_group) == 1)
        if aligned:
            counts['temporally_aligned_observations'] += 1
            class_students[outcome.risk_level].add(student_identifier)
            class_aligned_records[outcome.risk_level] += 1
        elif outcome and valid_outcome and not later_fields:
            counts['temporally_invalid_observations'] += 1

        provenance_complete = bool((batch and batch.source_filename and batch.file_checksum) or record.updated_by_user_id is not None)
        if provenance_complete:
            counts['complete_provenance_records'] += 1
        else:
            reasons.append('missing_record_provenance')
        candidate_eligible = bool(aligned and aggregation_approved and contract_approved)
        if aligned and not aggregation_approved:
            reasons.append('approved_global_aggregation_strategy_unavailable')
        if aligned and not contract_approved:
            reasons.append('approved_candidate_feature_contract_unavailable')
        safe_values: dict[str, float | None] = {}
        for field, value in values.items():
            safe = value is not None and FIELD_LIMITS[field][0] <= value <= FIELD_LIMITS[field][1] and dates[field] is not None and dates[field] <= checked_at
            if safe and outcome_at and dates[field] > outcome_at:
                safe = False
            safe_values[field] = float(value) if safe else None

        dimension_keys = (
            (period if period_valid else 'INVALID', periods, 'period'),
            (subject_code or 'UNKNOWN', subject_coverage, 'subject'),
            (f'{period if period_valid else "INVALID"}/{subject_code or "UNKNOWN"}', period_subject, 'period_subject'),
        )
        for key, coverage, dimension in dimension_keys:
            item = coverage.setdefault(key, {
                'dimension': dimension,
                'academic_period': (period if period_valid else 'INVALID') if dimension != 'subject' else None,
                'subject_code': (subject_code or 'UNKNOWN') if dimension != 'period' else None,
                'record_count': 0,
                'student_identifiers': set(),
                'fa1_count': 0, 'fa2_count': 0, 'assignment_count': 0,
                'attendance_count': 0, 'observation_date_count': 0,
                'populated_value_count': 0,
                'complete_count': 0, 'aligned_count': 0,
                'risk_class_students': {level: set() for level in OUTCOME_CLASSES},
            })
            item['record_count'] += 1
            if student_identifier:
                item['student_identifiers'].add(student_identifier)
            for field, counter in (
                ('fa1', 'fa1_count'), ('fa2', 'fa2_count'),
                ('assignment_mark', 'assignment_count'), ('attendance_rate', 'attendance_count'),
            ):
                item[counter] += values[field] is not None
                item['populated_value_count'] += values[field] is not None
                item['observation_date_count'] += values[field] is not None and dates[field] is not None
            item['complete_count'] += complete
            item['aligned_count'] += aligned
            if aligned:
                item['risk_class_students'][outcome.risk_level].add(student_identifier)

        for dimension, subgroup_name in (
            ('department', student.department if student else None),
            ('section', student.section if student else None),
        ):
            if not subgroup_name:
                continue
            item = subgroup_coverage[dimension].setdefault(subgroup_name, {
                'dimension': dimension,
                'name': subgroup_name,
                'record_count': 0,
                'student_identifiers': set(),
                'aligned_count': 0,
                'risk_class_students': {level: set() for level in OUTCOME_CLASSES},
            })
            item['record_count'] += 1
            item['student_identifiers'].add(student_identifier)
            item['aligned_count'] += aligned
            if aligned:
                item['risk_class_students'][outcome.risk_level].add(student_identifier)

        rows.append({
            'dataset_id': f'p3-{fingerprint[:16]}',
            'dataset_version': PHASE3_DATASET_VERSION,
            'dataset_fingerprint': fingerprint,
            'student_identifier': student_identifier,
            'department': student.department if student else None,
            'section': student.section if student else None,
            'academic_period': period,
            'subject_code': subject_code,
            'subject_or_aggregation': subject_code,
            'fa1_mean': None,
            'fa2_mean': None,
            'assignment_mean': None,
            'attendance_mean': None,
            **safe_values,
            **{date_field: _iso(dates[field]) for field, date_field in FIELD_DATES.items()},
            'risk_target': outcome.risk_level if aligned else None,
            'risk_observed_at': _iso(outcome_at) if aligned else None,
            'independent_outcome_id': outcome.id if outcome else None,
            'outcome_source': outcome.outcome_source if outcome else None,
            'outcome_batch_id': outcome.import_batch_id if outcome else None,
            'outcome_filename': batches[outcome.import_batch_id].source_filename if outcome and outcome.import_batch_id in batches else None,
            'outcome_checksum': batches[outcome.import_batch_id].file_checksum if outcome and outcome.import_batch_id in batches else None,
            'temporally_aligned': aligned,
            'candidate_eligible': candidate_eligible,
            'source_batch_id': batch.id if batch else None,
            'source_filename': batch.source_filename if batch else None,
            'source_checksum': batch.file_checksum if batch else None,
            'source_record_id': record.id,
            'revision_count': history_count,
            'exclusion_reasons': sorted(set(reasons)),
        })

    counts['duplicate_record_groups'] = db.query(
        StudentSubjectAcademicRecord.student_id,
        StudentSubjectAcademicRecord.subject_id,
        StudentSubjectAcademicRecord.academic_period,
    ).group_by(
        StudentSubjectAcademicRecord.student_id,
        StudentSubjectAcademicRecord.subject_id,
        StudentSubjectAcademicRecord.academic_period,
    ).having(func.count(StudentSubjectAcademicRecord.id) > 1).count()
    counts['duplicate_import_rows'] = duplicate_import_rows
    counts['rejected_import_rows'] = len(rejected_rows)
    counts['distinct_academic_periods'] = len({record.academic_period for record in records if ACADEMIC_PERIOD_PATTERN.fullmatch(record.academic_period)})
    counts['distinct_students_with_records'] = len({row['student_identifier'] for row in rows if row['student_identifier']})
    counts['distinct_subjects_with_records'] = len({row['subject_code'] for row in rows if row['subject_code']})

    for coverage in (periods, subject_coverage, period_subject):
        for item in coverage.values():
            item['student_count'] = len(item.pop('student_identifiers'))
            item['risk_class_students'] = {level: len(item['risk_class_students'][level]) for level in OUTCOME_CLASSES}
            total = item['record_count']
            for key in ('fa1_count', 'fa2_count', 'assignment_count', 'attendance_count', 'observation_date_count', 'complete_count', 'aligned_count'):
                denominator = item['populated_value_count'] if key == 'observation_date_count' else total
                item[key.replace('_count', '_coverage_percent')] = item[key] / denominator * 100 if denominator else None
            item.pop('populated_value_count')
    for dimension_rows in subgroup_coverage.values():
        for item in dimension_rows.values():
            item['student_count'] = len(item.pop('student_identifiers'))
            item['risk_class_students'] = {level: len(item['risk_class_students'][level]) for level in OUTCOME_CLASSES}

    academic_periods = sorted({record.academic_period for record in records if ACADEMIC_PERIOD_PATTERN.fullmatch(record.academic_period)})
    class_coverage = {level: {'aligned_records': class_aligned_records[level], 'distinct_students': len(class_students[level])} for level in OUTCOME_CLASSES}
    grouped_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row['student_identifier'] and ACADEMIC_PERIOD_PATTERN.fullmatch(row['academic_period']):
            grouped_rows[(row['student_identifier'], row['academic_period'])].append(row)
    candidate_rows: list[dict[str, Any]] = []
    if aggregation_strategy and feature_contract:
        aggregate_sources = {
            'fa1_mean': 'fa1',
            'fa2_mean': 'fa2',
            'assignment_mean': 'assignment_mark',
            'attendance_mean': 'attendance_rate',
        }
        for (student_identifier, period), period_rows in sorted(grouped_rows.items()):
            if not period_rows or any(not row['temporally_aligned'] for row in period_rows):
                continue
            targets = {row['risk_target'] for row in period_rows}
            if len(targets) != 1 or None in targets:
                continue
            candidate_values = {
                name: sum(float(row[source]) for row in period_rows) / len(period_rows)
                for name, source in aggregate_sources.items()
            }
            candidate_rows.append({
                'dataset_id': f'p3-{fingerprint[:16]}',
                'dataset_version': PHASE3_DATASET_VERSION,
                'dataset_fingerprint': fingerprint,
                'student_identifier': student_identifier,
                'department': period_rows[0].get('department'),
                'section': period_rows[0].get('section'),
                'academic_period': period,
                'subject_code': None,
                'subject_or_aggregation': aggregation_strategy,
                **candidate_values,
                'risk_target': next(iter(targets)),
                'risk_observed_at': period_rows[0]['risk_observed_at'],
                'independent_outcome_id': period_rows[0]['independent_outcome_id'],
                'outcome_source': period_rows[0]['outcome_source'],
                'outcome_batch_id': period_rows[0]['outcome_batch_id'],
                'outcome_filename': period_rows[0]['outcome_filename'],
                'outcome_checksum': period_rows[0]['outcome_checksum'],
                'source_record_ids': [row['source_record_id'] for row in period_rows],
                'source_subjects': [row['subject_code'] for row in period_rows],
                'source_record_count': len(period_rows),
                'source_batch_ids': [row['source_batch_id'] for row in period_rows],
                'source_filenames': [row['source_filename'] for row in period_rows],
                'source_checksums': [row['source_checksum'] for row in period_rows],
                'dataset_fingerprint': fingerprint,
            })
    counts['candidate_training_samples'] = len(candidate_rows)
    training_class_students = {
        level: {row['student_identifier'] for row in candidate_rows if row['risk_target'] == level}
        for level in OUTCOME_CLASSES
    }
    blockers: list[str] = []
    aligned_count = len(candidate_rows) if aggregation_strategy and feature_contract else counts['temporally_aligned_observations']
    if counts['independent_outcomes'] == 0:
        blockers.append('0 independent outcomes available.')
    if aligned_count < MIN_COMPLETE_ALIGNED_OBSERVATIONS:
        blockers.append(f'Only {aligned_count} complete temporally aligned observations; at least {MIN_COMPLETE_ALIGNED_OBSERVATIONS} are required.')
    if len(academic_periods) < MIN_DISTINCT_ACADEMIC_PERIODS:
        blockers.append(f'Only {len(academic_periods)} academic period(s) available; at least {MIN_DISTINCT_ACADEMIC_PERIODS} distinct periods are required for temporal validation.')
    for level in OUTCOME_CLASSES:
        class_students_count = len(training_class_students[level]) if aggregation_strategy and feature_contract else len(class_students[level])
        if class_students_count < MIN_UNIQUE_STUDENTS_PER_CLASS:
            blockers.append(f'Insufficient {level} outcomes: {class_students_count} distinct aligned students; at least {MIN_UNIQUE_STUDENTS_PER_CLASS} are required.')
    if counts['invalid_mark_records']:
        blockers.append(f'{counts["invalid_mark_records"]} academic record(s) contain values outside the defined ranges.')
    if counts['invalid_academic_periods']:
        blockers.append(f'{counts["invalid_academic_periods"]} academic record(s) have invalid academic periods.')
    if counts['invalid_outcomes']:
        blockers.append(f'{counts["invalid_outcomes"]} outcome(s) have invalid labels, dates, or prohibited provenance.')
    if counts['temporally_invalid_observations']:
        blockers.append(f'{counts["temporally_invalid_observations"]} observation(s) have incomplete or future-dated predictor evidence relative to their outcomes.')
    if counts['future_observations']:
        blockers.append(f'{counts["future_observations"]} observation or outcome timestamp(s) are in the future.')
    if counts['records_without_observation_dates']:
        blockers.append(f'{counts["records_without_observation_dates"]} record(s) have values without source observation dates.')
    if counts['unverifiable_timestamps']:
        blockers.append(f'{counts["unverifiable_timestamps"]} source or outcome timestamp(s) are missing or unverifiable.')
    if counts['revised_records']:
        blockers.append(f'{counts["revised_records"]} corrected record(s) lack a verified as-of revision contract.')
    if counts['duplicate_record_groups'] or counts['duplicate_outcome_groups']:
        blockers.append('Duplicate student/subject/period records or student/period outcomes must be resolved.')
    if counts['conflicting_records']:
        blockers.append(f'{counts["conflicting_records"]} conflicting import row(s) were rejected and require source correction.')
    if counts['unknown_students'] or counts['unknown_subjects']:
        blockers.append(f'{counts["unknown_students"]} unknown student reference(s) and {counts["unknown_subjects"]} unknown subject reference(s) require correction.')
    if counts['complete_provenance_records'] < len(records):
        blockers.append(f'{len(records) - counts["complete_provenance_records"]} academic record(s) lack complete source/actor provenance.')
    if counts['complete_outcome_provenance'] < counts['independent_outcomes']:
        blockers.append(f'{counts["independent_outcomes"] - counts["complete_outcome_provenance"]} independent outcome(s) lack complete source/actor provenance.')
    if not aggregation_approved:
        blockers.append('No approved aggregation strategy maps subject-period observations to the existing global student-period target; a request value is only a proposal.')
    if not contract_approved:
        blockers.append('The requested candidate feature contract has not been explicitly approved for this dataset contract.')
    readiness = {
        'training_ready': not blockers,
        'blockers': blockers,
        'evidence': {
            'independent_outcomes': counts['independent_outcomes'],
            'aligned_observations': aligned_count,
            'complete_observations': counts['complete_observations'],
            'distinct_academic_periods': academic_periods,
            'distinct_students_with_records': counts['distinct_students_with_records'],
            'students_per_risk_class': {level: len(training_class_students[level]) if aggregation_strategy and feature_contract else len(class_students[level]) for level in OUTCOME_CLASSES},
            'candidate_training_samples': len(candidate_rows),
            'class_coverage': class_coverage,
            'invalid_mark_records': counts['invalid_mark_records'],
            'temporal_violations': counts['temporally_invalid_observations'],
            'duplicate_record_groups': counts['duplicate_record_groups'],
            'duplicate_outcome_groups': counts['duplicate_outcome_groups'],
            'provenance_complete_records': counts['complete_provenance_records'],
            'aggregation_strategy': aggregation_strategy,
            'aggregation_approved': aggregation_approved,
            'feature_contract': feature_contract,
            'feature_contract_approved': contract_approved,
            'dataset_fingerprint': fingerprint,
            'fingerprint_reproducible': True,
        },
        'thresholds': {
            'minimum_complete_aligned_observations': MIN_COMPLETE_ALIGNED_OBSERVATIONS,
            'minimum_distinct_academic_periods': MIN_DISTINCT_ACADEMIC_PERIODS,
            'minimum_students_per_class': MIN_UNIQUE_STUDENTS_PER_CLASS,
        },
        'target_definition': PHASE3_TARGET_DEFINITION,
        'aggregation_strategy': aggregation_strategy,
        'feature_contract': feature_contract,
    }
    created = checked_at.replace(tzinfo=timezone.utc).isoformat() if checked_at.tzinfo is None else checked_at.isoformat()
    return {
        'dataset_id': f'p3-{fingerprint[:16]}',
        'dataset_version': PHASE3_DATASET_VERSION,
        'dataset_fingerprint': fingerprint,
        'created_at': created,
        'feature_contract_version': PHASE3_FEATURE_CONTRACT_VERSION,
        'target_definition': PHASE3_TARGET_DEFINITION,
        'aggregation_strategy': aggregation_strategy,
        'target_scope': 'global student-period; repeated subject rows are not independent training examples',
        'training_eligible': readiness['training_ready'],
        'counts': counts,
        'coverage': {
            'by_academic_period': periods,
            'by_subject': subject_coverage,
            'by_period_and_subject': period_subject,
            'by_subgroup': subgroup_coverage,
            'by_risk_class': class_coverage,
        },
        'readiness': readiness,
        'rows': rows,
        'candidate_rows': candidate_rows,
        'rejected_rows': rejected_rows,
        'duplicate_import_rows': duplicate_import_rows,
    }
