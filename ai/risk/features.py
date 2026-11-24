from pathlib import Path

import pandas as pd

FEATURES = [
    'attendance',
    'assignments_completed',
    'activity_per_week',
    'missing_submissions',
    'attendance_change',
    'assignment_submission_rate',
]
FEATURE_CONTRACT_VERSION = 'risk-features-v2'
TARGET = 'risk_label'
TARGET_PROVENANCE = 'transparent_rule_generated_demo_label'
FEATURE_CONTRACT = {
    'attendance': {'source': 'Imported attendance observations or Student.attendance', 'meaning': 'Attendance percentage.', 'scope': 'global', 'model_input': True},
    'assignments_completed': {'source': 'Student.assignments_completed', 'meaning': 'Assignments completed count.', 'scope': 'global', 'model_input': True},
    'activity_per_week': {'source': 'Latest four AcademicMomentum(activity) values', 'meaning': 'Mean weekly LMS/portal activity.', 'scope': 'global', 'model_input': True},
    'missing_submissions': {'source': 'AssignmentStudent and Submission', 'meaning': 'Assigned submissions minus submitted, late, or graded submissions.', 'scope': 'global', 'model_input': True},
    'attendance_change': {'source': 'AcademicMomentum(attendance)', 'meaning': 'Recent attendance percent change.', 'scope': 'global', 'model_input': True},
    'assignment_submission_rate': {'source': 'Submitted, late, or graded submissions divided by assigned submissions', 'meaning': 'Assignment submission percentage.', 'scope': 'global', 'model_input': True},
    'fa1': {'source': 'StudentSubjectAcademicRecord.fa1', 'meaning': 'Marks-related formative assessment 1.', 'scope': 'subject', 'model_input': False},
    'fa2': {'source': 'StudentSubjectAcademicRecord.fa2', 'meaning': 'Marks-related formative assessment 2.', 'scope': 'subject', 'model_input': False},
    'previous_gpa': {'source': 'Student.previous_gpa', 'meaning': 'Academic GPA; excluded from model.', 'scope': 'global', 'model_input': False},
    'current_gpa': {'source': 'Student.current_gpa', 'meaning': 'Academic GPA; excluded from model.', 'scope': 'global', 'model_input': False},
    'assessment': {'source': 'Assessment observations', 'meaning': 'Academic performance score; excluded from model.', 'scope': 'global', 'model_input': False},
    'assessment_change': {'source': 'AcademicMomentum(assessment)', 'meaning': 'Assessment-derived trend; excluded from model.', 'scope': 'global', 'model_input': False},
    'momentum_score': {'source': 'Attendance and assessment trends', 'meaning': 'Academic trend derived in part from assessment marks; excluded from model.', 'scope': 'global', 'model_input': False},
}

MARKS_EXCLUSION_PATTERNS = (
    'fa1', 'fa2', 'gpa', 'cgpa', 'grade', 'mark', 'score',
    'percentage', 'assessment', 'midterm', 'final', 'examination',
)


def load_training_data(path: Path) -> tuple[pd.DataFrame, pd.Series]:
    data = pd.read_csv(path)
    missing = sorted(set(FEATURES) - set(data.columns))
    if missing:
        raise ValueError(f'Dataset is missing required columns: {", ".join(missing)}')
    features = data[FEATURES].apply(pd.to_numeric, errors='coerce')
    scores = (
        features['attendance'].map(lambda value: max(0.0, min(1.0, (85.0 - value) / 30.0)) * 35.0)
        + features['assignment_submission_rate'].map(lambda value: max(0.0, min(1.0, 1.0 - value)) * 25.0)
        + features['missing_submissions'].map(lambda value: max(0.0, min(1.0, value / 2.0)) * 20.0)
        + features['activity_per_week'].map(lambda value: max(0.0, min(1.0, (12.0 - value) / 12.0)) * 15.0)
        + features['attendance_change'].map(lambda value: max(0.0, min(1.0, -value / 10.0)) * 5.0)
    )
    low_cutoff = float(scores.quantile(1 / 3))
    high_cutoff = float(scores.quantile(2 / 3))
    labels = scores.map(
        lambda score: 'HIGH' if score > high_cutoff else 'MEDIUM' if score > low_cutoff else 'LOW'
    )
    if labels.nunique() != 3:
        raise ValueError('The transparent rule did not produce all three required risk classes')
    if features.dropna(how='all').empty:
        raise ValueError('Training features contain no numeric observations')
    return features, labels