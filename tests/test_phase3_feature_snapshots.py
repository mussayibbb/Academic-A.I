import csv
import hashlib
import io
from datetime import datetime, timezone

import pandas as pd
import pytest

from ai.risk.candidate import evaluate_common_test_set, train_candidate
from ai.risk.features import FEATURE_CONTRACT_VERSION, FEATURES
from ai.risk.phase3 import (
    DATASET_FIELDS,
    FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
    PHASE3_TARGET_DEFINITION,
    _checked_at,
    _feature_snapshot_datetime,
    build_phase3_dataset,
)
from ai.risk.predict import DEFAULT_ARTIFACT
from backend.app.models import (
    AcademicRiskOutcome,
    ModelRegistryEntry,
    Student,
    StudentPeriodFeatureSnapshot,
    User,
)
from backend.app.services.academic_imports import ImportValidationError, import_academic_data
from backend.app.services.model_deployment import verify_candidate_artifact


def _feature_values() -> dict[str, float]:
    return {
        'attendance': 82.0,
        'assignments_completed': 6.0,
        'activity_per_week': 2.5,
        'missing_submissions': 1.0,
        'attendance_change': -1.5,
        'assignment_submission_rate': 0.85,
    }


def _snapshot_csv(
    *,
    student_id: str = 'T-001',
    period: str = '2026-S1',
    values: dict[str, float] | None = None,
    observed_at: dict[str, str] | None = None,
) -> bytes:
    feature_values = values or _feature_values()
    dates = observed_at or {name: '2026-09-01T00:00:00Z' for name in FEATURES}
    fields = [
        'student_id', 'academic_period', *FEATURES,
        *(f'{name}_observed_at' for name in FEATURES),
    ]
    row = {
        'student_id': student_id,
        'academic_period': period,
        **feature_values,
        **{f'{name}_observed_at': dates[name] for name in FEATURES},
    }
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerow(row)
    return output.getvalue().encode()


def test_snapshot_import_requires_all_dated_features_and_preserves_immutable_provenance(client):
    with client.app.state.test_session_factory() as db:
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        result = import_academic_data(
            db,
            user_id=teacher.id,
            filename='synthetic-test-fixture.csv',
            content=_snapshot_csv(),
            dataset_type='production_feature_snapshots',
        )
        snapshot = db.query(StudentPeriodFeatureSnapshot).one()
        assert result['imported_rows'] == 1
        assert snapshot.feature_values == _feature_values()
        assert list(snapshot.feature_values) == FEATURES
        assert set(snapshot.feature_observed_at) == set(FEATURES)
        assert snapshot.source_filename == 'synthetic-test-fixture.csv'
        assert snapshot.source_checksum
        assert snapshot.source_row_checksum
        assert snapshot.recorded_by_user_id == teacher.id

        with pytest.raises(ValueError, match='immutable'):
            snapshot.feature_values = {**snapshot.feature_values, 'attendance': 1.0}
            db.commit()
        db.rollback()

        missing_date = {name: '2026-09-01T00:00:00Z' for name in FEATURES}
        missing_date['assignment_submission_rate'] = ''
        rejected = import_academic_data(
            db,
            user_id=teacher.id,
            filename='synthetic-test-missing-date.csv',
            content=_snapshot_csv(student_id='T-002', observed_at=missing_date),
            dataset_type='production_feature_snapshots',
        )
        assert rejected['imported_rows'] == 0
        assert rejected['rejected_rows'] == 1
        assert db.query(StudentPeriodFeatureSnapshot).count() == 1


def test_offset_instants_normalize_to_the_same_utc_instant():
    first = _feature_snapshot_datetime('2026-09-01T09:00:00-05:00')
    equivalent = _feature_snapshot_datetime('2026-09-01T16:00:00+02:00')
    assert first is not None
    assert first == equivalent
    assert first.isoformat() == '2026-09-01T14:00:00+00:00'


