from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
import shap

from ai.risk.features import FEATURES
from ai.risk.predict import DEFAULT_ARTIFACT, load_artifact

_GLOBAL_IMPORTANCE_LOCK = Lock()


def _validated_pipeline(artifact: dict[str, Any]) -> tuple[Any, Any]:
    pipeline = artifact.get('pipeline')
    metadata = artifact.get('metadata')
    if pipeline is None or not isinstance(metadata, dict):
        raise ValueError('The risk artifact is missing its pipeline or metadata.')

    expected_features = list(FEATURES)
    declared_features = metadata.get('features')
    bundle_features = artifact.get('feature_contract')
    fitted_features = getattr(pipeline.named_steps.get('imputer'), 'feature_names_in_', None)
    if not isinstance(declared_features, list) or declared_features != expected_features:
        raise ValueError('The risk artifact feature contract is incompatible with the explainability feature contract.')
    if bundle_features is not None and list(bundle_features) != expected_features:
        raise ValueError('The risk artifact feature contract is incompatible with the explainability feature contract.')
    if fitted_features is not None and list(fitted_features) != expected_features:
        raise ValueError('The fitted risk pipeline feature contract is incompatible with explainability.')
    if getattr(pipeline, 'n_features_in_', len(expected_features)) != len(expected_features):
        raise ValueError('The fitted risk pipeline input width is incompatible with explainability.')
    classifier = pipeline.named_steps.get('classifier')
    if classifier is None or not callable(getattr(classifier, 'predict_proba', None)):
        raise ValueError('The risk artifact classifier does not support probability explanations.')
    return pipeline, classifier


def _explain_class_probability(classifier: Any, background: np.ndarray, rows: np.ndarray, class_index: int) -> shap.Explanation:
    def class_probability(masked_rows: np.ndarray) -> np.ndarray:
        return classifier.predict_proba(masked_rows)[:, class_index]

    explainer = shap.Explainer(class_probability, background, feature_names=FEATURES, algorithm='permutation')
    return explainer(rows, max_evals=2 * len(FEATURES) + 1)


def _explain(frame: pd.DataFrame, artifact_path: Path = DEFAULT_ARTIFACT, target_class: str | None = None) -> tuple[dict[str, Any], np.ndarray]:
    artifact = load_artifact(artifact_path)
    pipeline, classifier = _validated_pipeline(artifact)
    transformed = pipeline.named_steps['imputer'].transform(frame[FEATURES])
    classes = [str(label).upper() for label in artifact['label_encoder'].inverse_transform(np.asarray(classifier.classes_, dtype=int))]
    probabilities = classifier.predict_proba(transformed)[0]
    target_class = target_class.upper() if target_class else classes[int(np.argmax(probabilities))]
    if target_class not in classes:
        raise ValueError(f'Unknown SHAP target class: {target_class}')
    target_index = classes.index(target_class)
    values = _explain_class_probability(classifier, artifact['background'], transformed, target_index)
    contributions = np.asarray(values.values)[0]
    base_value = float(np.asarray(values.base_values).reshape(-1)[0])
    return {
        'target': f'P({target_class})',
        'target_class': target_class,
        'base_value': base_value,
        'contributions': {name: float(value) for name, value in zip(FEATURES, contributions)},
        'feature_values': {name: float(value) for name, value in zip(FEATURES, frame.iloc[0][FEATURES])},
        'model_version': artifact['metadata']['model_version'],
    }, np.asarray(values.values)


def explain_student(features: dict[str, float], artifact_path: Path = DEFAULT_ARTIFACT, target_class: str | None = None) -> dict[str, Any]:
    missing = sorted(set(FEATURES) - set(features))
    if missing:
        raise ValueError(f'Missing model features: {", ".join(missing)}')
    frame = pd.DataFrame([{name: float(features[name]) for name in FEATURES}], columns=FEATURES)
    explanation, _ = _explain(frame, artifact_path, target_class)
    ranked = sorted(explanation['contributions'].items(), key=lambda item: abs(item[1]), reverse=True)
    explanation['summary'] = [
        f'{name.replace("_", " ").capitalize()} increased P({explanation["target_class"]}).' if contribution > 0
        else f'{name.replace("_", " ").capitalize()} reduced P({explanation["target_class"]}).' if contribution < 0
        else f'{name.replace("_", " ").capitalize()} did not change P({explanation["target_class"]}) in this explanation.'
        for name, contribution in ranked[:5]
    ]
    explanation['waterfall'] = [
        {'feature': name, 'value': explanation['feature_values'][name], 'contribution': contribution}
        for name, contribution in ranked
    ]
    return explanation


@lru_cache(maxsize=8)
def _cached_global_importance(
    row_values: tuple[tuple[float, ...], ...],
    artifact_path: str,
    modified_ns: int,
    artifact_size: int,
) -> dict[str, Any]:
    frame = pd.DataFrame(
        [dict(zip(FEATURES, values)) for values in row_values],
        columns=FEATURES,
    )
    artifact = load_artifact(Path(artifact_path))
    pipeline, classifier = _validated_pipeline(artifact)
    transformed = pipeline.named_steps['imputer'].transform(frame[FEATURES])
    predicted_classes = np.argmax(classifier.predict_proba(transformed), axis=1)
    values = np.zeros((len(frame), len(FEATURES)))
    for class_index in np.unique(predicted_classes):
        row_indices = np.flatnonzero(predicted_classes == class_index)
        explanations = _explain_class_probability(classifier, artifact['background'], transformed[row_indices], int(class_index))
        values[row_indices] = np.asarray(explanations.values)
    scores = np.mean(np.abs(values), axis=0)
    return {
        'target': 'probability of each row predicted class',
        'features': [{'name': name, 'importance': float(score)} for name, score in sorted(zip(FEATURES, scores), key=lambda item: item[1], reverse=True)],
        'model_version': artifact['metadata']['model_version'],
    }


def global_importance(rows: list[dict[str, float]], artifact_path: Path = DEFAULT_ARTIFACT) -> dict[str, Any]:
    if not rows:
        raise ValueError('At least one feature row is required')
    row_values = tuple(tuple(float(row[name]) for name in FEATURES) for row in rows)
    resolved_path = artifact_path.resolve()
    stat = resolved_path.stat()
    with _GLOBAL_IMPORTANCE_LOCK:
        result = _cached_global_importance(
            row_values,
            str(resolved_path),
            stat.st_mtime_ns,
            stat.st_size,
        )
    return {
        'target': result['target'],
        'features': [feature.copy() for feature in result['features']],
        'model_version': result['model_version'],
    }