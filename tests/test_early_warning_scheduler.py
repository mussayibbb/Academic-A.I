from datetime import datetime

from backend.app.main import app
from backend.app.models import RiskSnapshot, Student
from backend.app.services.score_early_warnings import save_weekly_scores


def test_weekly_scoring_updates_the_current_week_snapshot_idempotently(client):
    with app.state.test_session_factory() as db:
        student = db.query(Student).order_by(Student.id).first()
        high_prediction = {
            'model_version': 'calibrated-model',
            'class_probabilities': {'HIGH': 0.8, 'MEDIUM': 0.15, 'LOW': 0.05},
        }
        as_of = datetime(2026, 11, 22, 12)

        assert save_weekly_scores(db, [student], [high_prediction], as_of=as_of) == 1
        snapshot = db.query(RiskSnapshot).filter(
            RiskSnapshot.student_id == student.id,
            RiskSnapshot.model_version == 'early-warning:calibrated-model',
        ).one()
        assert snapshot.risk_level == 'HIGH'
        assert snapshot.probability == 0.8
        assert snapshot.recorded_at == datetime(2026, 11, 16)

        watch_prediction = {
            'model_version': 'calibrated-model',
            'class_probabilities': {'HIGH': 0.4, 'MEDIUM': 0.5, 'LOW': 0.1},
        }
        assert save_weekly_scores(db, [student], [watch_prediction], as_of=as_of) == 1
        snapshots = db.query(RiskSnapshot).filter(
            RiskSnapshot.student_id == student.id,
            RiskSnapshot.model_version == 'early-warning:calibrated-model',
        ).all()
        assert len(snapshots) == 1
        assert snapshots[0].risk_level == 'MEDIUM'
        assert snapshots[0].probability == 0.4
