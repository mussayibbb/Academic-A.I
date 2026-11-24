
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ai.risk.features import FEATURES

DEFAULT_EARLY_WARNING_DATA = (
    Path(__file__).resolve().parents[3]
    / 'data'
    / 'processed'
    / 'DSstudents_processed.csv'
)


@dataclass(frozen=True)
class EarlyWarningCsvStudent:
    student_id: str
    features: dict[str, float]
    assignment_submission_rate: float | None
    name: str | None
    usn: str | None
    department: str | None
    course: str | None
    branch: str | None
    semester: str | None
    data_origin: str = 'DEMO DATA'


def _optional_text(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def load_early_warning_csv(
    path: Path = DEFAULT_EARLY_WARNING_DATA,
) -> list[EarlyWarningCsvStudent]:
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise ValueError(
            f'Could not read the Early Warning DEMO / TEST DATA CSV: {exc}'
        ) from exc

    required = {'student_id', 'data_origin', *FEATURES}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f'Early Warning CSV is missing required columns: {", ".join(missing)}.'
        )
    if frame.empty:
        return []

    origins = frame['data_origin'].fillna('').astype(str).str.strip()
    if not origins.eq('DEMO DATA').all():
        raise ValueError(
            'Early Warning CSV must contain only rows explicitly labelled DEMO DATA.'
        )

    student_ids = frame['student_id'].fillna('').astype(str).str.strip()
    if student_ids.eq('').any():
        raise ValueError('Early Warning CSV contains an empty student_id.')
    if student_ids.duplicated().any():
        raise ValueError('Early Warning CSV contains duplicate student_id values.')

    numeric_features: dict[str, pd.Series] = {}
    for feature in FEATURES:
        values = pd.to_numeric(frame[feature], errors='coerce')
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(
                f'Early Warning CSV contains a missing or invalid {feature} value.'
            )
        numeric_features[feature] = values

    submission_rates = numeric_features['assignment_submission_rate']
    if not submission_rates.between(0.0, 1.0).all():
        raise ValueError(
            'Early Warning CSV contains an invalid assignment_submission_rate value.'
        )

    records: list[EarlyWarningCsvStudent] = []
    for index, student_id in enumerate(student_ids):
        records.append(
            EarlyWarningCsvStudent(
                student_id=student_id,
                features={
                    feature: float(numeric_features[feature].iloc[index])
                    for feature in FEATURES
                },
                assignment_submission_rate=float(
                    submission_rates.iloc[index]
                ),
                name=_optional_text(frame.iloc[index].get('name')),
                usn=_optional_text(frame.iloc[index].get('usn')),
                department=_optional_text(frame.iloc[index].get('department')),
                course=_optional_text(frame.iloc[index].get('course')),
                branch=_optional_text(frame.iloc[index].get('branch')),
                semester=_optional_text(frame.iloc[index].get('semester')),
            )
        )

    return records