def test_explicit_cutoff_requires_timezone_and_normalizes_equivalent_instants(client):
    assert _checked_at(None).tzinfo == timezone.utc
    utc_cutoff = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    offset_cutoff = datetime.fromisoformat('2026-09-01T16:00:00+02:00')
    assert _checked_at(utc_cutoff) == _checked_at(offset_cutoff)

    with client.app.state.test_session_factory() as db:
        with pytest.raises(ValueError, match='evaluation cutoff'):
            build_phase3_dataset(db, now=datetime(2026, 9, 1, 14))


@pytest.mark.parametrize('timestamp', ['2026-09-01T09:00:00', 'not-a-timestamp'])
def test_snapshot_import_rejects_naive_or_invalid_temporal_evidence(client, timestamp):
    dates = {name: '2026-09-01T00:00:00Z' for name in FEATURES}
    dates['attendance'] = timestamp
    with client.app.state.test_session_factory() as db:
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        result = import_academic_data(
            db,
            user_id=teacher.id,
            filename='faculty-snapshot.csv',
            content=_snapshot_csv(observed_at=dates),
            dataset_type='production_feature_snapshots',
        )
        assert result['imported_rows'] == 0
        assert result['rejected_rows'] == 1
        assert db.query(StudentPeriodFeatureSnapshot).count() == 0
        assert any('timezone offset' in error or 'ISO date or datetime' in error for error in result['validation_errors'][0]['errors'])


@pytest.mark.parametrize(
    ('outcome_timestamp', 'expected_aligned'),
    [
        ('2026-09-01T16:00:00+02:00', True),
        ('2026-09-01T10:00:00+02:00', False),
    ],
)
def test_snapshot_temporal_comparison_uses_utc_instants_and_inclusive_outcome_boundary(
    client,
    monkeypatch,
    outcome_timestamp,
    expected_aligned,
):
    monkeypatch.setattr(
        'ai.risk.phase3.APPROVED_AGGREGATION_STRATEGY',
        FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
    )
    monkeypatch.setattr('ai.risk.phase3.APPROVED_CANDIDATE_FEATURE_CONTRACT', tuple(FEATURES))
    feature_dates = {name: '2026-09-01T09:00:00-05:00' for name in FEATURES}
    with client.app.state.test_session_factory() as db:
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        import_academic_data(
            db,
            user_id=teacher.id,
            filename='faculty-feature-records.csv',
            content=_snapshot_csv(observed_at=feature_dates),
            dataset_type='production_feature_snapshots',
        )
        import_academic_data(
            db,
            user_id=teacher.id,
            filename='faculty-outcome-records.csv',
            content=(
                'student_id,academic_period,risk_target,observed_at,outcome_source\n'
                f'T-001,2026-S1,LOW,{outcome_timestamp},faculty evaluation panel\n'
            ).encode(),
            dataset_type='risk_outcomes',
        )
        result = build_phase3_dataset(
            db,
            now=datetime(2026, 12, 1, tzinfo=timezone.utc),
            aggregation_strategy=FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
            feature_contract=FEATURES,
        )

    assert result['rows'][0]['temporally_aligned'] is expected_aligned
    assert bool(result['candidate_rows']) is expected_aligned


def test_feature_after_evaluation_cutoff_is_excluded(client, monkeypatch):
    monkeypatch.setattr(
        'ai.risk.phase3.APPROVED_AGGREGATION_STRATEGY',
        FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
    )
    monkeypatch.setattr('ai.risk.phase3.APPROVED_CANDIDATE_FEATURE_CONTRACT', tuple(FEATURES))
    feature_dates = {name: '2026-09-01T00:00:01Z' for name in FEATURES}
    with client.app.state.test_session_factory() as db:
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        import_academic_data(
            db,
            user_id=teacher.id,
            filename='faculty-feature-records.csv',
            content=_snapshot_csv(observed_at=feature_dates),
            dataset_type='production_feature_snapshots',
        )
        import_academic_data(
            db,
            user_id=teacher.id,
            filename='faculty-outcome-records.csv',
            content=(
                'student_id,academic_period,risk_target,observed_at,outcome_source\n'
                'T-001,2026-S1,LOW,2026-09-01T00:00:00Z,faculty evaluation panel\n'
            ).encode(),
            dataset_type='risk_outcomes',
        )
        result = build_phase3_dataset(
            db,
            now=datetime(2026, 9, 1, tzinfo=timezone.utc),
            aggregation_strategy=FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
            feature_contract=FEATURES,
        )

    assert result['rows'][0]['temporally_aligned'] is False
    assert result['candidate_rows'] == []


