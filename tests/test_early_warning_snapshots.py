from datetime import datetime, timedelta

from ai.risk.features import FEATURES
from backend.app.main import app
from backend.app.models import RiskPrediction, Student
from backend.app.services.early_warning_snapshots import (
    backfill_weekly_snapshots,
    weekly_snapshot_trend,
)


def test_backfill_scores_only_dated_point_in_time_inputs(client):
    with app.state.test_session_factory() as db:
        student_id = db.query(Student.id).first()[0]
        start = datetime(2026, 9, 28)
        input_features = {name: 0.0 for name in FEATURES}
        input_features['attendance'] = 72.0
        for week in range(8):
            db.add(RiskPrediction(
                student_id=student_id,
                risk_level='HIGH',
                probability=0.8,
                model_version='source-model',
                prediction_timestamp=start + timedelta(weeks=week, hours=2),
                input_features=input_features,
            ))
        db.add(RiskPrediction(
            student_id=student_id,
            risk_level='HIGH',
            probability=0.8,
            model_version='future-model',
            prediction_timestamp=datetime(2026, 11, 23),
            input_features=input_features,
        ))
        db.commit()

        added = backfill_weekly_snapshots(
            db,
            lambda values: {
                'model_version': 'calibrated-test',
                'class_probabilities': {'HIGH': values['attendance'] / 100},
            },
            as_of=datetime(2026, 11, 22, 23, 59),
        )

        trend = weekly_snapshot_trend(db, student_id)
        assert added == 8
        assert len(trend) == 8
        assert trend[0]['weekStart'] == '2026-09-28'
        assert trend[-1]['weekStart'] == '2026-11-16'
        assert all(point['risk'] == 0.72 for point in trend)
