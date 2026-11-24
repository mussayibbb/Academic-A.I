from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from ai.risk.features import FEATURES

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT = ROOT / 'models' / 'risk' / 'risk_model.joblib'


@lru_cache(maxsize=4)
def _load_cached_artifact(path: str, modified_ns: int, size: int) -> dict[str, Any]:
    return joblib.load(path)


def load_artifact(artifact_path: Path = DEFAULT_ARTIFACT) -> dict[str, Any]:
    resolved_path = Path(artifact_path).resolve()
    stat = resolved_path.stat()
    return _load_cached_artifact(str(resolved_path), stat.st_mtime_ns, stat.st_size)


def predict_many(features_rows: list[dict[str, float]], artifact_path: Path = DEFAULT_ARTIFACT) -> list[dict[str, Any]]:
    if not features_rows:
        return []
    forbidden = {
        'student_id', 'risk_level', 'risk_probability', 'fa1', 'fa2',
        'previous_gpa', 'current_gpa', 'assessment', 'assessment_change',
        'momentum_score', 'percentage', 'grade', 'mark', 'score',
        'midterm', 'final', 'examination', 'cgpa',
    }
    for features in features_rows:
        unexpected = sorted(set(features) & forbidden)
        if unexpected:
            raise ValueError(f'Marks, identifiers, or excluded academic features are not model inputs: {", ".join(unexpected)}')
        missing = sorted(set(FEATURES) - set(features))
        if missing:
            raise ValueError(f'Missing model features: {", ".join(missing)}')
    artifact = load_artifact(artifact_path)
    pipeline = artifact['pipeline']
    metadata = artifact['metadata']
    frame = pd.DataFrame(
        [{name: float(features[name]) for name in FEATURES} for features in features_rows],
        columns=FEATURES,
    )
    probabilities_by_row = np.asarray(pipeline.predict_proba(frame), dtype=float)
    label_encoder = artifact['label_encoder']
    class_ids = np.asarray(pipeline.classes_, dtype=int)
    classes = label_encoder.inverse_transform(class_ids)
    decision_scores = None
    if hasattr(pipeline, 'decision_function'):
        raw_scores = np.asarray(pipeline.decision_function(frame), dtype=float)
        if raw_scores.ndim == 2:
            high_indices = [index for index, label in enumerate(classes) if str(label).upper() == 'HIGH']
            if high_indices:
                decision_scores = raw_scores[:, high_indices[0]]
    predictions: list[dict[str, Any]] = []
    for row_index, probabilities in enumerate(probabilities_by_row):
        class_probabilities = {str(label).upper(): float(probability) for label, probability in zip(classes, probabilities)}
        level = str(classes[int(np.argmax(probabilities))]).upper()
        prediction = {
            'risk_level': level,
            'risk_probability': class_probabilities[level],
            'risk_probability_semantics': 'P(risk_level)',
            'risk_level_policy': 'argmax(class_probabilities)',
            'model_version': metadata['model_version'],
            'prediction_timestamp': datetime.now(timezone.utc),
            'class_probabilities': class_probabilities,
        }
        if decision_scores is not None:
            prediction['raw_high_decision_score'] = float(decision_scores[row_index])
        predictions.append(prediction)
    return predictions


def predict(features: dict[str, float], artifact_path: Path = DEFAULT_ARTIFACT) -> dict[str, Any]:
    return predict_many([features], artifact_path)[0]