def test_feature_snapshot_conflicts_are_rejected_without_overwriting_existing_evidence(client):
    with client.app.state.test_session_factory() as db:
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        original = _feature_values()
        import_academic_data(
            db,
            user_id=teacher.id,
            filename='synthetic-test-original.csv',
            content=_snapshot_csv(values=original),
            dataset_type='production_feature_snapshots',
        )
        changed = {**original, 'attendance': 99.0}
        result = import_academic_data(
            db,
            user_id=teacher.id,
            filename='synthetic-test-correction.csv',
            content=_snapshot_csv(values=changed),
            dataset_type='production_feature_snapshots',
        )
        snapshot = db.query(StudentPeriodFeatureSnapshot).one()
        assert result['imported_rows'] == 0
        assert result['rejected_rows'] == 1
        assert 'Conflicting feature snapshot' in result['validation_errors'][0]['errors'][0]
        assert snapshot.feature_values == original


@pytest.mark.parametrize(('feature', 'value'), [
    ('attendance', 101.0),
    ('assignment_submission_rate', 1.01),
    ('missing_submissions', -1.0),
    ('activity_per_week', 'nan'),
])
def test_snapshot_import_rejects_invalid_production_feature_values(client, feature, value):
    values = {**_feature_values(), feature: value}
    with client.app.state.test_session_factory() as db:
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        result = import_academic_data(
            db,
            user_id=teacher.id,
            filename=f'synthetic-test-invalid-{feature}.csv',
            content=_snapshot_csv(values=values),
            dataset_type='production_feature_snapshots',
        )
        assert result['imported_rows'] == 0
        assert result['rejected_rows'] == 1
        assert db.query(StudentPeriodFeatureSnapshot).count() == 0
        assert result['validation_errors'][0]['errors']


def test_feature_snapshot_readiness_withholds_later_or_synthetic_test_evidence(client, monkeypatch):
    monkeypatch.setattr(
        'ai.risk.phase3.APPROVED_AGGREGATION_STRATEGY',
        FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
    )
    monkeypatch.setattr(
        'ai.risk.phase3.APPROVED_CANDIDATE_FEATURE_CONTRACT',
        tuple(FEATURES),
    )
    observed = {name: '2026-09-01T00:00:00Z' for name in FEATURES}
    observed['attendance'] = '2026-09-20T00:00:00Z'
    with client.app.state.test_session_factory() as db:
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        student = db.query(Student).filter_by(student_id='T-001').one()
        import_academic_data(
            db,
            user_id=teacher.id,
            filename='synthetic-test-fixture.csv',
            content=_snapshot_csv(observed_at=observed),
            dataset_type='production_feature_snapshots',
        )
        db.add(AcademicRiskOutcome(
            student_id=student.id,
            academic_period='2026-S1',
            risk_level='LOW',
            observed_at=datetime(2026, 9, 10),
            temporal_evidence_verified=True,
            outcome_source='synthetic test fixture outcome',
            recorded_by_user_id=teacher.id,
        ))
        db.commit()

        result = build_phase3_dataset(
            db,
            now=datetime(2026, 12, 1, tzinfo=timezone.utc),
            aggregation_strategy=FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
            feature_contract=FEATURES,
        )

    row = result['rows'][0]
    assert set(row) <= set(DATASET_FIELDS)
    assert row['attendance'] is None
    assert row['risk_target'] is None
    assert 'attendance_observed_after_outcome' in row['exclusion_reasons']
    assert result['counts']['independent_outcomes'] == 0
    assert result['counts']['prohibited_source_records'] == 1
    assert result['candidate_rows'] == []
    assert result['readiness']['training_ready'] is False


