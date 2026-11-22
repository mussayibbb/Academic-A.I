from datetime import date, datetime, time, timedelta
import math
from typing import Any, Callable

from sqlalchemy.orm import Session

from ai.risk.features import FEATURES
from backend.app.early_warning_config import priority_for_probability
from backend.app.models import RiskPrediction, RiskSnapshot, Student


def backfill_weekly_snapshots(
    db: Session,
    score_features: Callable[[dict[str, float]], dict[str, Any]],
    *,
    as_of: datetime | None = None,
) -> int:
    cutoff = as_of or datetime.utcnow()
    current_week = cutoff.date() - timedelta(days=cutoff.weekday())
    first_week = current_week - timedelta(weeks=7)
    added = 0

    for student in db.query(Student).order_by(Student.id).all():
        predictions = db.query(RiskPrediction).filter(
            RiskPrediction.student_id == student.id,
            RiskPrediction.prediction_timestamp >= datetime.combine(first_week, time.min),
            RiskPrediction.prediction_timestamp <= cutoff,
        ).order_by(RiskPrediction.prediction_timestamp.asc(), RiskPrediction.id.asc()).all()
        latest_by_week: dict[date, RiskPrediction] = {}
        for prediction in predictions:
            values = prediction.input_features or {}
            if not set(FEATURES).issubset(values):
                continue
            features = {name: float(values[name]) for name in FEATURES}
            if not all(math.isfinite(value) for value in features.values()):
                continue
            week_start = prediction.prediction_timestamp.date() - timedelta(days=prediction.prediction_timestamp.weekday())
            latest_by_week[week_start] = prediction

        for week_start, source in latest_by_week.items():
            timestamp = datetime.combine(week_start, time.min)
            existing = db.query(RiskSnapshot.id).filter(
                RiskSnapshot.student_id == student.id,
                RiskSnapshot.recorded_at == timestamp,
                RiskSnapshot.model_version.like('early-warning:%'),
            ).first()
            if existing:
                continue
            features = {name: float(source.input_features[name]) for name in FEATURES}
            scored = score_features(features)
            probability = float(scored['class_probabilities'].get('HIGH', 0.0))
            priority = priority_for_probability(probability)
            risk_level = {'High': 'HIGH', 'Medium': 'MEDIUM', 'Low': 'LOW'}[priority]
            db.add(RiskSnapshot(
                student_id=student.id,
                model_version=f'early-warning:{scored["model_version"]}',
                risk_level=risk_level,
                probability=probability,
                recorded_at=timestamp,
            ))
            added += 1
    if added:
        db.commit()
    return added


def weekly_snapshot_trend(db: Session, student_id: int) -> list[dict[str, Any]]:
    rows = db.query(RiskSnapshot).filter(
        RiskSnapshot.student_id == student_id,
        RiskSnapshot.model_version.like('early-warning:%'),
    ).order_by(RiskSnapshot.recorded_at.asc(), RiskSnapshot.id.asc()).all()
    by_week: dict[date, RiskSnapshot] = {}
    for row in rows:
        week_start = row.recorded_at.date() - timedelta(days=row.recorded_at.weekday())
        by_week[week_start] = row
    return [
        {'weekStart': week_start.isoformat(), 'risk': max(0.0, min(1.0, float(row.probability)))}
        for week_start, row in sorted(by_week.items())[-8:]
    ]
