from datetime import datetime, time, timedelta
import math
from typing import Any

from sqlalchemy.orm import Session

from backend.app.early_warning_config import priority_for_probability
from backend.app.models import RiskSnapshot, Student


def save_weekly_scores(
    db: Session,
    students: list[Student],
    predictions: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
) -> int:
    if len(students) != len(predictions):
        raise ValueError('Each student must have exactly one weekly risk prediction.')
    cutoff = as_of or datetime.utcnow()
    week_start = cutoff.date() - timedelta(days=cutoff.weekday())
    recorded_at = datetime.combine(week_start, time.min)
    saved = 0

    for student, prediction in zip(students, predictions):
        class_probabilities = prediction.get('class_probabilities') or {}
        probability = float(class_probabilities.get('HIGH', 0.0))
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError(f'Invalid HIGH-class probability for student {student.id}.')
        priority = priority_for_probability(probability)
        risk_level = {'High': 'HIGH', 'Medium': 'MEDIUM', 'Low': 'LOW'}[priority]
        model_version = f'early-warning:{prediction["model_version"]}'

        snapshot = db.query(RiskSnapshot).filter(
            RiskSnapshot.student_id == student.id,
            RiskSnapshot.recorded_at == recorded_at,
            RiskSnapshot.model_version.like('early-warning:%'),
        ).order_by(RiskSnapshot.id.asc()).first()
        if snapshot is None:
            snapshot = RiskSnapshot(student_id=student.id, recorded_at=recorded_at)
            db.add(snapshot)
        snapshot.model_version = model_version
        snapshot.probability = probability
        snapshot.risk_level = risk_level
        saved += 1

    if students:
        db.commit()
    return saved


def main() -> None:
    from ai.risk.predict import load_artifact, predict_many
    from backend.app.database import SessionLocal
    from backend.app.main import selected_model_artifact, student_feature_batch

    with SessionLocal() as db:
        artifact_path = selected_model_artifact(db)
        artifact = load_artifact(artifact_path)
        if artifact.get('metadata', {}).get('demo_data'):
            raise SystemExit('Refusing to score weekly Early Warnings with a DEMO DATA model.')
        students = db.query(Student).order_by(Student.id).all()
        if not students:
            print('No students to score.')
            return
        features_by_student, _ = student_feature_batch(students, db)
        features = [features_by_student[student.id] for student in students]
        predictions = predict_many(features, artifact_path)
        saved = save_weekly_scores(db, students, predictions)
        print(f'Saved or updated {saved} weekly Early Warnings snapshots for {len(students)} students.')


if __name__ == '__main__':
    main()