def test_phase3_policy_requires_the_exact_ordered_production_contract(client, teacher_token):
    headers = {'Authorization': f'Bearer {teacher_token}'}
    payload = {
        'explicit_approval': True,
        'aggregation_strategy': FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
        'feature_contract': list(reversed(FEATURES)),
        'approval_note': 'Synthetic test-only invalid-order approval.',
    }
    rejected = client.post('/api/v1/ml/phase3/policy/approve', json=payload, headers=headers)
    assert rejected.status_code == 422

    payload['feature_contract'] = FEATURES
    approved = client.post('/api/v1/ml/phase3/policy/approve', json=payload, headers=headers)
    assert approved.status_code == 200
    policy = client.get('/api/v1/ml/phase3/policy', headers=headers).json()
    assert policy['approved_policy']['feature_contract'] == FEATURES
    assert FEATURE_SNAPSHOT_AGGREGATION_STRATEGY in policy['supported_aggregation_strategies']


def test_teacher_api_blocks_training_and_export_includes_contract_for_synthetic_snapshots(client, teacher_token, tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.main.CANDIDATE_ARTIFACT_DIRECTORY', tmp_path / 'candidate-artifacts')
    headers = {'Authorization': f'Bearer {teacher_token}'}
    with client.app.state.test_session_factory() as db:
        teacher = db.query(User).filter_by(email='teacher@example.com').one()
        student = db.query(Student).filter_by(student_id='T-001').one()
        import_academic_data(
            db,
            user_id=teacher.id,
            filename='synthetic-test-fixture.csv',
            content=_snapshot_csv(),
            dataset_type='production_feature_snapshots',
        )
        db.add(AcademicRiskOutcome(
            student_id=student.id,
            academic_period='2026-S1',
            risk_level='LOW',
            observed_at=datetime(2026, 9, 15),
            temporal_evidence_verified=True,
            outcome_source='synthetic test fixture outcome',
            recorded_by_user_id=teacher.id,
        ))
        db.commit()

    approved = client.post('/api/v1/ml/phase3/policy/approve', json={
        'explicit_approval': True,
        'aggregation_strategy': FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
        'feature_contract': FEATURES,
        'approval_note': 'Synthetic test-only methodology approval.',
    }, headers=headers)
    assert approved.status_code == 200

    query = [
        ('aggregation_strategy', FEATURE_SNAPSHOT_AGGREGATION_STRATEGY),
        *[('feature_contract', feature) for feature in FEATURES],
    ]
    exported = client.get('/api/v1/ml/phase3/dataset', params=query, headers=headers)
    assert exported.status_code == 200
    assert ','.join(FEATURES) in exported.text.splitlines()[0]
    assert exported.headers['x-training-eligible'] == 'false'

    readiness = client.get('/api/v1/ml/phase3/readiness', headers=headers)
    assert readiness.status_code == 200
    assert readiness.json()['readiness']['training_ready'] is False
    assert any('synthetic' in blocker.lower() for blocker in readiness.json()['readiness']['blockers'])
    training = client.post('/api/v1/ml/phase3/train', json={
        'aggregation_strategy': FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
        'feature_contract': FEATURES,
    }, headers=headers)
    assert training.status_code == 409
    assert 'real-data readiness criteria' in training.json()['detail']['message']
    assert not (tmp_path / 'candidate-artifacts').exists()


def _synthetic_training_frame() -> pd.DataFrame:
    classes = ('HIGH', 'MEDIUM', 'LOW')
    periods = ('2024-S1', '2024-S2', '2025-S1')
    rows = []
    for student_number in range(60):
        label = classes[student_number % len(classes)]
        class_index = classes.index(label)
        student_identifier = f'SYNTHETIC-TEST-{student_number:03d}'
        for period_index, period in enumerate(periods):
            rows.append({
                'student_identifier': student_identifier,
                'academic_period': period,
                'risk_target': label,
                'independent_outcome_id': student_number * len(periods) + period_index + 1,
                'source_row_checksum': hashlib.sha256(
                    f'synthetic-test-row:{student_number}:{period}'.encode()
                ).hexdigest(),
                'source_checksum': hashlib.sha256(b'synthetic-test-source-file').hexdigest(),
                'outcome_checksum': hashlib.sha256(b'synthetic-test-outcome-file').hexdigest(),
                'outcome_source': 'synthetic test fixture only',
                'source_filename': 'synthetic-test-fixture.csv',
                'attendance': float(50 + class_index * 15 + student_number % 5 + period_index),
                'assignments_completed': float(2 + class_index * 2 + student_number % 4),
                'activity_per_week': float(1 + class_index + (student_number % 5) / 2),
                'missing_submissions': float(6 - class_index * 2 + student_number % 3),
                'attendance_change': float(-2 + class_index * 2 + period_index / 10),
                'assignment_submission_rate': float(0.35 + class_index * 0.25 + (student_number % 5 + period_index) / 100),
            })
    return pd.DataFrame(rows)


def test_synthetic_candidate_artifact_passes_strict_contract_and_common_test_set_evaluation(tmp_path):
    frame = _synthetic_training_frame()
    fingerprint = hashlib.sha256(b'explicit synthetic test-only phase3 fixture').hexdigest()
    trained = train_candidate(
        frame,
        output_directory=tmp_path,
        feature_contract=list(FEATURES),
        target_column='risk_target',
        period_column='academic_period',
        identity_column='student_identifier',
        dataset_id='synthetic-phase3-test-only',
        dataset_version='synthetic-test-v1',
        dataset_fingerprint=fingerprint,
        target_definition='Synthetic test-only labels; no institutional or operational meaning.',
        aggregation_strategy=FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
        feature_contract_version=FEATURE_CONTRACT_VERSION,
    )
    metadata = trained['metadata']
    entry = ModelRegistryEntry(
        model_id=metadata['model_version'],
        model_version=metadata['model_version'],
        model_type='classification',
        feature_schema=list(FEATURES),
        feature_contract_version=FEATURE_CONTRACT_VERSION,
        training_dataset_hash=fingerprint,
        validation_metrics={
            'validation': metadata['validation_metrics'],
            'test': metadata['test_metrics'],
            'feature_importance': metadata['feature_importance'],
            'subgroup_evaluation': metadata['subgroup_evaluation'],
        },
        artifact_identifier=trained['artifact_path'].name,
        artifact_hash=trained['artifact_hash'],
        dataset_id=metadata['dataset_id'],
        dataset_version=metadata['dataset_version'],
        dataset_fingerprint=fingerprint,
        target_definition=metadata['target_definition'],
        aggregation_strategy=FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
        training_periods=metadata['training_periods'],
        validation_periods=metadata['validation_periods'],
        test_periods=metadata['test_periods'],
        algorithm=metadata['algorithm'],
        status='candidate',
    )
    artifact_path, artifact = verify_candidate_artifact(entry, tmp_path)
    assert artifact_path == trained['artifact_path']
    assert artifact['feature_contract'] == FEATURES
    assert artifact['metadata']['feature_contract_version'] == FEATURE_CONTRACT_VERSION

    test_frame = frame[frame['academic_period'].isin(metadata['test_periods'])]
    comparison = evaluate_common_test_set(
        test_frame,
        candidate_artifact_path=artifact_path,
        production_artifact_path=DEFAULT_ARTIFACT,
    )
    assert comparison['sample_count'] == len(test_frame) == 60
    assert comparison['periods'] == metadata['test_periods']
    assert len(comparison['row_identity_sha256']) == 64
    assert comparison['candidate']['sample_count'] == comparison['production']['sample_count'] == 60
    assert comparison['candidate']['labels'] == comparison['production']['labels'] == ['HIGH', 'MEDIUM', 'LOW']

