from datetime import date, datetime, timedelta, timezone
import hashlib
import csv
import io
import json
import logging
import math
import mimetypes
import re
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from ai.risk.features import FEATURE_CONTRACT_VERSION, FEATURES
from ai.explainability.service import explain_student, global_importance
from ai.llm.grounding import validate_intervention_draft, validate_risk_summary_selection
from ai.llm.provider import (
    InterventionContext,
    RecoveryTutorContext,
    RiskSummaryContext,
    RiskSummaryEvidence,
    build_provider,
)
from ai.risk.predict import DEFAULT_ARTIFACT, load_artifact, predict as predict_risk, predict_many as predict_many_risks
from ai.rag.index import retrieve, retrieve_scoped
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, func, inspect, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

from backend.app.api.auth import router as auth_router
from backend.app.api.recovery_workflow import router as recovery_workflow_router
from backend.app.config import BASE_DIR, get_settings
from backend.app.core.security import CurrentAdmin, CurrentStudent, CurrentTeacher, CurrentUser, get_current_user
from backend.app.database import get_db
from backend.app.early_warning_config import (
    HIGH_PRIORITY_THRESHOLD,
    MEDIUM_PRIORITY_THRESHOLD,
    MINIMUM_USABLE_SIGNALS,
    normalise_positive_shap,
    priority_for_probability,
)
from backend.app.models import (
    AcademicMomentum,
    AcademicDataImportBatch,
    AcademicDataImportRow,
    AcademicRiskOutcome,
    ApprovedTopic,
    Assessment,
    AssessmentAttempt,
    AssessmentAttemptAnswer,
    AssessmentQuestion,
    Assignment,
    AssignmentStudent,
    Attendance,
    AuditLog,
    Course,
    Intervention,
    InterventionDraft,
    InterventionReview,
    LearningAssessment,
    LearningStudySession,
    ModelDeploymentEvent,
    ModelDeploymentState,
    ModelRegistryEntry,
    Notification,
    Phase3PolicyApproval,
    Mark,
    RiskPrediction,
    RiskSnapshot,
    RecoveryAdaptationDecision,
    RecoveryTask,
    RecoveryTaskEvent,
    RubricCriterion,
    Rubric,
    RubricScore,
    Student,
    StudentSubjectAcademicRecord,
    StudentSubjectAcademicRecordHistory,
    SubjectFocusPolicy,
    Subject,
    SubjectModule,
    SubjectModuleResource,
    Submission,
    SubmissionVersion,
    SupportAction,
    SupportFollowUp,
    SupportPlanVersion,
    Teacher,
    TopicLearningAssignment,
    User,
)
from backend.app.schemas import AssignmentCreate, AssignmentOut, AssignmentStatusUpdate, GradeRequest, RubricCreate, StudentOut, StudentSelfOut, SubmissionOut
from backend.app.schemas.academic import Fa1MaximumEvidenceReview, SubjectAcademicRecordUpdate
from backend.app.schemas.students import StudentCreate, StudentCsvImportResult, StudentRecordOut, StudentStatsOut, StudentUpdate
from backend.app.schemas.interventions import InterventionEditRequest, InterventionGenerateRequest
from backend.app.schemas.learning import (
    AssessmentAttemptSubmit,
    LearningAnswerReview,
    LearningAssessmentReview,
    SubjectModuleCreate,
    SubjectModuleResourceOut,
    SubjectModuleUpdate,
    TopicApprovalRequest,
    TopicAssignmentCreate,
    TopicAssignmentOut,
)
from backend.app.services.learning_assessments import validate_assessment_response, validate_short_answer_evaluation, validate_topic_map_response
from backend.app.services.student_records import (
    ALLOWED_ORIGINS as STUDENT_IMPORT_ORIGINS,
    MAX_FILE_BYTES as STUDENT_IMPORT_MAX_FILE_BYTES,
    StudentRecordConflict,
    StudentRecordImportError,
    import_student_records,
)
from backend.app.schemas.recovery import (
    RecoveryActivity,
    RecoveryPlanDecisionRequest,
    RecoveryPlanEditRequest,
    RecoveryPlanGenerateRequest,
    RecoveryPlanOut,
    RecoveryBlueprintEditRequest,
    RecoveryBlueprintGenerateRequest,
    RecoveryAdaptationDecisionOut,
    RecoveryTutorModelResponse,
    RecoveryTutorOut,
    RecoveryTutorRequest,
    RecoveryTaskAssessmentRequest,
    RecoveryTaskEventOut,
    RecoveryTaskOut,
    RecoveryTaskProgressRequest,
    RecoveryTaskActivityEditRequest,
    RecoveryTaskActivityApprovalRequest,
    RecoveryTaskDraftSaveRequest,
    RecoveryTaskSubmitRequest,
)
from backend.app.schemas.risk import ModelApprovalRequest, ModelPromotionRequest, ModelRegistryEntryInput, ModelRejectionRequest, ModelRollbackRequest, Phase3PolicyApprovalRequest, Phase3TrainingRequest, RiskFeatureInput, RiskSimulationInput
from backend.app.schemas.risk_overview import RiskOverviewOut
from backend.app.schemas.subject_focus import SubjectFocusPolicyUpdate
from backend.app.services.academic_records import SUBJECTS, academic_periods, academic_totals, build_register_row, normalize_academic_period, selected_academic_period, serialize_record, subject_record_evidence, subject_records_by_student
from backend.app.services.risk_overview import build_teacher_risk_overview
from backend.app.services.subject_focus import (
    build_student_subject_focus,
    build_teacher_subject_focus,
    current_subject_focus_policy,
    evaluate_subject_assessments,
    serialize_subject_focus_policy,
)
from backend.app.services.uploads import store_submission
from backend.app.services.momentum import calculate_momentum
from backend.app.services.recovery import (
    PLAN_APPROVED,
    PLAN_DRAFT,
    PLAN_PUBLISHED,
    PLAN_REJECTED,
    TASK_AVAILABLE,
    TASK_COMPLETED,
    TASK_DRAFT,
    TASK_IN_PROGRESS,
    TASK_NEEDS_REVISION,
    TASK_SUBMITTED,
    TASK_SUPERSEDED,
    add_task_event,
    build_recovery_evidence,
    build_recovery_blueprint_schedule,
    create_draft,
    edit_draft,
    legacy_recovery_activity,
    publish_plan,
    record_adaptation_decision,
    stable_generation_key,
    validate_recovery_blueprint_response,
    validate_recovery_tutor_response,
)
from backend.app.services.model_deployment import (
    DeploymentArtifactError,
    active_artifact_path,
    candidate_artifact_path,
    independent_candidate_approval_error,
    sha256_file,
    verify_candidate_artifact,
)
from backend.app.services.academic_imports import ImportValidationError, import_academic_data
from backend.app.services.early_warning_snapshots import weekly_snapshot_trend
from ai.risk.academic_dataset import DATASET_FIELDS, build_observation_rows, readiness_report
from ai.risk.phase3 import (
    DATASET_FIELDS as PHASE3_DATASET_FIELDS,
    FEATURE_SNAPSHOT_AGGREGATION_STRATEGY,
    build_phase3_dataset,
)
from ai.risk.candidate import evaluate_common_test_set, explain_candidate, train_candidate

logger = logging.getLogger(__name__)

settings = get_settings()
LEARNING_ASSESSMENT_QUESTION_MARKS = 5
LEARNING_ASSESSMENT_TOTAL_MARKS = 100


class EarlyWarningSimulationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recoveries: dict[str, float] = Field(default_factory=dict)


class MeetingInterventionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    studentId: int
    slotStart: datetime
    durationMin: int = Field(ge=5, le=240)
    note: str = Field(default='', max_length=2000)


class TaskInterventionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    studentId: int
    taskType: str = Field(min_length=1, max_length=100)
    dueAt: datetime
    note: str = Field(default='', max_length=2000)


class MentorAlertRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    studentId: int
    message: str = Field(min_length=1, max_length=2000)


class SubjectRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    studentId: int
    subjectId: int | None = None


def _institutional_today() -> date:
    return datetime.now(ZoneInfo(settings.institutional_timezone)).date()


def _attempt_maximum_marks(attempt: AssessmentAttempt) -> float:
    questions = (attempt.question_set or {}).get('questions', [])
    return sum(
        float(question['marks'])
        for question in questions
        if isinstance(question, dict)
        and isinstance(question.get('marks'), (int, float))
        and not isinstance(question.get('marks'), bool)
        and question['marks'] > 0
    )
CANDIDATE_ARTIFACT_DIRECTORY = BASE_DIR / 'models' / 'candidates'
DEMO_PREVIEW_ARTIFACT = CANDIDATE_ARTIFACT_DIRECTORY / 'risk_model_candidate.joblib'
app = FastAPI(title=settings.app_name, version='2.0.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
    allow_headers=['Authorization', 'Content-Type'],
)
app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(recovery_workflow_router, prefix=settings.api_v1_prefix)


def audit(
    db: Session,
    user: User,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(AuditLog(
        actor_user_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        detail=detail or {},
    ))


def early_warning_teacher_scope(db: Session, user: User) -> tuple[list[int], list[str]]:
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        return {
            'summary': {'signals': 0, 'highPriority': 0, 'watchList': 0, 'recoveredThisMonth': 0, 'modelRefreshedAt': datetime.utcnow().isoformat()},
            'items': [],
            'nextPage': None,
        }
    courses = db.query(Course).filter(Course.teacher_id == teacher.id).all()
    course_ids = [item.id for item in courses]
    sections = sorted({item.section for item in courses if item.section})
    return course_ids, sections


def early_warning_authorized_student(db: Session, user: User, student_id: int) -> Student:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found.')
    course_ids, sections = early_warning_teacher_scope(db, user)
    authorized = (
        (student.course_id is not None and student.course_id in course_ids)
        or (student.course_id is None and student.section in sections)
    )
    if not authorized:
        raise HTTPException(status_code=403, detail='Student is outside the teacher’s assigned scope.')
    return student


def phase3_audit(
    db: Session,
    user: User,
    action: str,
    *,
    result: str,
    reason: str | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    dataset_fingerprint: str | None = None,
    model_version: str | None = None,
    resource_id: int | None = None,
    previous_state: dict[str, Any] | None = None,
    new_state: dict[str, Any] | None = None,
) -> None:
    db.add(AuditLog(
        actor_user_id=user.id,
        action=action,
        resource_type='phase3_model_governance',
        resource_id=str(resource_id) if resource_id is not None else model_version,
        detail={
            'result': result,
            'reason': reason,
            'dataset_id': dataset_id,
            'dataset_version': dataset_version,
            'dataset_fingerprint': dataset_fingerprint,
            'model_version': model_version,
            'actor_role': user.role.upper(),
            'previous_state': previous_state,
            'new_state': new_state,
        },
    ))
    db.commit()


def phase3_public_report(dataset: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in dataset.items() if key not in {'rows', 'rejected_rows'}}


def student_user_id(db: Session, student_id: int) -> int:
    student = db.query(Student.user_id).filter(Student.id == student_id).first()
    return student[0] if student and student[0] else -1


def normalize_support_status(value: Any, *, default: str = 'PENDING_FACULTY_REVIEW') -> str:
    if value is None:
        return default
    text = str(value).strip().upper().replace(' ', '_')
    aliases = {
        'PENDING_REVIEW': 'PENDING_FACULTY_REVIEW',
        'PENDING_FACULTY_REVIEW': 'PENDING_FACULTY_REVIEW',
        'IN_PROGRESS': 'IN_PROGRESS',
        'PROGRESS': 'IN_PROGRESS',
        'COMPLETED': 'COMPLETED',
        'DONE': 'COMPLETED',
        'CANCELLED': 'CANCELLED',
        'CANCELED': 'CANCELLED',
        'MISSED': 'MISSED',
        'OVERDUE': 'OVERDUE',
        'SCHEDULED': 'SCHEDULED',
        'APPROVED': 'APPROVED',
        'REJECTED': 'REJECTED',
        'DRAFT': 'DRAFT',
    }
    return aliases.get(text, text)


MODULE_ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.pptx', '.txt'}


def _teacher_subject_access(db: Session, *, teacher_user: User, subject_id: int) -> tuple[Subject, Teacher]:
    teacher = db.query(Teacher).filter(Teacher.user_id == teacher_user.id).first()
    if teacher is None:
        raise HTTPException(status_code=403, detail='Teacher profile not found')
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if subject is None:
        raise HTTPException(status_code=404, detail='Subject not found')
    connected = db.query(Course).filter(Course.subject_id == subject.id, Course.teacher_id == teacher.id).first()
    if connected is None:
        raise HTTPException(status_code=403, detail='Teacher is not authorized for this subject')
    return subject, teacher


def _extract_document_text(file_path: Path, file_type: str) -> tuple[str, list[dict[str, Any]], str | None]:
    def escape_source_location_marker(value: str) -> str:
        return value.replace('[[[SOURCE_PAGE:', '[[[SOURCE PAGE:').replace('[[[SOURCE_SLIDE:', '[[[SOURCE SLIDE:')

    try:
        if file_type == '.txt':
            text = file_path.read_text(encoding='utf-8', errors='replace')
            return text.strip(), [], None
        if file_type == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(str(file_path))
            pages = []
            for index, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ''
                if page_text.strip():
                    pages.append(f'[[[SOURCE_PAGE:{index}]]]\n{escape_source_location_marker(page_text.strip())}')
            text = '\n\n'.join(pages)
            if not text.strip():
                raise ValueError('PDF file was uploaded but no readable text could be extracted.')
            return text, [], None
        if file_type == '.docx':
            from docx import Document
            document = Document(str(file_path))
            paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text and paragraph.text.strip()]
            text = '\n\n'.join(paragraphs)
            if not text.strip():
                raise ValueError('DOCX file was uploaded but no readable text could be extracted.')
            return text, [], None
        if file_type == '.pptx':
            try:
                from pptx import Presentation
            except ImportError as exc:
                raise ValueError('PPTX support is not available in the current environment.') from exc
            presentation = Presentation(str(file_path))
            slides = []
            for index, slide in enumerate(presentation.slides, start=1):
                lines = []
                for shape in slide.shapes:
                    if hasattr(shape, 'text') and shape.text:
                        lines.append(escape_source_location_marker(shape.text.strip()))
                text = '\n'.join(line for line in lines if line)
                if text:
                    slides.append(f'[[[SOURCE_SLIDE:{index}]]]\n{text}')
            combined = '\n\n'.join(slides)
            if not combined.strip():
                raise ValueError('PPTX file was uploaded but the slides contain no readable text.')
            return combined, [], None
        raise ValueError(f'Unsupported document type: {file_type}')
    except Exception as exc:  # pragma: no cover - the specific parsing error is surfaced to the caller.
        return '', [], str(exc)


def _merge_topic_candidate_maps(
    previous: list[dict[str, Any]],
    refreshed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refreshed_by_id = {
        item.get('candidate_id'): item
        for item in refreshed
        if isinstance(item, dict) and isinstance(item.get('candidate_id'), str)
    }
    merged: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for prior in previous:
        if not isinstance(prior, dict):
            continue
        candidate_id = prior.get('candidate_id')
        current = refreshed_by_id.get(candidate_id)
        if current is None:
            omitted = dict(prior)
            omitted['requires_review'] = True
            omitted['review_reason'] = '; '.join(filter(None, [
                omitted.get('review_reason'),
                'Candidate was omitted from the repair response; confirm whether it belongs in the syllabus.',
            ]))[:1000]
            omitted['children'] = _merge_topic_candidate_maps(
                list(prior.get('children') or []),
                [],
            )
            merged.append(omitted)
            continue
        consumed.add(candidate_id)
        combined = {**prior, **current}
        combined['order_uncertain'] = bool(prior.get('order_uncertain') or current.get('order_uncertain'))
        combined['hierarchy_uncertain'] = bool(prior.get('hierarchy_uncertain') or current.get('hierarchy_uncertain'))
        combined['requires_review'] = bool(prior.get('requires_review') or current.get('requires_review'))
        reasons = list(dict.fromkeys(filter(None, [
            prior.get('review_reason'),
            current.get('review_reason'),
        ])))
        combined['review_reason'] = '; '.join(reasons)[:1000] or None
        combined['children'] = _merge_topic_candidate_maps(
            list(prior.get('children') or []),
            list(current.get('children') or []),
        )
        merged.append(combined)
    merged.extend(
        item for item in refreshed
        if isinstance(item, dict) and item.get('candidate_id') not in consumed
    )
    return sorted(
        merged,
        key=lambda item: item.get('source_order') if isinstance(item.get('source_order'), int) else 999,
    )


def coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def student_feature_batch(
    students: list[Student],
    db: Session,
) -> tuple[dict[int, dict[str, float]], dict[int, dict[str, dict[str, Any]]]]:
    if not students:
        return {}, {}

    student_ids = [student.id for student in students]
    grouped: dict[int, dict[str, list[float]]] = {}
    imported_attendance: dict[int, list[float]] = {}
    observations = db.query(AcademicMomentum).filter(
        AcademicMomentum.student_id.in_(student_ids),
    ).order_by(
        AcademicMomentum.student_id,
        AcademicMomentum.observed_at,
        AcademicMomentum.id,
    ).all()
    for observation in observations:
        metrics = grouped.setdefault(observation.student_id, {})
        metrics.setdefault(observation.metric, []).append(observation.value)
        if observation.import_batch_id is not None:
            if observation.metric == 'attendance':
                imported_attendance.setdefault(observation.student_id, []).append(observation.value)

    manual_attendance: dict[int, float] = {}
    manual_attendance_rows = db.query(
        AcademicMomentum.student_id,
        AcademicMomentum.value,
    ).join(
        AcademicDataImportBatch,
        AcademicDataImportBatch.id == AcademicMomentum.import_batch_id,
    ).filter(
        AcademicMomentum.student_id.in_(student_ids),
        AcademicMomentum.metric == 'attendance',
        AcademicDataImportBatch.dataset_type == 'student_record_update',
    ).order_by(
        AcademicMomentum.observed_at.desc(),
        AcademicMomentum.id.desc(),
    ).all()
    for student_id, value in manual_attendance_rows:
        manual_attendance.setdefault(student_id, value)

    assigned_counts = dict(db.query(
        AssignmentStudent.student_id,
        func.count(AssignmentStudent.id),
    ).filter(
        AssignmentStudent.student_id.in_(student_ids),
    ).group_by(AssignmentStudent.student_id).all())
    submitted_counts = dict(db.query(
        Submission.student_id,
        func.count(Submission.id),
    ).filter(
        Submission.student_id.in_(student_ids),
        Submission.status.in_(['submitted', 'late', 'graded']),
    ).group_by(Submission.student_id).all())

    features_by_student: dict[int, dict[str, float]] = {}
    momentum_by_student: dict[int, dict[str, dict[str, Any]]] = {}
    for student in students:
        metrics = grouped.get(student.id, {})
        trends = {metric: calculate_momentum(values) for metric, values in metrics.items()}
        attendance_trend = trends.get('attendance', calculate_momentum([]))
        activities = metrics.get('activity', [])
        attendance = imported_attendance.get(student.id, [])
        assigned = assigned_counts.get(student.id, 0)
        submitted = submitted_counts.get(student.id, 0)
        features_by_student[student.id] = {
            'attendance': manual_attendance.get(
                student.id,
                sum(attendance) / len(attendance) if attendance else student.attendance,
            ),
            'assignments_completed': student.assignments_completed,
            'activity_per_week': sum(activities[-4:]) / max(1, len(activities[-4:])),
            'missing_submissions': float(max(0, assigned - submitted)),
            'attendance_change': float(attendance_trend['percent_change'] or 0),
            'assignment_submission_rate': float(submitted / max(assigned, 1)),
        }
        momentum_by_student[student.id] = trends
    return features_by_student, momentum_by_student


def student_features(student: Student, db: Session) -> dict[str, float]:
    features, _ = student_feature_batch([student], db)
    return features[student.id]


def student_risk_data(
    student: Student,
    features: dict[str, float],
    prediction: dict[str, Any],
    *,
    risk_status: str = 'available',
    risk_status_message: str | None = None,
) -> dict[str, Any]:
    student_data = StudentOut.model_validate(student).model_dump()
    student_data.update({
        'attendance': features['attendance'],
        'assignments_completed': features['assignments_completed'],
        'missing_submissions': features['missing_submissions'],
        'assignment_submission_rate': features['assignment_submission_rate'],
        'activity_per_week': features['activity_per_week'],
        'risk_level': prediction['risk_level'],
        'risk_probability': prediction['risk_probability'],
        'risk_probability_semantics': prediction['risk_probability_semantics'],
        'class_probabilities': prediction['class_probabilities'],
        'risk_status': risk_status,
        'risk_status_message': risk_status_message,
    })
    return student_data


def student_model_signal_counts(students: list[Student], db: Session) -> dict[int, int]:
    if not students:
        return {}
    student_ids = [student.id for student in students]
    momentum_counts: dict[int, dict[str, int]] = {}
    for student_id, metric, count in db.query(
        AcademicMomentum.student_id,
        AcademicMomentum.metric,
        func.count(AcademicMomentum.id),
    ).filter(
        AcademicMomentum.student_id.in_(student_ids),
        AcademicMomentum.metric.in_(['attendance', 'activity']),
    ).group_by(
        AcademicMomentum.student_id,
        AcademicMomentum.metric,
    ).all():
        momentum_counts.setdefault(student_id, {})[metric] = count
    attendance_counts = dict(db.query(
        Attendance.student_id,
        func.count(Attendance.id),
    ).filter(Attendance.student_id.in_(student_ids)).group_by(Attendance.student_id).all())
    assignment_counts = dict(db.query(
        AssignmentStudent.student_id,
        func.count(AssignmentStudent.id),
    ).filter(AssignmentStudent.student_id.in_(student_ids)).group_by(AssignmentStudent.student_id).all())
    submission_counts = dict(db.query(
        Submission.student_id,
        func.count(Submission.id),
    ).filter(Submission.student_id.in_(student_ids)).group_by(Submission.student_id).all())

    counts: dict[int, int] = {}
    for student in students:
        momentum = momentum_counts.get(student.id, {})
        has_attendance = (
            student.attendance > 0
            or momentum.get('attendance', 0) > 0
            or attendance_counts.get(student.id, 0) > 0
        )
        has_assignments = (
            student.assignments_completed > 0
            or assignment_counts.get(student.id, 0) > 0
        )
        has_activity = momentum.get('activity', 0) > 0
        has_attendance_history = momentum.get('attendance', 0) >= 2
        has_submissions = submission_counts.get(student.id, 0) > 0
        counts[student.id] = sum((
            has_attendance,
            has_assignments,
            has_assignments,
            has_activity,
            has_attendance_history,
            has_submissions,
        ))
    return counts


def student_risk_availability(
    student: Student,
    db: Session,
    *,
    demo_model: bool | None = None,
    usable_signals: int | None = None,
) -> tuple[str, str | None]:
    if demo_model is None:
        demo_model = early_warning_uses_demo_model(db)
    if demo_model and student.data_origin != 'DEMO DATA':
        return 'unavailable', 'The active model is trained on DEMO DATA and cannot assess this non-demo or unverified record.'
    if usable_signals is None:
        usable_signals = student_model_signal_counts([student], db)[student.id]
    if usable_signals < MINIMUM_USABLE_SIGNALS:
        return 'insufficient-data', 'There are not enough model-supported signals to produce a risk prediction.'
    return ('unvalidated-demo-model', 'Unvalidated preview from a model trained on DEMO DATA.') if demo_model else ('available', None)


def student_risk_views_from_features(
    students: list[Student],
    features_by_student: dict[int, dict[str, float]],
    db: Session,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not students:
        return []
    demo_model = early_warning_uses_demo_model(db)
    signal_counts = student_model_signal_counts(students, db)
    scorable_students = [
        student for student in students
        if signal_counts[student.id] >= MINIMUM_USABLE_SIGNALS
        and (not demo_model or student.data_origin == 'DEMO DATA')
    ]
    predictions_by_student: dict[int, dict[str, Any]] = {}
    try:
        if scorable_students:
            artifact_path = demo_preview_artifact_path() if demo_model else selected_model_artifact(db)
            predictions = predict_many_risks(
                [features_by_student[student.id] for student in scorable_students],
                artifact_path,
            )
            if len(predictions) != len(scorable_students):
                raise HTTPException(status_code=503, detail='The active risk model did not return one prediction per scorable student.')
            predictions_by_student = {
                student.id: prediction
                for student, prediction in zip(scorable_students, predictions)
            }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail='Risk model is not trained. Run the documented training command first.') from exc

    risk_views: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for student in students:
        prediction = predictions_by_student.get(student.id)
        if prediction is None:
            status, reason = student_risk_availability(
                student,
                db,
                demo_model=demo_model,
                usable_signals=signal_counts[student.id],
            )
            unavailable = {
                'risk_level': 'UNAVAILABLE',
                'risk_probability': 0.0,
                'risk_probability_semantics': 'unavailable',
                'class_probabilities': {},
                'model_version': None,
            }
            unavailable['risk_probability_semantics'] = status
            risk_views.append((
                student_risk_data(
                    student,
                    features_by_student[student.id],
                    unavailable,
                    risk_status=status,
                    risk_status_message=reason,
                ),
                unavailable,
            ))
            continue
        risk_views.append((
            student_risk_data(
                student,
                features_by_student[student.id],
                prediction,
                risk_status='unvalidated-demo-model' if demo_model else 'available',
                risk_status_message='Unvalidated preview from a model trained on DEMO DATA.' if demo_model else None,
            ),
            prediction,
        ))
    return risk_views


def student_risk_views(
    students: list[Student],
    db: Session,
) -> tuple[
    list[tuple[dict[str, Any], dict[str, Any]]],
    dict[int, dict[str, float]],
    dict[int, dict[str, dict[str, Any]]],
]:
    features_by_student, momentum_by_student = student_feature_batch(students, db)
    risk_views = student_risk_views_from_features(students, features_by_student, db)
    return risk_views, features_by_student, momentum_by_student


def student_risk_view(student: Student, db: Session) -> tuple[dict[str, Any], dict[str, Any]]:
    risk_views, _, _ = student_risk_views([student], db)
    return risk_views[0]


def model_feature_schema() -> list[str]:
    try:
        artifact = load_artifact()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=503, detail='The production risk model or its feature metadata is unavailable.') from exc
    metadata = artifact.get('metadata', {})
    features = metadata.get('features')
    if not isinstance(features, list):
        features = getattr(artifact.get('pipeline'), 'feature_names_in_', None)
        features = features.tolist() if features is not None else None
    if not isinstance(features, list):
        raise HTTPException(status_code=503, detail='The production model feature contract does not match the supported prediction contract.')
    return [str(name) for name in features]


def production_model_registry_entry() -> dict[str, Any]:
    try:
        artifact = load_artifact()
        artifact_hash = hashlib.sha256(DEFAULT_ARTIFACT.read_bytes()).hexdigest()
    except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail='The production risk model is unavailable for registry inspection.') from exc
    metadata = artifact.get('metadata', {})
    dataset_path = metadata.get('dataset')
    return {
        'model_id': 'risk-production',
        'model_version': metadata.get('model_version'),
        'model_type': 'classification',
        'feature_contract_version': FEATURE_CONTRACT_VERSION,
        'feature_contract': model_feature_schema(),
        'training_dataset_version': Path(dataset_path).name if isinstance(dataset_path, str) and dataset_path else None,
        'training_dataset_hash': metadata.get('training_dataset_hash'),
        'training_provenance': {
            'source': Path(dataset_path).name if isinstance(dataset_path, str) and dataset_path else None,
            'demo_data': metadata.get('demo_data'),
            'training_rows': metadata.get('training_rows'),
            'evaluation_rows': metadata.get('evaluation_rows'),
        },
        'training_timestamp': metadata.get('trained_at'),
        'validation_metrics': metadata.get('evaluation'),
        'artifact_identifier': DEFAULT_ARTIFACT.name,
        'artifact_hash': artifact_hash,
        'status': 'production',
        'created_at': None,
        'notes': None,
    }


def selected_model_artifact(db: Session) -> Path:
    try:
        production = production_model_registry_entry()
        return active_artifact_path(
            db,
            candidate_directory=CANDIDATE_ARTIFACT_DIRECTORY,
            production_path=DEFAULT_ARTIFACT,
            production_version=str(production['model_version']),
            production_hash=str(production['artifact_hash']),
        )
    except DeploymentArtifactError as exc:
        raise HTTPException(status_code=503, detail=f'Active local model deployment is unsafe or unavailable: {exc}') from exc


def early_warning_uses_demo_model(db: Session) -> bool:
    try:
        artifact = load_artifact(selected_model_artifact(db))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail='The active risk model artifact is unavailable.') from exc
    return artifact.get('metadata', {}).get('demo_data') is True


def demo_preview_artifact_path() -> Path:
    if not DEMO_PREVIEW_ARTIFACT.is_file():
        raise HTTPException(status_code=503, detail='The DEMO / RULE-GENERATED preview candidate artifact is unavailable.')
    artifact = load_artifact(DEMO_PREVIEW_ARTIFACT)
    metadata = artifact.get('metadata', {})
    contract = artifact.get('feature_contract')
    if contract is not None and list(contract) != FEATURES:
        raise HTTPException(status_code=503, detail='The DEMO preview candidate feature contract is incompatible with the live marks-free contract.')
    if metadata.get('target_label_provenance') != 'transparent_rule_generated_demo_label':
        raise HTTPException(status_code=503, detail='The DEMO preview candidate does not identify rule-generated training provenance.')
    if metadata.get('demo_data') is not True:
        raise HTTPException(status_code=503, detail='The preview candidate is not explicitly marked as DEMO / TEST DATA.')
    return DEMO_PREVIEW_ARTIFACT


def predict_with_demo_preview(features: dict[str, float]) -> dict[str, Any]:
    return predict_risk(features, demo_preview_artifact_path())


def explain_with_demo_preview(features: dict[str, float], *, target_class: str | None = None) -> dict[str, Any]:
    return explain_student(features, demo_preview_artifact_path(), target_class=target_class)


def predict_with_active_model(features: dict[str, float], db: Session) -> dict[str, Any]:
    artifact_path = selected_model_artifact(db)
    return predict_risk(features) if artifact_path.resolve() == DEFAULT_ARTIFACT.resolve() else predict_risk(features, artifact_path)


def high_class_probability(prediction: dict[str, Any]) -> float:
    class_probabilities = prediction.get('class_probabilities')
    if not isinstance(class_probabilities, dict) or 'HIGH' not in class_probabilities:
        raise HTTPException(status_code=503, detail='Active risk model did not return the HIGH-class probability.')
    try:
        probability = float(class_probabilities['HIGH'])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail='Active risk model returned an invalid HIGH-class probability.') from exc
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise HTTPException(status_code=503, detail='Active risk model returned an invalid HIGH-class probability.')
    return probability


def explain_with_active_model(features: dict[str, float], db: Session, *, target_class: str | None = None) -> dict[str, Any]:
    artifact_path = selected_model_artifact(db)
    return explain_student(features, target_class=target_class) if artifact_path.resolve() == DEFAULT_ARTIFACT.resolve() else explain_student(features, artifact_path, target_class=target_class)


def validate_simulation_scenario(scenario: dict[str, float]) -> dict[str, float]:
    supported = set(model_feature_schema())
    unsupported = sorted(set(scenario) - supported)
    if unsupported:
        raise HTTPException(status_code=422, detail={'message': 'Unsupported model feature(s).', 'features': unsupported})
    if not scenario:
        raise HTTPException(status_code=422, detail='Provide at least one supported model feature in the scenario payload.')
    bounds = {
        'attendance': (0.0, 100.0),
        'assignments_completed': (0.0, None),
        'activity_per_week': (0.0, None),
        'missing_submissions': (0.0, None),
        'attendance_change': (None, None),
        'assignment_submission_rate': (0.0, 1.0),
    }
    validated: dict[str, float] = {}
    for feature, raw_value in scenario.items():
        value = float(raw_value)
        if not math.isfinite(value):
            raise HTTPException(status_code=422, detail=f'{feature} must be a finite number.')
        minimum, maximum = bounds.get(feature, (None, None))
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            range_description = f'{minimum} to {maximum}' if maximum is not None else f'at least {minimum}'
            raise HTTPException(status_code=422, detail=f'{feature} must be {range_description}.')
        validated[feature] = value
    return validated


def simulate_student_risk(student: Student | None, db: Session, scenario: dict[str, float]) -> dict[str, Any]:
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    baseline_features = student_features(student, db)
    base_prediction = predict_with_active_model(baseline_features, db)
    scenario_values = validate_simulation_scenario(scenario)
    candidate = dict(baseline_features)
    candidate.update(scenario_values)
    simulated_prediction = predict_with_active_model(candidate, db)
    class_names = sorted(set(base_prediction['class_probabilities']) | set(simulated_prediction['class_probabilities']))
    probability_deltas = {
        name: round(simulated_prediction['class_probabilities'].get(name, 0.0) - base_prediction['class_probabilities'].get(name, 0.0), 8)
        for name in class_names
    }
    changed_features = [
        {'name': name, 'baseline_value': baseline_features[name], 'simulated_value': candidate[name]}
        for name in FEATURES
        if baseline_features[name] != candidate[name]
    ]
    unchanged_features = [name for name in FEATURES if baseline_features[name] == candidate[name]]
    simulation_timestamp = datetime.now(timezone.utc).isoformat()
    risk_delta = round(float(simulated_prediction['risk_probability']) - float(base_prediction['risk_probability']), 8)
    class_delta = simulated_prediction['risk_level'] if simulated_prediction['risk_level'] != base_prediction['risk_level'] else None
    return {
        'student_id': student.id,
        'baseline_prediction': base_prediction,
        'simulated_prediction': simulated_prediction,
        'baseline_class_probabilities': base_prediction['class_probabilities'],
        'simulated_class_probabilities': simulated_prediction['class_probabilities'],
        'baseline_risk_level': base_prediction['risk_level'],
        'simulated_risk_level': simulated_prediction['risk_level'],
        'probability_deltas': probability_deltas,
        'changed_features': changed_features,
        'unchanged_features': unchanged_features,
        'model_version': base_prediction['model_version'],
        'simulation_timestamp': simulation_timestamp,
        'risk_delta': risk_delta,
        'class_delta': class_delta,
        'scenario_applied': scenario_values,
        'simulation_label': 'SIMULATION / WHAT-IF - NOT AN ACTUAL STUDENT PREDICTION',
    }


def compute_data_quality_report(db: Session) -> dict[str, Any]:
    now = datetime.utcnow()
    stale_before = now - timedelta(days=180)
    records = db.query(StudentSubjectAcademicRecord).all()
    outcomes = db.query(AcademicRiskOutcome).all()
    import_rows = db.query(AcademicDataImportRow).all()
    outcome_by_key = {(item.student_id, item.academic_period): item for item in outcomes}
    temporal_problems = 0
    stale_observation_records = 0
    future_dated_records = 0
    missing_observation_dates = 0
    complete_observations = 0
    for record in records:
        dated_values = (
            (record.fa1, record.fa1_observed_at),
            (record.fa2, record.fa2_observed_at),
            (record.assignment_mark, record.assignment_observed_at),
            (record.attendance_rate, record.attendance_observed_at),
        )
        outcome = outcome_by_key.get((record.student_id, record.academic_period))
        if all(value is not None and observed_at is not None for value, observed_at in dated_values):
            complete_observations += 1
        present_dates = [observed_at for value, observed_at in dated_values if value is not None and observed_at is not None]
        if any(observed_at < stale_before for observed_at in present_dates):
            stale_observation_records += 1
        if any(observed_at > now for observed_at in present_dates):
            future_dated_records += 1
        if any(value is not None and observed_at is None for value, observed_at in dated_values):
            missing_observation_dates += 1
        if outcome is not None and any(value is not None and (observed_at is None or observed_at > outcome.observed_at) for value, observed_at in dated_values):
            temporal_problems += 1

    unmatched_student_rows = sum(any('unknown student_id:' in error.casefold() or 'ambiguous student_id:' in error.casefold() for error in (row.errors or [])) for row in import_rows)
    unmatched_subject_rows = sum(any('unknown subject_code:' in error.casefold() for error in (row.errors or [])) for row in import_rows)
    academic_period_problems = sum(
        record.academic_period == 'UNSPECIFIED' for record in records
    ) + sum(any('academic_period' in error.casefold() for error in (row.errors or [])) for row in import_rows)
    readiness = readiness_report(db)
    record_count = len(records)
    aligned_count = readiness['complete_period_aligned_observations_with_valid_target']
    fa1_count = sum(record.fa1 is not None for record in records)
    fa2_count = sum(record.fa2 is not None for record in records)
    missing_values = {
        'fa1': record_count - fa1_count,
        'fa2': record_count - fa2_count,
        'assignment_mark': sum(record.assignment_mark is None for record in records),
        'attendance_rate': sum(record.attendance_rate is None for record in records),
    }
    invalid_ranges = readiness['invalid_values']
    invalid_count = sum(invalid_ranges.values())
    severe = invalid_count > 0 or readiness['duplicate_record_groups'] > 0 or readiness['duplicate_outcome_groups'] > 0 or temporal_problems > 0 or future_dated_records > 0
    has_warnings = (
        any(missing_values.values()) or unmatched_student_rows > 0 or unmatched_subject_rows > 0
        or academic_period_problems > 0 or stale_observation_records > 0 or missing_observation_dates > 0
    )
    status = 'critical' if severe else 'warning' if has_warnings or record_count == 0 else 'healthy'
    return {
        'status': status,
        'generated_at': now.isoformat(),
        'counts': {
            'students': db.query(Student.id).count(),
            'subject_records': record_count,
            'risk_outcomes': len(outcomes),
            'records_with_aligned_outcomes': aligned_count,
            'complete_observations': complete_observations,
            'duplicate_record_groups': readiness['duplicate_record_groups'],
            'duplicate_outcome_groups': readiness['duplicate_outcome_groups'],
        },
        'coverage': {
            'fa1_count': fa1_count,
            'fa1_percent': fa1_count / record_count * 100 if record_count else None,
            'fa2_count': fa2_count,
            'fa2_percent': fa2_count / record_count * 100 if record_count else None,
            'complete_observations_percent': complete_observations / record_count * 100 if record_count else None,
            'outcome_alignment_percent': aligned_count / record_count * 100 if record_count else None,
        },
        'missing_values': missing_values,
        'invalid_ranges': invalid_ranges,
        'checks': {
            'unmatched_student_rows': unmatched_student_rows,
            'unmatched_subject_rows': unmatched_subject_rows,
            'academic_period_problems': academic_period_problems,
            'stale_observation_records_180d': stale_observation_records,
            'future_dated_records': future_dated_records,
            'missing_observation_timestamps': missing_observation_dates,
            'temporal_alignment_problems': temporal_problems,
            'import_rows_with_errors': sum(bool(row.errors) for row in import_rows),
        },
        'readiness': readiness,
        'notes': 'The academic-data gate requires period-matched independent outcomes and temporal alignment. Demo-model metrics are excluded from readiness; inspect import provenance before treating academic-record coverage as real-world evidence.',
    }


def risk_transition_report(db: Session, student_ids: set[int] | None = None) -> dict[str, Any]:
    student_query = db.query(Student).order_by(Student.id)
    if student_ids is not None:
        student_query = student_query.filter(Student.id.in_(student_ids))
    students = student_query.all()
    histories: dict[int, list[RiskPrediction]] = {student.id: [] for student in students}
    prediction_query = db.query(RiskPrediction)
    if student_ids is not None:
        prediction_query = prediction_query.filter(RiskPrediction.student_id.in_(student_ids))
    for prediction in prediction_query.order_by(RiskPrediction.student_id, RiskPrediction.prediction_timestamp, RiskPrediction.id).all():
        histories.setdefault(prediction.student_id, []).append(prediction)
    insufficient = [student.id for student in students if len(histories.get(student.id, [])) < 2]
    transitions = []
    for student in students:
        history = histories.get(student.id, [])
        for previous, current in zip(history, history[1:]):
            if previous.risk_level == current.risk_level:
                continue
            transitions.append({
                'student_id': student.id,
                'student_name': student.full_name,
                'previous_risk': previous.risk_level,
                'new_risk': current.risk_level,
                'recorded_at': current.prediction_timestamp.isoformat(),
                'previous_probability': previous.probability,
                'new_probability': current.probability,
                'probability_delta': round(current.probability - previous.probability, 8),
                'probability_note': 'Difference between the saved winning-class probabilities; classes may differ.',
                'previous_model_version': previous.model_version,
                'model_version': current.model_version,
                'evidence': {'previous_prediction_id': previous.id, 'prediction_id': current.id},
            })
    return {
        'history_status': 'available' if any(len(items) >= 2 for items in histories.values()) else 'insufficient_history',
        'students_with_insufficient_history': insufficient,
        'transitions': transitions,
    }


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.get(f'{settings.api_v1_prefix}/ml/academic-data/readiness')
def academic_ml_readiness(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    return readiness_report(db)


@app.get(f'{settings.api_v1_prefix}/ml/academic-data/export.csv')
def export_academic_ml_observations(user: CurrentTeacher, db: Session = Depends(get_db)) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=DATASET_FIELDS)
    writer.writeheader()
    writer.writerows(build_observation_rows(db))
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="academic-ml-observations.csv"'})


@app.post(f'{settings.api_v1_prefix}/academic-data/import')
async def upload_academic_data(
    user: CurrentTeacher,
    dataset_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    content = await file.read(settings.max_upload_size + 1)
    await file.close()
    if len(content) > settings.max_upload_size:
        raise HTTPException(status_code=413, detail=f'File exceeds the {settings.max_upload_size}-byte upload limit.')
    try:
        result = import_academic_data(db, user_id=user.id, filename=file.filename or '', content=content, dataset_type=dataset_type)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail={'message': str(exc), 'validation_errors': exc.errors}) from exc
    audit(db, user, 'academic_data.imported', 'academic_data_import_batch', result.get('batch_id'))
    if not result.get('duplicate_import'):
        db.commit()
    return result


@app.get(f'{settings.api_v1_prefix}/academic-data/imports')
def list_academic_imports(user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    batches = db.query(AcademicDataImportBatch).order_by(AcademicDataImportBatch.created_at.desc()).limit(50).all()
    return [{
        'id': batch.id,
        'dataset_type': batch.dataset_type,
        'source_filename': batch.source_filename,
        'imported_rows': batch.imported_rows,
        'rejected_rows': batch.rejected_rows,
        'duplicate_rows': batch.duplicate_rows,
        'source_provenance': batch.source_provenance or {},
        'created_at': batch.created_at.isoformat(),
    } for batch in batches]


@app.get(f'{settings.api_v1_prefix}/subjects')
def list_subjects(user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise HTTPException(status_code=403, detail='Teacher profile not found')
    subjects = db.query(Subject).order_by(Subject.code).all()
    visible = []
    for subject in subjects:
        connected = db.query(Course.id).filter(Course.subject_id == subject.id, Course.teacher_id == teacher.id).first()
        if connected is None and subject.department and teacher.department and subject.department != teacher.department:
            continue
        visible.append({'id': subject.id, 'code': subject.code, 'name': subject.name})
    return visible


@app.get(f'{settings.api_v1_prefix}/teacher/student-risk-overview', response_model=RiskOverviewOut)
def teacher_student_risk_overview(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    student_id: int | None = Query(default=None, gt=0),
    search: str | None = Query(default=None, max_length=120),
    section: str | None = Query(default=None, max_length=40),
    academic_period: str | None = Query(default=None, max_length=30),
    risk_category: str | None = Query(default=None, max_length=30),
) -> dict[str, Any]:
    try:
        return build_teacher_risk_overview(
            db,
            user,
            student_id=student_id,
            search=search,
            section=section,
            academic_period=academic_period,
            risk_category=risk_category,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get(f'{settings.api_v1_prefix}/teacher/subject-focus')
def teacher_subject_focus(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    subject_id: int | None = Query(default=None, gt=0),
    student_id: int | None = Query(default=None, gt=0),
    first_internal_only: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        return build_teacher_subject_focus(
            db,
            user,
            subject_id=subject_id,
            student_id=student_id,
            first_internal_only=first_internal_only,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(f'{settings.api_v1_prefix}/students/me/subject-focus')
def my_subject_focus(
    student: CurrentStudent,
    db: Session = Depends(get_db),
    first_internal_only: bool = Query(default=False),
) -> dict[str, Any]:
    return build_student_subject_focus(
        db,
        student,
        first_internal_only=first_internal_only,
    )


@app.get(f'{settings.api_v1_prefix}/admin/subject-focus-policy')
def get_subject_focus_policy(user: CurrentAdmin, db: Session = Depends(get_db)) -> dict[str, Any]:
    policies = db.query(SubjectFocusPolicy).order_by(SubjectFocusPolicy.version.desc()).all()
    return {
        'current': serialize_subject_focus_policy(policies[0] if policies else None),
        'history': [{
            **(serialize_subject_focus_policy(policy) or {}),
            'actor_user_id': policy.actor_user_id,
            'actor_name': policy.actor.full_name if policy.actor else 'Project configuration',
        } for policy in policies],
    }


@app.put(f'{settings.api_v1_prefix}/admin/subject-focus-policy')
def update_subject_focus_policy(
    payload: SubjectFocusPolicyUpdate,
    user: CurrentAdmin,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    current = current_subject_focus_policy(db)
    if current is None:
        raise HTTPException(status_code=503, detail='The subject-focus policy has not been initialized.')
    if current.version != payload.expected_version:
        raise HTTPException(status_code=409, detail='The subject-focus policy changed. Reload its history and retry.')

    updated = SubjectFocusPolicy(
        version=current.version + 1,
        threshold_score=payload.threshold_score,
        threshold_maximum=payload.threshold_maximum,
        actor_user_id=user.id,
        change_reason=payload.change_reason.strip(),
    )
    db.add(updated)
    db.add(AuditLog(
        actor_user_id=user.id,
        action='subject_focus_policy.changed',
        resource_type='subject_focus_policy',
        resource_id=str(updated.version),
        detail={
            'previous_version': current.version,
            'previous_threshold': {
                'score': current.threshold_score,
                'maximum': current.threshold_maximum,
            },
            'new_threshold': {
                'score': payload.threshold_score,
                'maximum': payload.threshold_maximum,
            },
            'reason': payload.change_reason.strip(),
        },
    ))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail='The subject-focus policy changed concurrently. Reload and retry.') from exc
    db.refresh(updated)
    return serialize_subject_focus_policy(updated) or {}


@app.get(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/modules')
def list_subject_modules(subject_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    subject, _ = _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    modules = db.query(SubjectModule).filter(SubjectModule.subject_id == subject.id).order_by(SubjectModule.module_number).all()
    return [{
        'id': module.id,
        'subject_id': module.subject_id,
        'module_number': module.module_number,
        'title': module.title,
        'description': module.description,
        'status': module.status,
        'resource_count': len(module.resources),
    } for module in modules]


@app.post(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/modules', status_code=201)
def create_subject_module(subject_id: int, payload: SubjectModuleCreate, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    subject, _ = _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    existing = db.query(SubjectModule).filter_by(subject_id=subject.id, module_number=payload.module_number).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail='A module with that number already exists for this subject.')
    module = SubjectModule(subject_id=subject.id, module_number=payload.module_number, title=payload.title, description=payload.description, status='ACTIVE')
    db.add(module)
    db.commit()
    db.refresh(module)
    return {
        'id': module.id,
        'subject_id': module.subject_id,
        'module_number': module.module_number,
        'title': module.title,
        'description': module.description,
        'status': module.status,
    }


@app.put(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/modules/{{module_id}}')
def update_subject_module(subject_id: int, module_id: int, payload: SubjectModuleUpdate, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    module = db.query(SubjectModule).filter_by(id=module_id, subject_id=subject_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail='Module not found for this subject')
    if payload.title is not None:
        module.title = payload.title
    if payload.description is not None:
        module.description = payload.description
    db.commit()
    db.refresh(module)
    return {
        'id': module.id, 'subject_id': module.subject_id, 'module_number': module.module_number,
        'title': module.title, 'description': module.description, 'status': module.status,
    }


@app.post(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/modules/{{module_id}}/resources')
async def upload_subject_module_resource(
    subject_id: int,
    module_id: int,
    user: CurrentTeacher,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    subject, teacher = _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    module = db.query(SubjectModule).filter_by(id=module_id, subject_id=subject.id).first()
    if module is None:
        raise HTTPException(status_code=404, detail='Module not found for this subject')
    original_name = Path(file.filename or 'module-resource').name
    suffix = Path(original_name).suffix.lower()
    if not suffix or suffix not in MODULE_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail='Only PDF, DOCX, PPTX and TXT files are supported for module resources.')
    content = await file.read(settings.max_upload_size + 1)
    await file.close()
    if len(content) > settings.max_upload_size:
        raise HTTPException(status_code=413, detail=f'File exceeds the {settings.max_upload_size}-byte upload limit.')
    storage_root = (settings.upload_directory / 'subject-modules' / str(subject.id) / str(module.id)).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    safe_name = f'{uuid4().hex}{suffix}'
    target = storage_root / safe_name
    target.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    cached_resource = db.query(SubjectModuleResource).filter_by(
        subject_id=subject.id,
        content_hash=digest,
        file_type=suffix.lstrip('.').upper(),
        processing_status='PROCESSED',
    ).first()
    if cached_resource is not None and cached_resource.extracted_text:
        extracted_text = cached_resource.extracted_text
        extracted_topics = cached_resource.extracted_topics
        extraction_error = None
    else:
        extracted_text, extracted_topics, extraction_error = _extract_document_text(target, suffix)
    if not extraction_error and not extracted_text.strip():
        extraction_error = 'No readable text could be extracted from this resource.'
    resource = SubjectModuleResource(
        module_id=module.id,
        subject_id=subject.id,
        teacher_id=teacher.id,
        title=Path(original_name).stem[:200] or 'Uploaded resource',
        original_filename=original_name[:255],
        storage_path=str(target),
        file_type=suffix.lstrip('.').upper(),
        file_size=len(content),
        content_hash=digest,
        processing_status='FAILED' if extraction_error else 'PROCESSED',
        error_message=extraction_error,
        extracted_text=extracted_text or None,
        extracted_topics=extracted_topics,
        provenance={'storage_path': str(target), 'uploaded_by': user.email, 'source': 'teacher_upload'},
    )
    db.add(resource)
    db.commit()
    db.refresh(resource)
    return {
        'id': resource.id,
        'module_id': resource.module_id,
        'subject_id': resource.subject_id,
        'teacher_id': resource.teacher_id,
        'title': resource.title,
        'original_filename': resource.original_filename,
        'file_type': resource.file_type,
        'file_size': resource.file_size,
        'content_hash': resource.content_hash,
        'processing_status': resource.processing_status,
        'error_message': resource.error_message,
        'extracted_topics': resource.extracted_topics,
        'provenance': resource.provenance,
        'uploaded_at': resource.created_at.isoformat(),
    }


@app.get(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/modules/{{module_id}}/resources')
def list_subject_module_resources(subject_id: int, module_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    module = db.query(SubjectModule).filter_by(id=module_id, subject_id=subject_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail='Module not found')
    return [{
        'id': item.id,
        'module_id': item.module_id,
        'title': item.title,
        'original_filename': item.original_filename,
        'file_type': item.file_type,
        'file_size': item.file_size,
        'content_hash': item.content_hash,
        'processing_status': item.processing_status,
        'error_message': item.error_message,
        'extracted_text': item.extracted_text,
        'extracted_topics': item.extracted_topics,
        'provenance': item.provenance,
        'created_at': item.created_at.isoformat(),
    } for item in module.resources]


@app.get(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/modules/{{module_id}}/resources/{{resource_id}}/file')
def view_subject_module_resource(
    subject_id: int,
    module_id: int,
    resource_id: int,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> FileResponse:
    _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    resource = db.query(SubjectModuleResource).filter_by(
        id=resource_id,
        subject_id=subject_id,
        module_id=module_id,
    ).first()
    if resource is None:
        raise HTTPException(status_code=404, detail='Resource not found for this module.')
    storage_root = (settings.upload_directory / 'subject-modules').resolve()
    path = Path(resource.storage_path).resolve()
    if not path.is_relative_to(storage_root) or not path.is_file():
        raise HTTPException(status_code=404, detail='Stored module file is unavailable.')
    suffix = Path(resource.original_filename).suffix.lower()
    inline = suffix in {'.pdf', '.txt'}
    return FileResponse(
        path,
        filename=resource.original_filename,
        media_type=mimetypes.guess_type(resource.original_filename)[0] or 'application/octet-stream',
        content_disposition_type='inline' if inline else 'attachment',
    )


@app.post(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/modules/{{module_id}}/resources/{{resource_id}}/extract-topics')
def extract_subject_module_topics(
    subject_id: int,
    module_id: int,
    resource_id: int,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    resource = db.query(SubjectModuleResource).filter_by(
        id=resource_id, subject_id=subject_id, module_id=module_id,
    ).first()
    if resource is None:
        raise HTTPException(status_code=404, detail='Resource not found for this module.')
    if resource.processing_status != 'PROCESSED' or not resource.extracted_text:
        raise HTTPException(status_code=409, detail='Readable source text is required before topic extraction.')
    if resource.extracted_topics and resource.provenance.get('topic_map_status') == 'PROCESSED':
        return {
            'id': resource.id, 'processing_status': resource.processing_status,
            'topic_map_status': 'PROCESSED', 'error_message': resource.error_message,
            'extracted_topics': resource.extracted_topics, 'provenance': resource.provenance,
        }
    context = {
        'subject': {'id': subject_id, 'name': resource.module.subject.name},
        'module': {'id': module_id, 'number': resource.module.module_number, 'title': resource.module.title},
        'source': {
            'resource_id': resource.id,
            'filename': resource.original_filename,
            'file_type': resource.file_type,
            'available_locator_types': {'PDF': ['page'], 'PPTX': ['slide']}.get(resource.file_type.upper(), []),
            'sha256': resource.content_hash,
        },
        'source_text': resource.extracted_text[:60000],
        'truncated': len(resource.extracted_text) > 60000,
    }
    issues: list[str] = []
    limitation = None
    topics: list[dict[str, Any]] = []
    try:
        provider = build_provider(settings)
        raw = provider.generate_topic_map(context)
        topics, issues, limitation = validate_topic_map_response(raw, context['source_text'], context)
        if issues:
            previous_topics = topics
            previous_issues = list(issues)
            context['validation_issues'] = issues
            context['repair_instruction'] = (
                'Repair only schema or source-evidence errors once. Do not remove genuine ambiguity flags, '
                'do not suppress review reasons, and preserve every supported candidate and its source order.'
            )
            raw = provider.generate_topic_map(context)
            refreshed_topics, refreshed_issues, limitation = validate_topic_map_response(
                raw,
                context['source_text'],
                context,
            )
            if refreshed_topics:
                topics = _merge_topic_candidate_maps(previous_topics, refreshed_topics)
                issues = refreshed_issues
            else:
                topics = previous_topics
                issues = list(dict.fromkeys(previous_issues + refreshed_issues))
    except (RuntimeError, ValueError) as exc:
        issues.append(f'Topic extraction is unavailable: {exc}')
    if context['truncated']:
        issues.append('Only the first 60000 characters were submitted for topic mapping; review the remainder of the source.')
    def has_review_flag(items: list[dict[str, Any]]) -> bool:
        return any(
            bool(item.get('requires_review'))
            or has_review_flag(item.get('children', []))
            for item in items
            if isinstance(item, dict)
        )

    def collect_review_reasons(items: list[dict[str, Any]]) -> list[str]:
        reasons = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get('requires_review'):
                reasons.append(
                    f"Candidate {item.get('candidate_id') or item.get('title', 'topic')} requires teacher review: "
                    f"{item.get('review_reason') or 'Confirm its source grounding and syllabus placement.'}"
                )
            reasons.extend(collect_review_reasons(item.get('children', [])))
        return reasons

    issues = list(dict.fromkeys(issues + collect_review_reasons(topics)))
    valid = bool(topics) and not issues and not has_review_flag(topics)
    provenance = dict(resource.provenance or {})
    topic_map_metadata = {
        'course_title': next((topic.get('course_title') for topic in topics if topic.get('course_title')), None),
        'module_number': resource.module.module_number,
        'module_title': resource.module.title,
        'source_filename': resource.original_filename,
        'source_hash': resource.content_hash,
    }
    provenance.update({
        'topic_map_status': 'PROCESSED' if valid else 'REVIEW_REQUIRED',
        'topic_map_provider': getattr(locals().get('provider'), 'provider_name', 'unavailable'),
        'topic_map_errors': issues,
        'topic_map_limitation': limitation,
        'topic_map_metadata': topic_map_metadata,
        'topic_map_attempted_at': datetime.utcnow().isoformat(),
    })
    if topics:
        resource.extracted_topics = topics
    if valid:
        resource.error_message = None
    else:
        resource.error_message = 'Topic map requires teacher review. ' + ' '.join(issues or [limitation or 'No supported topics were returned.'])
    resource.provenance = provenance
    _recovery_audit(db, user, 'subject_module.topic_map_generated', 'subject_module_resource', resource.id, new_state=provenance['topic_map_status'], reason='; '.join(issues)[:2000] or limitation)
    db.commit()
    db.refresh(resource)
    return {
        'id': resource.id, 'processing_status': resource.processing_status,
        'topic_map_status': provenance['topic_map_status'], 'error_message': resource.error_message,
        'extracted_topics': resource.extracted_topics, 'provenance': resource.provenance,
    }


@app.post(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/topics/approve')
def approve_teaching_topic(subject_id: int, payload: TopicApprovalRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    _, teacher = _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    resource = db.query(SubjectModuleResource).filter_by(id=payload.resource_id, subject_id=subject_id).first()
    if resource is None:
        raise HTTPException(status_code=404, detail='Resource not found for this subject')
    module = db.query(SubjectModule).filter_by(id=resource.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail='Resource module not found')
    candidate: dict[str, Any] | None = None
    if payload.candidate_id:
        pending = list(resource.extracted_topics or [])
        while pending:
            item = pending.pop(0)
            if isinstance(item, dict):
                if item.get('candidate_id') == payload.candidate_id:
                    candidate = item
                    break
                children = item.get('children')
                if isinstance(children, list):
                    pending[0:0] = children
        if candidate is None:
            raise HTTPException(status_code=409, detail='The selected topic candidate is no longer available for this source.')

    candidate_provenance = {
        'candidate_id': candidate.get('candidate_id') if candidate else None,
        'candidate_path': candidate.get('candidate_path') if candidate else None,
        'original_number': candidate.get('original_number') if candidate else None,
        'source_order': candidate.get('source_order') if candidate else payload.source_order,
        'source_excerpt': candidate.get('source_excerpt') if candidate else None,
        'source_locator': candidate.get('source_locator') if candidate else None,
        'course_title': candidate.get('course_title') if candidate else None,
        'module_number': module.module_number,
        'module_title': module.title,
        'order_uncertain': candidate.get('order_uncertain', False) if candidate else payload.order_uncertain,
        'hierarchy_uncertain': candidate.get('hierarchy_uncertain', False) if candidate else False,
        'requires_review': candidate.get('requires_review', False) if candidate else False,
        'review_reason': candidate.get('review_reason') if candidate else None,
        'parent_candidate_id': (
            '.'.join(str(value) for value in candidate['candidate_path'][:-1])
            if candidate and isinstance(candidate.get('candidate_path'), list) and len(candidate['candidate_path']) > 1
            else None
        ),
    }
    for existing in resource.topics:
        provenance = existing.source_provenance if isinstance(existing.source_provenance, dict) else {}
        same_candidate = bool(payload.candidate_id and provenance.get('candidate_id') == payload.candidate_id)
        same_source_identity = (
            provenance.get('source_order') is not None
            and provenance.get('source_order') == candidate_provenance['source_order']
            and provenance.get('original_number') == candidate_provenance['original_number']
        )
        legacy_match = (
            not provenance.get('candidate_id')
            and re.sub(r'\s+', ' ', existing.title).strip().casefold()
            == re.sub(r'\s+', ' ', payload.title).strip().casefold()
            and (
                provenance.get('source_order') is None
                or candidate_provenance['source_order'] is None
                or provenance.get('source_order') == candidate_provenance['source_order']
            )
            and (
                provenance.get('original_number') is None
                or candidate_provenance['original_number'] is None
                or provenance.get('original_number') == candidate_provenance['original_number']
            )
        )
        if same_candidate or same_source_identity or legacy_match:
            if existing.approval_status != 'APPROVED':
                raise HTTPException(status_code=409, detail='This source candidate already has a teacher review record.')
            return {
                'id': existing.id,
                'subject_id': existing.subject_id,
                'module_id': existing.module_id,
                'resource_id': existing.source_resource_id,
                'title': existing.title,
                'summary': existing.summary,
                'learning_objectives': existing.learning_objectives,
                'concepts': existing.concepts,
                'approval_status': existing.approval_status,
                'source_provenance': existing.source_provenance,
                'duplicate': True,
            }
    topic = ApprovedTopic(
        subject_id=subject_id,
        module_id=module.id,
        source_resource_id=resource.id,
        teacher_id=teacher.id,
        title=payload.title,
        summary=payload.summary,
        learning_objectives=payload.learning_objectives,
        concepts=payload.concepts,
        approval_status=payload.approval_status,
        approved_by_user_id=user.id,
        source_provenance={
            'resource_id': resource.id,
            'resource_title': resource.title,
            'source_filename': resource.original_filename,
            'module': module.title,
            'hash': resource.content_hash,
            **candidate_provenance,
        },
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return {
        'id': topic.id,
        'subject_id': topic.subject_id,
        'module_id': topic.module_id,
        'resource_id': topic.source_resource_id,
        'title': topic.title,
        'summary': topic.summary,
        'learning_objectives': topic.learning_objectives,
        'concepts': topic.concepts,
        'approval_status': topic.approval_status,
        'source_provenance': topic.source_provenance,
        'duplicate': False,
    }


def _approved_topic_sequence_key(topic: ApprovedTopic) -> tuple[int, int, datetime, int]:
    module_number = topic.module.module_number if topic.module else 999
    provenance = topic.source_provenance if isinstance(topic.source_provenance, dict) else {}
    source_order = provenance.get('source_order')
    if isinstance(source_order, bool) or not isinstance(source_order, int):
        source_order = 999
    return module_number, source_order, topic.created_at, topic.id


def _strict_recovery_subject_ids(focus: dict[str, Any], student_id: int) -> set[int]:
    policy = focus.get('policy')
    if (
        focus.get('evidence_scope') != 'FIRST_INTERNAL'
        or not isinstance(policy, dict)
        or not policy.get('active_for_classification')
        or policy.get('threshold_maximum') != 25
    ):
        return set()

    eligible_subject_ids: set[int] = set()
    for subject in focus.get('subjects', []):
        student = next(
            (item for item in subject.get('students', []) if item.get('id') == student_id),
            None,
        )
        assessment = student.get('latest_assessment') if student else None
        if (
            assessment
            and assessment.get('evidence_status') == 'VERIFIED'
            and assessment.get('maximum_marks_verified') is True
            and isinstance(assessment.get('normalized_score'), (int, float))
            and not isinstance(assessment.get('normalized_score'), bool)
            and assessment['normalized_score'] < 12
        ):
            eligible_subject_ids.add(subject['id'])
    return eligible_subject_ids


@app.get(f'{settings.api_v1_prefix}/subjects/{{subject_id}}/topics')
def list_subject_topics(subject_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    topics = (
        db.query(ApprovedTopic)
        .join(SubjectModule, SubjectModule.id == ApprovedTopic.module_id)
        .filter(ApprovedTopic.subject_id == subject_id)
        .order_by(SubjectModule.module_number, ApprovedTopic.created_at, ApprovedTopic.id)
        .all()
    )
    topics.sort(key=_approved_topic_sequence_key)
    return [{
        'id': topic.id,
        'module_id': topic.module_id,
        'title': topic.title,
        'summary': topic.summary,
        'learning_objectives': topic.learning_objectives,
        'concepts': topic.concepts,
        'approval_status': topic.approval_status,
        'source_provenance': topic.source_provenance,
    } for topic in topics]


@app.post(f'{settings.api_v1_prefix}/students/{{student_id}}/topic-learning-assignments')
def create_topic_learning_assignment(student_id: int, payload: TopicAssignmentCreate, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    _recovery_student_access(student.id, user, db)
    topic = db.query(ApprovedTopic).filter(ApprovedTopic.id == payload.approved_topic_id).first()
    if topic is None:
        raise HTTPException(status_code=404, detail='Approved topic not found')
    if topic.approval_status != 'APPROVED':
        raise HTTPException(status_code=409, detail='Only teacher-approved topics can be assigned for recovery.')
    _teacher_subject_access(db, teacher_user=user, subject_id=topic.subject_id)
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    existing_assignment = db.query(TopicLearningAssignment).filter_by(
        student_id=student.id, approved_topic_id=topic.id,
    ).first()
    if existing_assignment is not None:
        if not _topic_assignment_scope_is_valid(existing_assignment, db):
            raise HTTPException(status_code=409, detail='The existing learning assignment has inconsistent subject provenance.')
        return _topic_assignment_out(db, existing_assignment, include_private=True)
    if topic.module and topic.module.module_number == 4:
        focus = build_teacher_subject_focus(db, user, student_id=student.id, first_internal_only=True)
        eligible_subject_ids = _strict_recovery_subject_ids(focus, student.id)
        if topic.subject_id in eligible_subject_ids:
            module_three_topics = (
                db.query(ApprovedTopic.id, ApprovedTopic.subject_id)
                .join(SubjectModule, SubjectModule.id == ApprovedTopic.module_id)
                .filter(
                    ApprovedTopic.subject_id.in_(eligible_subject_ids),
                    ApprovedTopic.approval_status == 'APPROVED',
                    SubjectModule.module_number == 3,
                )
                .all()
            )
            module_three_subject_ids = {subject_id for _, subject_id in module_three_topics}
            if not eligible_subject_ids.issubset(module_three_subject_ids):
                raise HTTPException(
                    status_code=409,
                    detail='Module 3 needs approved topics in every FA-1-eligible subject before Module 4 assignments can begin.',
                )
            module_three_topic_ids = {topic_id for topic_id, _ in module_three_topics}
            mastered_topic_ids = {
                topic_id for (topic_id,) in db.query(TopicLearningAssignment.approved_topic_id)
                .filter(
                    TopicLearningAssignment.student_id == student.id,
                    TopicLearningAssignment.subject_id.in_(eligible_subject_ids),
                    TopicLearningAssignment.approved_topic_id.in_(module_three_topic_ids),
                    TopicLearningAssignment.status == 'MASTERED',
                )
                .all()
            }
            if not module_three_topic_ids.issubset(mastered_topic_ids):
                raise HTTPException(
                    status_code=409,
                    detail='Complete every approved Module 3 topic across all FA-1-eligible subjects before assigning a Module 4 topic.',
                )
    ordered_topics = (
        db.query(ApprovedTopic)
        .join(SubjectModule, SubjectModule.id == ApprovedTopic.module_id)
        .filter(ApprovedTopic.subject_id == topic.subject_id, ApprovedTopic.approval_status == 'APPROVED')
        .order_by(SubjectModule.module_number, ApprovedTopic.created_at, ApprovedTopic.id)
        .all()
    )
    ordered_topics.sort(key=_approved_topic_sequence_key)
    student_assignments = db.query(TopicLearningAssignment).filter_by(student_id=student.id, subject_id=topic.subject_id).all()
    mastered_topic_ids = {item.approved_topic_id for item in student_assignments if item.status == 'MASTERED'}
    next_topic = next((item for item in ordered_topics if item.id not in mastered_topic_ids), None)
    if next_topic is not None and next_topic.id != topic.id:
        raise HTTPException(status_code=409, detail=f'Complete the next topic in source order first: {next_topic.title}.')
    assignment = TopicLearningAssignment(
        student_id=student.id,
        subject_id=topic.subject_id,
        module_id=topic.module_id,
        approved_topic_id=topic.id,
        teacher_id=teacher.id if teacher else None,
        title=(payload.title or topic.title)[:200],
        learning_objective=(payload.learning_objective or (topic.learning_objectives[0] if topic.learning_objectives else topic.summary))[:2000],
        lesson_content=(payload.lesson_content or f"Use the approved topic '{topic.title}' to review the key concept, definitions, and examples from the linked module resources. Focus on the learning objective and the fundamentals needed for the assessment.")[:10000],
        expected_duration_minutes=payload.expected_duration_minutes,
        scheduled_at=payload.scheduled_at,
        available_from=payload.available_from,
        status='ACTIVE',
        mastery_criteria=(payload.mastery_criteria or 'Earn at least 80% on the topic assessment and demonstrate the listed learning objective in your explanation.')[:2000],
        source_provenance={'topic_id': topic.id, 'topic_title': topic.title, 'resource_id': topic.source_resource_id, 'module_id': topic.module_id},
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return {
        'id': assignment.id,
        'student_id': assignment.student_id,
        'subject_id': assignment.subject_id,
        'module_id': assignment.module_id,
        'approved_topic_id': assignment.approved_topic_id,
        'teacher_id': assignment.teacher_id,
        'title': assignment.title,
        'learning_objective': assignment.learning_objective,
        'lesson_content': assignment.lesson_content,
        'expected_duration_minutes': assignment.expected_duration_minutes,
        'scheduled_at': assignment.scheduled_at.isoformat() if assignment.scheduled_at else None,
        'available_from': assignment.available_from.isoformat() if assignment.available_from else None,
        'status': assignment.status,
        'mastery_criteria': assignment.mastery_criteria,
        'source_provenance': assignment.source_provenance,
    }


@app.get(f'{settings.api_v1_prefix}/students/{{student_id:int}}/topic-learning-assignments')
def list_student_topic_assignments(student_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    _recovery_student_access(student.id, user, db)
    subject_ids = _teacher_assigned_subject_ids(db, user)
    assignments = db.query(TopicLearningAssignment).filter(
        TopicLearningAssignment.student_id == student.id,
        TopicLearningAssignment.subject_id.in_(subject_ids),
    ).order_by(TopicLearningAssignment.created_at.desc()).all() if subject_ids else []
    assignments = [
        assignment for assignment in assignments
        if _topic_assignment_scope_is_valid(assignment, db)
    ]
    return [_topic_assignment_out(db, assignment, include_private=True) for assignment in assignments]


@app.get(f'{settings.api_v1_prefix}/students/me/topic-learning-assignments')
def my_topic_learning_assignments(student: CurrentStudent, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    assignments = db.query(TopicLearningAssignment).filter(
        TopicLearningAssignment.student_id == student.id,
        TopicLearningAssignment.status.in_(['ACTIVE', 'MASTERED', 'REVISION_REQUIRED']),
    ).order_by(TopicLearningAssignment.created_at.desc()).all()
    assignments = [
        assignment for assignment in assignments
        if _topic_assignment_scope_is_valid(assignment, db)
    ]
    return [_topic_assignment_out(db, assignment, include_private=False) for assignment in assignments]


def _topic_assignment_out(db: Session, assignment: TopicLearningAssignment, *, include_private: bool) -> dict[str, Any]:
    topic = db.query(ApprovedTopic).filter(ApprovedTopic.id == assignment.approved_topic_id).first()
    subject = db.query(Subject).filter(Subject.id == assignment.subject_id).first()
    module = db.query(SubjectModule).filter(SubjectModule.id == assignment.module_id).first()
    session = db.query(LearningStudySession).filter_by(assignment_id=assignment.id, student_id=assignment.student_id).first()
    assessments = db.query(LearningAssessment).filter(LearningAssessment.assignment_id == assignment.id).order_by(LearningAssessment.version.desc()).all()
    published = next((item for item in assessments if item.status == 'PUBLISHED'), None)
    attempts = db.query(AssessmentAttempt).filter(AssessmentAttempt.assignment_id == assignment.id).order_by(AssessmentAttempt.created_at.desc()).all()
    resources = db.query(SubjectModuleResource).filter_by(module_id=assignment.module_id, subject_id=assignment.subject_id, processing_status='PROCESSED').order_by(SubjectModuleResource.created_at).all()
    latest_attempt = attempts[0] if attempts else None
    result: dict[str, Any] = {
        'id': assignment.id,
        'subject_id': assignment.subject_id,
        'subject_name': subject.name if subject else 'Subject',
        'module_id': assignment.module_id,
        'module_number': module.module_number if module else None,
        'module_title': module.title if module else 'Module',
        'approved_topic_id': assignment.approved_topic_id,
        'topic_title': topic.title if topic else assignment.title,
        'topic_summary': topic.summary if topic else '',
        'title': assignment.title,
        'learning_objective': assignment.learning_objective,
        'lesson_content': assignment.lesson_content,
        'expected_duration_minutes': assignment.expected_duration_minutes,
        'status': assignment.status,
        'scheduled_at': assignment.scheduled_at.isoformat() if assignment.scheduled_at else None,
        'available_from': assignment.available_from.isoformat() if assignment.available_from else None,
        'study_session': {
            'id': session.id,
            'started_at': session.started_at.isoformat(),
            'required_duration_minutes': session.required_duration_minutes,
            'completed_at': session.completed_at.isoformat() if session.completed_at else None,
            'elapsed_seconds': max(0, int((datetime.utcnow() - session.started_at).total_seconds())),
            'remaining_seconds': max(0, session.required_duration_minutes * 60 - int((datetime.utcnow() - session.started_at).total_seconds())),
            'ready': int((datetime.utcnow() - session.started_at).total_seconds()) >= session.required_duration_minutes * 60,
        } if session else None,
        'assessment': {
            'id': published.id,
            'version': published.version,
            'status': published.status,
            'question_count': published.question_count,
        } if published else None,
        'latest_attempt': {
            'id': attempts[0].id,
            'status': attempts[0].status,
            'percentage': attempts[0].percentage,
            'submitted_at': attempts[0].submitted_at.isoformat() if attempts[0].submitted_at else None,
            'review_pending': db.query(AssessmentAttemptAnswer).filter_by(attempt_id=attempts[0].id, review_required=True).count() > 0,
        } if attempts else None,
        'attempt_count': len(attempts),
        'resources': [{
            'id': resource.id, 'title': resource.title, 'original_filename': resource.original_filename,
            'file_type': resource.file_type, 'file_size': resource.file_size,
        } for resource in resources],
        'source_provenance': assignment.source_provenance,
    }
    if latest_attempt:
        result['latest_attempt'].update({
            'total_marks': latest_attempt.total_marks,
            'question_set': latest_attempt.question_set if latest_attempt.status == 'IN_PROGRESS' else None,
            'responses': [{
                'question_id': answer.question_id,
                'marks_awarded': answer.marks_awarded,
                'feedback': answer.feedback,
                'review_pending': answer.review_required,
            } for answer in latest_attempt.responses],
        })
    if include_private:
        result.update({
            'student_id': assignment.student_id,
            'student_name': assignment.student.full_name if assignment.student else '',
            'mastery_criteria': assignment.mastery_criteria,
            'assessments': [{'id': item.id, 'version': item.version, 'status': item.status, 'question_count': item.question_count, 'validation_results': item.validation_results, 'error_message': item.error_message} for item in assessments],
            'latest_attempt_id': attempts[0].id if attempts else None,
        })
    elif published:
        questions = db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == published.id).order_by(AssessmentQuestion.id).all()
        result['assessment']['questions'] = [_learning_question_out(question, include_answer=False) for question in questions]
    return result


def _student_learning_assignment_access(assignment_id: int, student: Student, db: Session) -> TopicLearningAssignment:
    assignment = db.query(TopicLearningAssignment).filter_by(id=assignment_id, student_id=student.id).first()
    if assignment is None:
        raise HTTPException(status_code=404, detail='Learning assignment not found')
    if assignment.status not in {'ACTIVE', 'MASTERED', 'REVISION_REQUIRED'}:
        raise HTTPException(status_code=404, detail='Learning assignment not found')
    if not _topic_assignment_scope_is_valid(assignment, db):
        raise HTTPException(status_code=404, detail='Learning assignment not found')
    if assignment.available_from and assignment.available_from > datetime.utcnow():
        raise HTTPException(status_code=409, detail='This learning activity is not available yet.')
    return assignment


def _topic_assignment_scope_is_valid(assignment: TopicLearningAssignment, db: Session) -> bool:
    topic_exists = db.query(ApprovedTopic.id).filter(
        ApprovedTopic.id == assignment.approved_topic_id,
        ApprovedTopic.subject_id == assignment.subject_id,
        ApprovedTopic.module_id == assignment.module_id,
        ApprovedTopic.approval_status == 'APPROVED',
    ).first()
    module_exists = db.query(SubjectModule.id).filter(
        SubjectModule.id == assignment.module_id,
        SubjectModule.subject_id == assignment.subject_id,
    ).first()
    return topic_exists is not None and module_exists is not None


def _study_session_out(session: LearningStudySession) -> dict[str, Any]:
    elapsed = max(0, int((datetime.utcnow() - session.started_at).total_seconds()))
    required = session.required_duration_minutes * 60
    return {
        'id': session.id,
        'started_at': session.started_at.isoformat(),
        'required_duration_minutes': session.required_duration_minutes,
        'elapsed_seconds': elapsed,
        'remaining_seconds': max(0, required - elapsed),
        'completed_at': session.completed_at.isoformat() if session.completed_at else None,
        'ready': elapsed >= required,
    }


@app.post(f'{settings.api_v1_prefix}/topic-learning-assignments/{{assignment_id}}/study-session')
def start_topic_study_session(assignment_id: int, student: CurrentStudent, db: Session = Depends(get_db)) -> dict[str, Any]:
    assignment = _student_learning_assignment_access(assignment_id, student, db)
    session = db.query(LearningStudySession).filter_by(assignment_id=assignment.id, student_id=student.id).first()
    if session is None:
        session = LearningStudySession(
            assignment_id=assignment.id,
            student_id=student.id,
            started_at=datetime.utcnow(),
            required_duration_minutes=assignment.expected_duration_minutes,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
    return _study_session_out(session)


@app.get(f'{settings.api_v1_prefix}/topic-learning-assignments/{{assignment_id}}/study-session')
def get_topic_study_session(assignment_id: int, student: CurrentStudent, db: Session = Depends(get_db)) -> dict[str, Any]:
    assignment = _student_learning_assignment_access(assignment_id, student, db)
    session = db.query(LearningStudySession).filter_by(assignment_id=assignment.id, student_id=student.id).first()
    if session is None:
        raise HTTPException(status_code=404, detail='Study session has not started.')
    return _study_session_out(session)


@app.post(f'{settings.api_v1_prefix}/topic-learning-assignments/{{assignment_id}}/study-session/complete')
def complete_topic_study_session(assignment_id: int, student: CurrentStudent, db: Session = Depends(get_db)) -> dict[str, Any]:
    assignment = _student_learning_assignment_access(assignment_id, student, db)
    session = db.query(LearningStudySession).filter_by(assignment_id=assignment.id, student_id=student.id).first()
    if session is None:
        raise HTTPException(status_code=409, detail='Start the learning activity before completing its timer.')
    timer = _study_session_out(session)
    if not timer['ready']:
        raise HTTPException(status_code=409, detail={'message': 'Required elapsed study time has not passed.', 'remaining_seconds': timer['remaining_seconds']})
    if session.completed_at is None:
        session.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(session)
    return _study_session_out(session)


@app.get(f'{settings.api_v1_prefix}/topic-learning-assignments/{{assignment_id}}/resources/{{resource_id}}')
def download_topic_learning_resource(assignment_id: int, resource_id: int, student: CurrentStudent, db: Session = Depends(get_db)) -> FileResponse:
    assignment = _student_learning_assignment_access(assignment_id, student, db)
    topic = db.query(ApprovedTopic).filter_by(
        id=assignment.approved_topic_id,
        subject_id=assignment.subject_id,
        module_id=assignment.module_id,
        approval_status='APPROVED',
    ).first()
    if topic is None or topic.source_resource_id != resource_id:
        raise HTTPException(status_code=404, detail='Resource not found for this learning activity.')
    resource = db.query(SubjectModuleResource).filter_by(
        id=resource_id, module_id=assignment.module_id, subject_id=assignment.subject_id, processing_status='PROCESSED',
    ).first()
    if resource is None:
        raise HTTPException(status_code=404, detail='Resource not found for this learning activity.')
    path = Path(resource.storage_path).resolve()
    allowed_root = (settings.upload_directory / 'subject-modules' / str(assignment.subject_id) / str(assignment.module_id)).resolve()
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail='Resource file is unavailable.') from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Resource file is unavailable.')
    return FileResponse(path, filename=resource.original_filename, media_type=mimetypes.guess_type(resource.original_filename)[0] or 'application/octet-stream')


def _teacher_learning_attempt_access(attempt_id: int, user: User, db: Session) -> tuple[AssessmentAttempt, LearningAssessment, TopicLearningAssignment]:
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if attempt is None or attempt.assessment_id is None:
        raise HTTPException(status_code=404, detail='Assessment attempt not found')
    assessment, assignment = _teacher_learning_assessment_access(attempt.assessment_id, user, db)
    if attempt.assignment_id != assignment.id or attempt.student_id != assignment.student_id:
        raise HTTPException(status_code=404, detail='Assessment attempt not found')
    return attempt, assessment, assignment


@app.get(f'{settings.api_v1_prefix}/assessment-attempts/{{attempt_id}}/review')
def review_learning_attempt(attempt_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    attempt, assessment, assignment = _teacher_learning_attempt_access(attempt_id, user, db)
    questions = db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment.id).order_by(AssessmentQuestion.id).all()
    answers = db.query(AssessmentAttemptAnswer).filter(AssessmentAttemptAnswer.attempt_id == attempt.id).all()
    answer_by_question = {answer.question_id: answer for answer in answers}
    return {
        'id': attempt.id, 'assignment_id': assignment.id, 'assessment_id': assessment.id,
        'student_id': assignment.student_id, 'student_name': assignment.student.full_name if assignment.student else '',
        'subject_id': assignment.subject_id, 'status': attempt.status, 'percentage': attempt.percentage,
        'total_marks': attempt.total_marks,
        'questions': [{
            **_learning_question_out(question, include_answer=True),
            'answer': {
                'response_text': answer_by_question[question.id].response_text,
                'selected_choice': answer_by_question[question.id].selected_choice,
                'marks_awarded': answer_by_question[question.id].marks_awarded,
                'original_marks_awarded': answer_by_question[question.id].original_marks_awarded,
                'feedback': answer_by_question[question.id].feedback,
                'review_required': answer_by_question[question.id].review_required,
                'review_reason': answer_by_question[question.id].review_reason,
                'rubric_evaluation': answer_by_question[question.id].rubric_evaluation,
                'reviewed_by_user_id': answer_by_question[question.id].reviewed_by_user_id,
            } if question.id in answer_by_question else None,
        } for question in questions],
    }


@app.put(f'{settings.api_v1_prefix}/assessment-attempts/{{attempt_id}}/answers/{{question_id}}/review')
def review_learning_answer(
    attempt_id: int,
    question_id: int,
    payload: LearningAnswerReview,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    attempt, _, assignment = _teacher_learning_attempt_access(attempt_id, user, db)
    answer = db.query(AssessmentAttemptAnswer).filter_by(attempt_id=attempt.id, question_id=question_id).first()
    if answer is None:
        raise HTTPException(status_code=404, detail='Answer not found for this attempt.')
    question = db.query(AssessmentQuestion).filter_by(id=question_id).first()
    if question is None or question.question_type != 'SHORT_ANSWER':
        raise HTTPException(status_code=409, detail='Only short-answer responses can be manually reviewed.')
    previous_marks = answer.marks_awarded
    if payload.decision == 'override':
        if payload.marks_awarded is None or not payload.reason:
            raise HTTPException(status_code=422, detail='A mark and reason are required for a grade override.')
        if payload.marks_awarded > question.marks:
            raise HTTPException(status_code=422, detail=f'The override cannot exceed this question’s {question.marks:g} marks.')
        answer.original_marks_awarded = answer.original_marks_awarded if answer.original_marks_awarded is not None else previous_marks
        answer.marks_awarded = payload.marks_awarded
        answer.feedback = payload.feedback or answer.feedback
        answer.review_reason = payload.reason
    elif answer.review_required:
        answer.review_reason = payload.reason
    answer.review_required = False
    answer.reviewed_by_user_id = user.id
    answer.reviewed_at = datetime.utcnow()
    db.flush()
    remaining = db.query(AssessmentAttemptAnswer).filter_by(attempt_id=attempt.id, review_required=True).count()
    answers = db.query(AssessmentAttemptAnswer).filter_by(attempt_id=attempt.id).all()
    attempt.total_marks = sum(item.marks_awarded for item in answers)
    maximum_marks = _attempt_maximum_marks(attempt)
    attempt.percentage = round(attempt.total_marks / maximum_marks * 100, 2) if maximum_marks else 0
    if remaining == 0:
        attempt.status = 'SUBMITTED'
        assignment.status = 'MASTERED' if attempt.percentage >= 80 else 'REVISION_REQUIRED'
    _recovery_audit(
        db, user,
        'learning_answer.overridden' if payload.decision == 'override' else 'learning_answer.reviewed',
        'assessment_attempt_answer', answer.id,
        previous_state=str(previous_marks), new_state=str(answer.marks_awarded), reason=payload.reason,
    )
    db.commit()
    return {
        'question_id': question_id, 'marks_awarded': answer.marks_awarded,
        'original_marks_awarded': answer.original_marks_awarded,
        'review_pending': answer.review_required, 'attempt_status': attempt.status,
        'percentage': attempt.percentage,
    }


@app.post(f'{settings.api_v1_prefix}/topic-learning-assignments/{{assignment_id}}/generate-assessment')
def generate_topic_assessment(assignment_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    assignment = db.query(TopicLearningAssignment).filter(TopicLearningAssignment.id == assignment_id).first()
    if assignment is None or not _topic_assignment_scope_is_valid(assignment, db):
        raise HTTPException(status_code=404, detail='Topic learning assignment not found')
    _recovery_student_access(assignment.student_id, user, db)
    topic = db.query(ApprovedTopic).filter(ApprovedTopic.id == assignment.approved_topic_id).first()
    if topic is None:
        raise HTTPException(status_code=404, detail='Approved topic not found for this assignment')
    _teacher_subject_access(db, teacher_user=user, subject_id=assignment.subject_id)
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    resources = (
        db.query(SubjectModuleResource)
        .join(ApprovedTopic, ApprovedTopic.source_resource_id == SubjectModuleResource.id)
        .filter(
            ApprovedTopic.id == topic.id,
            ApprovedTopic.subject_id == topic.subject_id,
            ApprovedTopic.module_id == topic.module_id,
            ApprovedTopic.approval_status == 'APPROVED',
            SubjectModuleResource.processing_status == 'PROCESSED',
        )
        .order_by(SubjectModuleResource.created_at, SubjectModuleResource.id)
        .all()
    )
    documents = [
        {'ref': f'resource:{resource.id}', 'source': resource.original_filename, 'checksum': resource.content_hash, 'text': resource.extracted_text or ''}
        for resource in {item.id: item for item in resources}.values()
        if resource.extracted_text and resource.extracted_text.strip()
    ]
    query = ' '.join([topic.title, topic.summary, *topic.learning_objectives, *topic.concepts])
    passages = retrieve_scoped(query, documents, top_k=8) if documents else []
    existing_version = db.query(func.max(LearningAssessment.version)).filter(LearningAssessment.assignment_id == assignment.id).scalar() or 0
    assessment = LearningAssessment(
        assignment_id=assignment.id,
        version=existing_version + 1,
        status='REVIEW_REQUIRED',
        source_evidence=passages,
        validation_results={},
        teacher_id=teacher.id if teacher else None,
    )
    db.add(assessment)
    db.flush()
    issues: list[str] = []
    limitation = None
    generated: list[dict[str, Any]] = []
    error_message = None
    if not passages:
        issues.append('No readable evidence was retrieved from teacher-approved resources for this module and topic.')
        limitation = 'Approved resources contain no retrievable text for this topic.'
    else:
        context = {
            'subject': {'id': assignment.subject_id, 'name': topic.module.subject.name if topic.module and topic.module.subject else ''},
            'module': {'id': assignment.module_id, 'title': topic.module.title if topic.module else ''},
            'topic': {'title': topic.title, 'summary': topic.summary, 'learning_objectives': topic.learning_objectives, 'concepts': topic.concepts},
            'passages': passages,
            'assessment_policy': {
                'mcq_count': 15,
                'short_answer_count': 5,
                'marks_per_question': LEARNING_ASSESSMENT_QUESTION_MARKS,
                'total_marks': LEARNING_ASSESSMENT_TOTAL_MARKS,
                'source_required': True,
            },
        }
        try:
            provider = build_provider(settings)
            raw = provider.generate_topic_assessment(context)
            generated, issues, limitation = validate_assessment_response(raw, passages)
            if issues:
                context['validation_issues'] = issues
                context['repair_instruction'] = 'Repair the rejected output once. Follow every validation issue and retain only questions supported by the provided source passages.'
                repaired_raw = provider.generate_topic_assessment(context)
                repaired_questions, repaired_issues, repaired_limitation = validate_assessment_response(repaired_raw, passages)
                generated = repaired_questions
                issues = repaired_issues
                limitation = repaired_limitation
        except (RuntimeError, ValueError) as exc:
            error_message = str(exc)
            issues.append(f'Assessment generation is unavailable: {error_message}')
    for item in generated:
        evidence_refs = item['source_evidence']
        source_reference = ', '.join(dict.fromkeys(citation['ref'] for citation in evidence_refs))[:255]
        db.add(AssessmentQuestion(
            assessment_id=assessment.id,
            subject_id=assignment.subject_id,
            module_id=assignment.module_id,
            topic_id=topic.id,
            question_type=item['question_type'],
            question=item['question'],
            options=item['options'],
            correct_answer=item['correct_answer'],
            explanation=item['explanation'],
            learning_objective=item['learning_objective'],
            source_reference=source_reference,
            rubric=item['rubric'],
            marks=float(LEARNING_ASSESSMENT_QUESTION_MARKS),
            source_provenance={'evidence': evidence_refs, 'difficulty': item['difficulty'], 'approved_topic_id': topic.id},
        ))
    assessment.question_count = len(generated)
    assessment.error_message = error_message
    mcq_count = sum(item['question_type'] == 'MCQ' for item in generated)
    short_answer_count = sum(item['question_type'] == 'SHORT_ANSWER' for item in generated)
    total_marks = len(generated) * LEARNING_ASSESSMENT_QUESTION_MARKS
    if mcq_count != 15 or short_answer_count != 5 or total_marks != LEARNING_ASSESSMENT_TOTAL_MARKS:
        issues.append(
            'The assessment must contain 15 MCQs and 5 short-answer questions worth 5 marks each (100 total).'
        )
    assessment.validation_results = {
        'valid': not issues and mcq_count == 15 and short_answer_count == 5 and total_marks == LEARNING_ASSESSMENT_TOTAL_MARKS,
        'issues': issues,
        'limitation': limitation,
        'mcq_count': mcq_count,
        'short_answer_count': short_answer_count,
        'total_marks': total_marks,
    }
    _recovery_audit(db, user, 'learning_assessment.generated', 'learning_assessment', assessment.id, new_state='REVIEW_REQUIRED', reason='; '.join(issues)[:2000] or None)
    db.commit()
    db.refresh(assessment)
    questions = db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment.id).order_by(AssessmentQuestion.id).all()
    return {
        'id': assessment.id,
        'assignment_id': assessment.assignment_id,
        'version': assessment.version,
        'status': assessment.status,
        'question_count': assessment.question_count,
        'total_marks': total_marks,
        'validation_results': assessment.validation_results,
        'source_evidence': assessment.source_evidence,
        'error_message': assessment.error_message,
        'questions': [_learning_question_out(question, include_answer=True) for question in questions],
    }


@app.post(f'{settings.api_v1_prefix}/topic-learning-assignments/{{assignment_id}}/assessment')
def topic_learning_assignment_assessment(assignment_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    return generate_topic_assessment(assignment_id=assignment_id, user=user, db=db)


def _learning_question_out(question: AssessmentQuestion, *, include_answer: bool) -> dict[str, Any]:
    provenance = question.source_provenance if isinstance(question.source_provenance, dict) else {}
    result = {
        'id': question.id,
        'question_type': question.question_type,
        'question': question.question,
        'options': question.options,
        'marks': question.marks,
        'explanation': question.explanation,
        'learning_objective': question.learning_objective,
        'difficulty': provenance.get('difficulty', 'UNSPECIFIED'),
        'rubric': question.rubric,
        'source_evidence': provenance.get('evidence', []),
    }
    if include_answer:
        result['correct_answer'] = question.correct_answer
    return result


def _teacher_learning_assessment_access(assessment_id: int, user: User, db: Session) -> tuple[LearningAssessment, TopicLearningAssignment]:
    assessment = db.query(LearningAssessment).filter(LearningAssessment.id == assessment_id).first()
    if assessment is None:
        raise HTTPException(status_code=404, detail='Learning assessment not found')
    assignment = db.query(TopicLearningAssignment).filter(TopicLearningAssignment.id == assessment.assignment_id).first()
    if assignment is None or not _topic_assignment_scope_is_valid(assignment, db):
        raise HTTPException(status_code=404, detail='Learning assignment not found')
    _teacher_subject_access(db, teacher_user=user, subject_id=assignment.subject_id)
    _recovery_student_access(assignment.student_id, user, db)
    return assessment, assignment


@app.get(f'{settings.api_v1_prefix}/topic-learning-assignments/{{assignment_id}}/assessments')
def list_learning_assessments(assignment_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    assignment = db.query(TopicLearningAssignment).filter(TopicLearningAssignment.id == assignment_id).first()
    if assignment is None or not _topic_assignment_scope_is_valid(assignment, db):
        raise HTTPException(status_code=404, detail='Learning assignment not found')
    _teacher_subject_access(db, teacher_user=user, subject_id=assignment.subject_id)
    _recovery_student_access(assignment.student_id, user, db)
    assessments = db.query(LearningAssessment).filter(LearningAssessment.assignment_id == assignment_id).order_by(LearningAssessment.version.desc()).all()
    output = []
    for assessment in assessments:
        questions = db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment.id).order_by(AssessmentQuestion.id).all()
        output.append({
            'id': assessment.id, 'version': assessment.version, 'status': assessment.status,
            'question_count': assessment.question_count, 'validation_results': assessment.validation_results,
            'total_marks': sum(float(question.marks) for question in questions),
            'error_message': assessment.error_message, 'source_evidence': assessment.source_evidence,
            'questions': [_learning_question_out(question, include_answer=True) for question in questions],
            'created_at': assessment.created_at.isoformat(),
        })
    return output


@app.post(f'{settings.api_v1_prefix}/learning-assessments/{{assessment_id}}/approve')
def approve_learning_assessment(assessment_id: int, payload: LearningAssessmentReview, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    assessment, assignment = _teacher_learning_assessment_access(assessment_id, user, db)
    if assessment.status != 'REVIEW_REQUIRED':
        raise HTTPException(status_code=409, detail='Only assessments awaiting review can be approved.')
    validation = assessment.validation_results or {}
    questions = db.query(AssessmentQuestion).filter_by(
        assessment_id=assessment.id,
    ).order_by(AssessmentQuestion.id).all()
    mcq_count = sum(question.question_type == 'MCQ' for question in questions)
    short_answer_count = sum(question.question_type == 'SHORT_ANSWER' for question in questions)
    total_marks = sum(float(question.marks) for question in questions)
    issues = list(validation.get('issues') or [])
    if len(questions) != 20 or assessment.question_count != 20:
        issues.append('The persisted assessment must contain exactly 20 questions.')
    if mcq_count != 15 or short_answer_count != 5:
        issues.append('The persisted assessment must contain exactly 15 MCQs and 5 short-answer questions.')
    if any(question.question_type not in {'MCQ', 'SHORT_ANSWER'} for question in questions):
        issues.append('The persisted assessment contains an unsupported question type.')
    if any(
        not math.isclose(
            float(question.marks),
            float(LEARNING_ASSESSMENT_QUESTION_MARKS),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        for question in questions
    ):
        issues.append('Every persisted assessment question must be worth exactly five marks.')
    if not math.isclose(
        total_marks,
        float(LEARNING_ASSESSMENT_TOTAL_MARKS),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        issues.append('The persisted assessment must total exactly 100 marks.')
    if not validation.get('valid') or validation.get('total_marks') != LEARNING_ASSESSMENT_TOTAL_MARKS:
        issues.append('The generated assessment validation did not pass.')
    if any(
        question.subject_id != assignment.subject_id
        or question.module_id != assignment.module_id
        or question.topic_id != assignment.approved_topic_id
        for question in questions
    ):
        issues.append('Persisted questions must belong to the assigned subject, module, and approved topic.')
    if issues:
        raise HTTPException(status_code=422, detail={
            'message': 'Assessment validation must pass before approval.',
            'issues': list(dict.fromkeys(issues)),
        })
    assessment.status = 'APPROVED'
    assessment.approved_by_user_id = user.id
    assessment.approved_at = datetime.utcnow()
    assessment.review_reason = payload.reason
    _recovery_audit(db, user, 'learning_assessment.approved', 'learning_assessment', assessment.id, previous_state='REVIEW_REQUIRED', new_state='APPROVED', reason=payload.reason)
    db.commit()
    return {'id': assessment.id, 'status': assessment.status, 'approved_at': assessment.approved_at.isoformat()}


@app.post(f'{settings.api_v1_prefix}/learning-assessments/{{assessment_id}}/reject')
def reject_learning_assessment(assessment_id: int, payload: LearningAssessmentReview, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    assessment, _ = _teacher_learning_assessment_access(assessment_id, user, db)
    if assessment.status not in {'REVIEW_REQUIRED', 'APPROVED'}:
        raise HTTPException(status_code=409, detail='This assessment can no longer be rejected.')
    if not payload.reason or len(payload.reason.strip()) < 5:
        raise HTTPException(status_code=422, detail='Provide a reason of at least five characters.')
    previous = assessment.status
    assessment.status = 'REJECTED'
    assessment.review_reason = payload.reason.strip()
    _recovery_audit(db, user, 'learning_assessment.rejected', 'learning_assessment', assessment.id, previous_state=previous, new_state='REJECTED', reason=assessment.review_reason)
    db.commit()
    return {'id': assessment.id, 'status': assessment.status, 'review_reason': assessment.review_reason}


@app.post(f'{settings.api_v1_prefix}/learning-assessments/{{assessment_id}}/publish')
def publish_learning_assessment(assessment_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    assessment, _ = _teacher_learning_assessment_access(assessment_id, user, db)
    if assessment.status != 'APPROVED':
        raise HTTPException(status_code=409, detail='Approve a validated assessment before publishing it.')
    assessment.status = 'PUBLISHED'
    assessment.published_at = datetime.utcnow()
    _recovery_audit(db, user, 'learning_assessment.published', 'learning_assessment', assessment.id, previous_state='APPROVED', new_state='PUBLISHED')
    db.commit()
    return {'id': assessment.id, 'status': assessment.status, 'published_at': assessment.published_at.isoformat()}


@app.post(f'{settings.api_v1_prefix}/topic-learning-assignments/{{assignment_id}}/attempts')
def create_topic_learning_attempt(assignment_id: int, student: CurrentStudent, db: Session = Depends(get_db)) -> dict[str, Any]:
    assignment = _student_learning_assignment_access(assignment_id, student, db)
    if assignment.status == 'MASTERED':
        raise HTTPException(status_code=409, detail='This topic is already mastered.')
    session = db.query(LearningStudySession).filter_by(assignment_id=assignment.id, student_id=student.id).first()
    if session is None or session.completed_at is None or (datetime.utcnow() - session.started_at).total_seconds() < session.required_duration_minutes * 60:
        raise HTTPException(status_code=409, detail='Complete the required server-timed study duration before starting the assessment.')
    assessment = db.query(LearningAssessment).filter_by(assignment_id=assignment.id, status='PUBLISHED').order_by(LearningAssessment.version.desc()).first()
    if assessment is None:
        raise HTTPException(status_code=409, detail='No teacher-published assessment is available for this topic.')
    existing_attempt = db.query(AssessmentAttempt).filter_by(
        assignment_id=assignment.id, assessment_id=assessment.id, student_id=student.id, status='IN_PROGRESS',
    ).first()
    if existing_attempt:
        return {
            'id': existing_attempt.id, 'assignment_id': existing_attempt.assignment_id,
            'student_id': existing_attempt.student_id, 'status': existing_attempt.status,
            'question_count': len(existing_attempt.question_set.get('questions', [])),
            'question_set': existing_attempt.question_set,
        }
    questions = db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment.id).order_by(AssessmentQuestion.id).all()
    if len(questions) != 20:
        raise HTTPException(status_code=409, detail='The published assessment is incomplete and needs teacher review.')
    if any(
        question.subject_id != assignment.subject_id
        or question.module_id != assignment.module_id
        or question.topic_id != assignment.approved_topic_id
        for question in questions
    ):
        raise HTTPException(status_code=409, detail='The published assessment contains questions outside this subject topic.')
    attempt = AssessmentAttempt(
        assignment_id=assignment.id,
        assessment_id=assessment.id,
        study_session_id=session.id,
        student_id=student.id,
        question_set_version=assessment.version,
        status='IN_PROGRESS',
        question_set={'questions': [{
            'id': q.id, 'question_type': q.question_type, 'question': q.question,
            'options': q.options, 'marks': q.marks, 'learning_objective': q.learning_objective,
            'rubric': q.rubric, 'source_evidence': (q.source_provenance or {}).get('evidence', []),
        } for q in questions]},
        answer_key={str(q.id): {'correct_answer': q.correct_answer, 'marks': q.marks, 'question_type': q.question_type} for q in questions},
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return {
        'id': attempt.id,
        'assignment_id': attempt.assignment_id,
        'student_id': attempt.student_id,
        'status': attempt.status,
        'question_count': len(questions),
        'question_set': attempt.question_set,
    }


@app.post(f'{settings.api_v1_prefix}/assessment-attempts/{{attempt_id}}/submit')
def submit_topic_learning_attempt(attempt_id: int, payload: dict[str, Any], student: CurrentStudent, db: Session = Depends(get_db)) -> dict[str, Any]:
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id, AssessmentAttempt.student_id == student.id).first()
    if attempt is None:
        raise HTTPException(status_code=404, detail='Attempt not found')
    assignment = db.query(TopicLearningAssignment).filter_by(
        id=attempt.assignment_id,
        student_id=student.id,
    ).first()
    if assignment is None or not _topic_assignment_scope_is_valid(assignment, db):
        raise HTTPException(status_code=404, detail='Attempt not found')
    if attempt.status in {'SUBMITTED', 'AWAITING_REVIEW'}:
        return {'id': attempt.id, 'status': attempt.status, 'total_marks': attempt.total_marks, 'percentage': attempt.percentage}
    if attempt.status != 'IN_PROGRESS':
        raise HTTPException(status_code=409, detail='This assessment attempt is not open for submission.')
    assessment = db.query(LearningAssessment).filter_by(
        id=attempt.assessment_id,
        assignment_id=assignment.id,
        status='PUBLISHED',
    ).first()
    if assessment is None:
        raise HTTPException(status_code=409, detail='The assessment is no longer published.')
    session = db.query(LearningStudySession).filter_by(
        id=attempt.study_session_id,
        assignment_id=assignment.id,
        student_id=student.id,
    ).first()
    if session is None or session.completed_at is None or (datetime.utcnow() - session.started_at).total_seconds() < session.required_duration_minutes * 60:
        raise HTTPException(status_code=409, detail='The required study session is not complete.')
    try:
        submission = AssessmentAttemptSubmit.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    questions = db.query(AssessmentQuestion).filter(AssessmentQuestion.id.in_([int(key) for key in attempt.answer_key.keys()])).all()
    expected_ids = set(attempt.answer_key)
    if set(submission.answers) != expected_ids or len(questions) != 20:
        raise HTTPException(status_code=422, detail='Submit exactly one answer for each of the 20 assessment questions.')
    if any(
        question.assessment_id != assessment.id
        or question.subject_id != assignment.subject_id
        or question.module_id != assignment.module_id
        or question.topic_id != assignment.approved_topic_id
        for question in questions
    ):
        raise HTTPException(status_code=409, detail='Assessment attempt questions do not match the assigned subject topic.')
    questions_by_id = {question.id: question for question in questions}
    total_marks = 0.0
    responses = []
    requires_review = False
    for key, value in submission.answers.items():
        question_id = int(key)
        question = questions_by_id.get(question_id)
        if question is None:
            raise HTTPException(status_code=422, detail='The submission contains an unknown question.')
        review_required = False
        review_reason = None
        rubric_evaluation: dict[str, Any] = {}
        if question.question_type == 'MCQ':
            selected = value.strip()
            is_correct = selected == question.correct_answer
            marks = float(question.marks) if is_correct else 0.0
            feedback = 'Correct.' if is_correct else 'Incorrect. Review the approved source material and try again in a future attempt.'
            source_reference = question.source_reference
        else:
            response_text = value.strip()
            source_reference = question.source_reference
            context = {
                'question': question.question,
                'student_answer': response_text,
                'reference_answer': question.correct_answer,
                'rubric': question.rubric,
                'source_evidence': (question.source_provenance or {}).get('evidence', []),
            }
            try:
                provider = build_provider(settings)
                rubric_evaluation = validate_short_answer_evaluation(provider.evaluate_short_answer(context))
                marks = min(float(question.marks), float(question.marks) * rubric_evaluation['marks_awarded'])
                feedback = rubric_evaluation['feedback']
                review_required = rubric_evaluation['needs_review']
                review_reason = 'Evaluator confidence below policy threshold or answer flagged as ambiguous.' if review_required else None
                is_correct = marks >= float(question.marks)
            except (RuntimeError, ValueError) as exc:
                marks = 0.0
                feedback = 'Automated grading was unavailable; a teacher must review this response.'
                review_required = True
                review_reason = str(exc)[:1000]
                rubric_evaluation = {'needs_review': True, 'provider_error': str(exc)[:1000]}
                is_correct = None
            requires_review = requires_review or review_required
        total_marks += marks
        response_record = AssessmentAttemptAnswer(
            attempt_id=attempt.id,
            question_id=question.id,
            selected_choice=str(value) if question.question_type == 'MCQ' else None,
            response_text=str(value) if question.question_type != 'MCQ' else None,
            marks_awarded=marks,
            original_marks_awarded=marks,
            is_correct=is_correct,
            review_required=review_required,
            feedback=feedback,
            review_reason=review_reason,
            rubric_evaluation=rubric_evaluation,
            source_reference=source_reference,
        )
        db.add(response_record)
        responses.append({'question_id': question.id, 'marks_awarded': marks, 'feedback': feedback})
    attempt.status = 'AWAITING_REVIEW' if requires_review else 'SUBMITTED'
    attempt.total_marks = total_marks
    maximum_marks = _attempt_maximum_marks(attempt)
    attempt.percentage = round((total_marks / maximum_marks) * 100, 2) if maximum_marks else 0
    attempt.submitted_at = datetime.utcnow()
    attempt.evaluated_at = datetime.utcnow()
    if not requires_review:
        assignment = db.query(TopicLearningAssignment).filter_by(id=attempt.assignment_id).first()
        if assignment:
            assignment.status = 'MASTERED' if attempt.percentage >= 80 else 'REVISION_REQUIRED'
    db.commit()
    return {'id': attempt.id, 'status': attempt.status, 'total_marks': attempt.total_marks, 'percentage': attempt.percentage, 'review_pending': requires_review, 'responses': responses}


@app.get(f'{settings.api_v1_prefix}/teacher/learning-monitoring')
def teacher_learning_monitoring(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    subject_ids = _teacher_assigned_subject_ids(db, user)
    if not subject_ids:
        return {'topic_count': 0, 'assignment_count': 0, 'resource_count': 0, 'status': 'active'}
    topic_count = db.query(ApprovedTopic).filter(ApprovedTopic.subject_id.in_(subject_ids)).count()
    assignments = db.query(TopicLearningAssignment).filter(TopicLearningAssignment.subject_id.in_(subject_ids)).count()
    resources = db.query(SubjectModuleResource).filter(SubjectModuleResource.subject_id.in_(subject_ids)).count()
    return {'topic_count': topic_count, 'assignment_count': assignments, 'resource_count': resources, 'status': 'active'}


@app.get(f'{settings.api_v1_prefix}/academic-register')
def academic_register(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    search: str | None = None,
    academic_period: str | None = None,
    offset: int = 0,
    limit: int = 500,
) -> dict[str, Any]:
    try:
        period = selected_academic_period(db, academic_period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    query = db.query(Student)
    if search:
        query = query.filter(Student.full_name.ilike(f'%{search}%') | Student.student_id.ilike(f'%{search}%'))
    students = query.order_by(Student.student_id).all()
    total = len(students)
    page = students[max(offset, 0):max(offset, 0) + min(max(limit, 1), 500)]
    records = subject_records_by_student(db, [student.id for student in page], period)
    subjects = db.query(Subject).filter(Subject.code.in_(SUBJECTS)).order_by(Subject.code).all()
    items = []
    for student in page:
        row = build_register_row(student, records.get(student.id, {}), period)
        current, _ = student_risk_view(student, db)
        row.update({
            'risk_level': current['risk_level'],
            'risk_probability': current['risk_probability'],
            'risk_status': current['risk_status'],
            'risk_status_message': current['risk_status_message'],
        })
        items.append(row)
    return {
        'subjects': [{'code': subject.code, 'name': subject.name} for subject in subjects],
        'academic_period': period,
        'academic_periods': academic_periods(db),
        'items': items,
        'total': total,
        'offset': max(offset, 0),
        'limit': min(max(limit, 1), 500),
        'risk_scope': 'global model; subject marks are academic context and are not model features',
    }


def student_subject_records(student: Student, db: Session, academic_period: str | None = None) -> dict[str, Any]:
    try:
        period = selected_academic_period(db, academic_period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    records = subject_records_by_student(db, [student.id], period).get(student.id, {})
    subjects = db.query(Subject).filter(Subject.code.in_(SUBJECTS)).order_by(Subject.code).all()
    return {
        'student_id': student.student_id,
        'academic_period': period,
        'academic_periods': academic_periods(db),
        'records': subject_record_evidence(db, student.id, period),
        'subjects': {subject.code: {'name': subject.name, **serialize_record(records.get(subject.code))} for subject in subjects},
        **academic_totals(records),
        'risk_scope': 'global model; subject marks are not model features',
    }


@app.get(f'{settings.api_v1_prefix}/students/me/academic-records')
def my_academic_records(student: CurrentStudent, db: Session = Depends(get_db), academic_period: str | None = None) -> dict[str, Any]:
    return student_subject_records(student, db, academic_period)


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/academic-records')
def get_student_academic_records(student_id: int, user: CurrentTeacher, db: Session = Depends(get_db), academic_period: str | None = None) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    return student_subject_records(student, db, academic_period)


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/subjects/{{subject_code}}/assignments')
def student_subject_assignments(student_id: int, subject_code: str, user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    student = db.query(Student).filter(Student.id == student_id).first()
    subject = db.query(Subject).filter(Subject.code == subject_code.upper(), Subject.code.in_(SUBJECTS)).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    if subject is None:
        raise HTTPException(status_code=404, detail='Subject not found')
    assignments = db.query(Assignment).filter(Assignment.subject_id == subject.id).order_by(Assignment.due_date).all()
    result = []
    for assignment in assignments:
        submission = db.query(Submission).filter_by(student_id=student.id, assignment_id=assignment.id).first()
        result.append({
            'id': assignment.id,
            'title': assignment.title,
            'subject_code': subject.code,
            'status': assignment.status,
            'due_date': assignment.due_date.isoformat() if assignment.due_date else None,
            'max_marks': assignment.max_marks,
            'submission_status': submission.status if submission else 'not_submitted',
            'uploaded': bool(submission and submission.stored_path),
            'file_name': submission.file_name if submission else None,
            'grade': submission.grade if submission else None,
            'feedback': submission.feedback if submission else None,
            'submitted_at': submission.submitted_at.isoformat() if submission and submission.submitted_at else None,
        })
    return result


@app.patch(f'{settings.api_v1_prefix}/academic-register/{{student_id}}/{{subject_code}}')
def update_academic_record(
    student_id: int,
    subject_code: str,
    payload: SubjectAcademicRecordUpdate,
    user: CurrentTeacher,
    academic_period: str = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == student_id).first()
    subject = db.query(Subject).filter(Subject.code == subject_code.upper(), Subject.code.in_(SUBJECTS)).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    if subject is None:
        raise HTTPException(status_code=404, detail='Subject not found')
    try:
        period = normalize_academic_period(academic_period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record = db.query(StudentSubjectAcademicRecord).filter_by(student_id=student.id, subject_id=subject.id, academic_period=period).first()
    if record is None:
        record = StudentSubjectAcademicRecord(student_id=student.id, subject_id=subject.id, academic_period=period)
        db.add(record)
        db.flush()
    updates = payload.model_dump(exclude_unset=True)
    if updates.get('fa1') is not None:
        fa1_maximum = record.fa1_maximum_marks if record.fa1_maximum_marks is not None else 25
        if updates['fa1'] > fa1_maximum:
            raise HTTPException(
                status_code=422,
                detail=f'fa1 must not exceed the recorded maximum of {fa1_maximum}.',
            )
    old_values = {field: getattr(record, field) for field in updates}
    for field, value in updates.items():
        setattr(record, field, value)
    if 'fa1' in updates:
        record.temporal_evidence_verified = False
    record.updated_by_user_id = user.id
    db.add(StudentSubjectAcademicRecordHistory(
        academic_record_id=record.id,
        changed_by_user_id=user.id,
        change_kind='manual',
        fa1=record.fa1,
        fa1_observed_at=record.fa1_observed_at,
        fa1_maximum_marks=record.fa1_maximum_marks,
        fa1_maximum_verified=record.fa1_maximum_verified,
        fa2=record.fa2,
        fa2_observed_at=record.fa2_observed_at,
        assignment_mark=record.assignment_mark,
        assignment_observed_at=record.assignment_observed_at,
        attendance_rate=record.attendance_rate,
        attendance_observed_at=record.attendance_observed_at,
    ))
    db.add(AuditLog(
        actor_user_id=user.id,
        action='academic_subject_record.updated',
        resource_type='student_subject_academic_record',
        resource_id=f'{student.id}:{subject.code}:{period}',
        detail={'student_id': student.student_id, 'subject_code': subject.code, 'academic_period': period, 'old_values': old_values, 'new_values': updates},
    ))
    db.commit()
    db.refresh(record)
    records = subject_records_by_student(db, [student.id], period).get(student.id, {})
    register_row = build_register_row(student, records, period)
    current_student, _ = student_risk_view(student, db)
    register_row.update({
        'risk_level': current_student['risk_level'],
        'risk_probability': current_student['risk_probability'],
        'risk_status': current_student['risk_status'],
        'risk_status_message': current_student['risk_status_message'],
    })
    return {
        'student_id': student.student_id,
        'subject_code': subject.code,
        'academic_period': period,
        **serialize_record(record),
        'register_row': register_row,
    }


@app.post(f'{settings.api_v1_prefix}/academic-register/{{student_id}}/{{subject_code}}/fa1-maximum-verification')
def verify_fa1_maximum_evidence(
    student_id: int,
    subject_code: str,
    payload: Fa1MaximumEvidenceReview,
    user: CurrentTeacher,
    academic_period: str = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == student_id).first()
    subject = db.query(Subject).filter(
        Subject.code == subject_code.upper(),
        Subject.code.in_(SUBJECTS),
    ).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    if subject is None:
        raise HTTPException(status_code=404, detail='Subject not found')
    try:
        period = normalize_academic_period(academic_period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _, teacher = _teacher_subject_access(db, teacher_user=user, subject_id=subject.id)
    assigned_course = db.query(Course).filter_by(
        id=student.course_id,
        subject_id=subject.id,
        teacher_id=teacher.id,
    ).first()
    if assigned_course is None:
        raise HTTPException(status_code=403, detail='Teacher is not assigned to this student and subject')

    record = db.query(StudentSubjectAcademicRecord).filter_by(
        student_id=student.id,
        subject_id=subject.id,
        academic_period=period,
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail='Subject academic record not found')
    batch = (
        db.query(AcademicDataImportBatch)
        .filter(AcademicDataImportBatch.id == record.import_batch_id)
        .first()
        if record.import_batch_id is not None
        else None
    )
    if record.fa1 is None:
        raise HTTPException(status_code=409, detail='A recorded FA-1 mark is required before maximum evidence can be reviewed')
    if record.fa1_observed_at is None or not record.temporal_evidence_verified:
        raise HTTPException(status_code=409, detail='A dated, temporally verified FA-1 observation is required')
    if (
        record.fa1_maximum_marks is None
        or not math.isfinite(record.fa1_maximum_marks)
        or record.fa1_maximum_marks <= 0
        or record.fa1 > record.fa1_maximum_marks
    ):
        raise HTTPException(status_code=409, detail='A valid recorded FA-1 maximum that covers the score is required')
    if batch is None or not batch.source_filename.strip():
        raise HTTPException(status_code=409, detail='Imported source provenance is required before maximum evidence can be reviewed')
    if payload.source_reference.casefold() == batch.source_filename.strip().casefold():
        raise HTTPException(status_code=409, detail='Maximum review must cite an independent source, not only the imported filename')

    record.fa1_maximum_verified = True
    record.updated_by_user_id = user.id
    db.add(StudentSubjectAcademicRecordHistory(
        academic_record_id=record.id,
        import_batch_id=record.import_batch_id,
        changed_by_user_id=user.id,
        change_kind='fa1_maximum_verified',
        fa1=record.fa1,
        fa1_observed_at=record.fa1_observed_at,
        fa1_maximum_marks=record.fa1_maximum_marks,
        fa1_maximum_verified=True,
        fa2=record.fa2,
        fa2_observed_at=record.fa2_observed_at,
        assignment_mark=record.assignment_mark,
        assignment_observed_at=record.assignment_observed_at,
        attendance_rate=record.attendance_rate,
        attendance_observed_at=record.attendance_observed_at,
    ))
    db.add(AuditLog(
        actor_user_id=user.id,
        action='academic_subject_record.fa1_maximum_verified',
        resource_type='student_subject_academic_record',
        resource_id=str(record.id),
        detail={
            'student_id': student.student_id,
            'subject_id': subject.id,
            'subject_code': subject.code,
            'academic_period': period,
            'fa1': record.fa1,
            'fa1_observed_at': record.fa1_observed_at.isoformat(),
            'fa1_maximum_marks': record.fa1_maximum_marks,
            'import_batch_id': batch.id,
            'import_source_filename': batch.source_filename,
            'source_reference': payload.source_reference,
        },
    ))
    db.commit()
    return next(
        item for item in subject_record_evidence(db, student.id, period)
        if item['record_id'] == record.id
    )


@app.get(f'{settings.api_v1_prefix}/me')
def current_profile(user: CurrentUser) -> dict[str, Any]:
    return {'id': user.id, 'email': user.email, 'name': user.full_name, 'role': user.role.upper()}


@app.get(f'{settings.api_v1_prefix}/dashboard/teacher')
def teacher_dashboard(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    students = db.query(Student).order_by(Student.full_name).all()
    risk_views, _, _ = student_risk_views(students, db)
    current_students = [student_data for student_data, _ in risk_views]
    scorable_students = [
        item for item in current_students
        if item['risk_status'] in {'available', 'unvalidated-demo-model'}
    ]
    demo_model = early_warning_uses_demo_model(db)
    risk_status = 'unvalidated-demo-model' if demo_model else (
        'partial' if len(scorable_students) < len(students) else 'available'
    )
    risk_status_message = (
        'Risk distribution is an unvalidated demo preview and includes only records explicitly marked DEMO DATA.'
        if demo_model else (
            f'{len(students) - len(scorable_students)} record(s) are excluded because they lack sufficient risk-model data.'
            if len(scorable_students) < len(students) else None
        )
    )
    assignments = db.query(Assignment).filter(Assignment.status == 'active').all()
    submissions = db.query(Submission).all()
    pending_reviews = sum(item.status in {'submitted', 'late'} and item.grade is None for item in submissions)
    drafts = db.query(InterventionDraft).filter(InterventionDraft.status == 'draft').count()
    now = datetime.utcnow()
    return {
        'teacher_name': user.full_name,
        'summary': {
            'students': len(students),
            'assignments': len(assignments),
            'submissions': len(submissions),
            'pending_reviews': pending_reviews,
            'intervention_drafts': drafts,
            'high_risk': sum(item['risk_level'] == 'HIGH' for item in current_students),
            'medium_risk': sum(item['risk_level'] == 'MEDIUM' for item in current_students),
            'low_risk': sum(item['risk_level'] == 'LOW' for item in current_students),
            'risk_scored_students': len(scorable_students),
        },
        'risk_status': risk_status,
        'risk_status_message': risk_status_message,
        'students_needing_attention': [item for item in scorable_students if item['risk_level'] in {'HIGH', 'MEDIUM'}][:12],
        'assignments': [AssignmentOut.model_validate(item).model_dump() for item in assignments[:6]],
        'upcoming_deadlines': [AssignmentOut.model_validate(item).model_dump() for item in assignments if item.due_date and item.due_date >= now][:8],
    }


@app.get(f'{settings.api_v1_prefix}/dashboard/student')
def student_dashboard(student: CurrentStudent, db: Session = Depends(get_db)) -> dict[str, Any]:
    submissions = db.query(Submission).filter(Submission.student_id == student.id).all()
    assigned_ids = select(AssignmentStudent.assignment_id).where(AssignmentStudent.student_id == student.id)
    assignments = db.query(Assignment).filter(
        (Assignment.status == 'active') & ((Assignment.id.in_(assigned_ids)) | (Assignment.section == student.section))
    ).all()
    unread = db.query(Notification).filter(Notification.user_id == student_user_id(db, student.id), Notification.read_at.is_(None)).count()
    return {
        'student': StudentSelfOut.model_validate(student).model_dump(),
        'assignments': [AssignmentOut.model_validate(item).model_dump() for item in assignments],
        'recent_submissions': [SubmissionOut.model_validate(item).model_dump() for item in submissions],
        'unread_notifications': unread,
    }


@app.get(f'{settings.api_v1_prefix}/students')
def list_students(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    search: str | None = None,
    risk: str | None = None,
    department: str | None = None,
    program: str | None = None,
    semester: str | None = None,
    section: str | None = None,
    course: str | None = None,
    momentum: str | None = None,
    attendance_min: float | None = None,
    attendance_max: float | None = None,
    sort: str = 'full_name',
    direction: str = 'asc',
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    query = db.query(Student)
    if search:
        search_value = search.strip()
        if search_value:
            query = query.filter(Student.full_name.ilike(f'%{search_value}%') | Student.student_id.ilike(f'%{search_value}%'))
    if department:
        query = query.filter(Student.department == department)
    if program:
        query = query.filter(Student.program == program)
    if semester:
        query = query.filter(Student.semester == semester)
    if section:
        query = query.filter(Student.section == section)
    if course:
        query = query.filter(Student.course.has(Course.code == course))
    students = query.all()
    risk_views, _, momentum_by_student = student_risk_views(students, db)
    response_items = []
    for values, _ in risk_views:
        if attendance_min is not None and values['attendance'] < attendance_min:
            continue
        if attendance_max is not None and values['attendance'] > attendance_max:
            continue
        if risk and values['risk_level'] != risk.upper():
            continue
        values['momentum_trend'] = momentum_by_student[values['id']].get('assessment', calculate_momentum([]))['classification']
        if momentum and values['momentum_trend'] != momentum.upper():
            continue
        response_items.append(values)
    sortable = {
        'full_name',
        'student_id',
        'department',
        'semester',
        'risk_level',
        'risk_probability',
        'attendance',
        'current_gpa',
        'assignments_completed',
    }
    sort_key = sort if sort in sortable else 'full_name'
    response_items.sort(
        key=lambda item: (item.get(sort_key) is None, item.get(sort_key)),
        reverse=direction.lower() == 'desc',
    )
    total = len(response_items)
    items = response_items[offset:offset + limit]
    return {'items': items, 'total': total}


def _management_student(student_identifier: str, db: Session) -> Student:
    query = db.query(Student)
    if student_identifier.isdigit():
        query = query.filter(Student.id == int(student_identifier))
    else:
        query = query.filter(Student.student_id == student_identifier)
    student = query.first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found.')
    return student


def _validate_student_course(course_id: int | None, db: Session) -> None:
    if course_id is not None and db.query(Course.id).filter(Course.id == course_id).first() is None:
        raise HTTPException(status_code=422, detail='The selected course does not exist.')


def _validate_student_unique_fields(
    student_id: str | None,
    email: str | None,
    db: Session,
    *,
    exclude_student_id: int | None = None,
) -> None:
    student_id_query = db.query(Student.id).filter(func.lower(Student.student_id) == student_id.lower()) if student_id else None
    email_query = db.query(Student.id).filter(func.lower(Student.email) == email.lower()) if email else None
    if exclude_student_id is not None:
        if student_id_query is not None:
            student_id_query = student_id_query.filter(Student.id != exclude_student_id)
        if email_query is not None:
            email_query = email_query.filter(Student.id != exclude_student_id)
    if student_id_query is not None and student_id_query.first():
        raise HTTPException(status_code=409, detail='That student ID is already in use.')
    if email_query is not None and email_query.first():
        raise HTTPException(status_code=409, detail='That email address is already in use.')


def _student_model_signal_count(student: Student, db: Session) -> int:
    return student_model_signal_counts([student], db)[student.id]


def _reassess_student_after_change(student: Student, user: User, db: Session) -> dict[str, Any]:
    signal_count = _student_model_signal_count(student, db)
    if signal_count < MINIMUM_USABLE_SIGNALS:
        return {
            'status': 'insufficient-data',
            'usable_signals': signal_count,
            'required_signals': MINIMUM_USABLE_SIGNALS,
            'message': 'Academic data was saved, but there are not enough model-supported signals to reassess risk.',
        }

    try:
        demo_model = early_warning_uses_demo_model(db)
        if demo_model:
            if student.data_origin != 'DEMO DATA':
                return {
                    'status': 'unavailable',
                    'usable_signals': signal_count,
                    'required_signals': MINIMUM_USABLE_SIGNALS,
                    'message': 'The active model is trained on DEMO DATA and cannot assess this non-demo student record.',
                }
            prediction = predict_with_demo_preview(student_features(student, db))
            return {
                'status': 'unvalidated-demo-model',
                'usable_signals': signal_count,
                'required_signals': MINIMUM_USABLE_SIGNALS,
                'message': 'The result is an unvalidated demo preview and was not saved as a validated risk prediction.',
                'prediction': prediction,
            }
        features = student_features(student, db)
        prediction = predict_with_active_model(features, db)
    except FileNotFoundError:
        return {
            'status': 'unavailable',
            'usable_signals': signal_count,
            'required_signals': MINIMUM_USABLE_SIGNALS,
            'message': 'Academic data was saved, but the active risk model is unavailable.',
        }
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        return {
            'status': 'unavailable',
            'usable_signals': signal_count,
            'required_signals': MINIMUM_USABLE_SIGNALS,
            'message': str(exc.detail),
        }

    student.risk_level = prediction['risk_level']
    student.risk_probability = prediction['risk_probability']
    db.add(RiskPrediction(
        student_id=student.id,
        risk_level=prediction['risk_level'],
        probability=prediction['risk_probability'],
        model_version=prediction['model_version'],
        prediction_timestamp=prediction['prediction_timestamp'],
        input_features=features,
    ))
    db.add(RiskSnapshot(
        student_id=student.id,
        model_version=prediction['model_version'],
        risk_level=prediction['risk_level'],
        probability=prediction['risk_probability'],
        recorded_at=prediction['prediction_timestamp'].replace(tzinfo=None),
    ))
    audit(db, user, 'student.risk_reassessed', 'student', student.id, {
        'risk_level': prediction['risk_level'],
        'model_version': prediction['model_version'],
    })
    db.commit()
    return {
        'status': 'available',
        'usable_signals': signal_count,
        'required_signals': MINIMUM_USABLE_SIGNALS,
        'prediction': prediction,
    }


def _record_management_attendance(student: Student, user: User, db: Session) -> None:
    observed_at = datetime.utcnow()
    fingerprint = hashlib.sha256(
        f'{student.id}:{observed_at.isoformat()}:{student.attendance}'.encode('utf-8')
    ).hexdigest()
    batch = AcademicDataImportBatch(
        dataset_type='student_record_update',
        source_filename='teacher-student-edit',
        file_checksum=fingerprint,
        imported_by_user_id=user.id,
        imported_rows=1,
        rejected_rows=0,
        duplicate_rows=0,
        validation_errors=[],
    )
    db.add(batch)
    db.flush()
    row_checksum = hashlib.sha256(f'{fingerprint}:attendance'.encode('utf-8')).hexdigest()
    db.add(AcademicDataImportRow(
        batch_id=batch.id,
        dataset_type='student_record_update',
        row_checksum=row_checksum,
        row_number=1,
        status='imported',
        student_id=student.id,
        errors=[],
    ))
    db.add(AcademicMomentum(
        student_id=student.id,
        import_batch_id=batch.id,
        metric='attendance',
        value=student.attendance,
        observed_at=observed_at,
    ))


@app.get(f'{settings.api_v1_prefix}/students/options')
def student_directory_options(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    def distinct_values(column: Any) -> list[str]:
        values = db.query(column).filter(column.is_not(None), column != '').distinct().all()
        return sorted(value for (value,) in values if value)

    courses = db.query(Course).order_by(Course.code).all()
    return {
        'departments': distinct_values(Student.department),
        'programs': distinct_values(Student.program),
        'semesters': distinct_values(Student.semester),
        'sections': distinct_values(Student.section),
        'courses': [{'id': item.id, 'code': item.code, 'name': item.name} for item in courses],
    }


@app.get(f'{settings.api_v1_prefix}/students/stats', response_model=StudentStatsOut)
def student_management_stats(user: CurrentTeacher, db: Session = Depends(get_db)) -> StudentStatsOut:
    students = db.query(Student).all()
    origins: dict[str, int] = {}
    for student in students:
        origins[student.data_origin] = origins.get(student.data_origin, 0) + 1

    high_risk: int | None = None
    medium_risk: int | None = None
    low_risk: int | None = None
    attention: int | None = None
    prediction_status = 'available'
    prediction_message: str | None = None
    try:
        risk_views, _, _ = student_risk_views(students, db) if students else ([], {}, {})
        high_risk = sum(prediction['risk_level'] == 'HIGH' for _, prediction in risk_views)
        medium_risk = sum(prediction['risk_level'] == 'MEDIUM' for _, prediction in risk_views)
        low_risk = sum(prediction['risk_level'] == 'LOW' for _, prediction in risk_views)
        attention = high_risk + medium_risk
        incomplete_count = sum(
            values['risk_status'] in {'unavailable', 'insufficient-data'}
            for values, _ in risk_views
        )
        demo_model = early_warning_uses_demo_model(db)
        if demo_model:
            prediction_status = 'unvalidated-demo-model'
            prediction_message = (
                'Unvalidated DEMO DATA previews are counted only for demo-origin records; other records are not scored.'
            )
        elif incomplete_count:
            prediction_status = 'partial' if incomplete_count < len(students) else 'unavailable'
            prediction_message = f'{incomplete_count} record(s) lack sufficient model-supported signals and are excluded from risk counts.'
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        prediction_status = 'unavailable'
        prediction_message = str(exc.detail)

    now = datetime.utcnow()
    return StudentStatsOut(
        total=len(students),
        high_risk=high_risk,
        medium_risk=medium_risk,
        low_risk=low_risk,
        average_attendance=round(sum(student.attendance for student in students) / len(students), 2) if students else None,
        average_gpa=round(sum(student.current_gpa for student in students) / len(students), 2) if students else None,
        attention=attention,
        recently_added=sum(
            student.created_at is not None and student.created_at >= now - timedelta(days=30)
            for student in students
        ),
        by_origin=origins,
        prediction_status=prediction_status,
        prediction_message=prediction_message,
    )


@app.post(f'{settings.api_v1_prefix}/students', status_code=201)
def create_student_record(
    payload: StudentCreate,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    values = payload.model_dump()
    _validate_student_unique_fields(values['student_id'], values['email'], db)
    _validate_student_course(values['course_id'], db)
    student = Student(**values, data_origin='MANUALLY ADDED')
    db.add(student)
    try:
        db.flush()
        audit(db, user, 'student.created', 'student', student.id, {
            'data_origin': student.data_origin,
            'fields': sorted(values),
        })
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail='A student with that ID or email already exists.') from exc
    db.refresh(student)
    reassessment = _reassess_student_after_change(student, user, db)
    return {
        'student': StudentRecordOut.model_validate(student).model_dump(),
        'risk_reassessment': reassessment,
    }


@app.put(f'{settings.api_v1_prefix}/students/{{student_id}}')
def update_student_record(
    student_id: str,
    payload: StudentUpdate,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    student = _management_student(student_id, db)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail='Provide at least one student field to update.')
    _validate_student_unique_fields(
        updates.get('student_id'),
        updates.get('email'),
        db,
        exclude_student_id=student.id,
    )
    if 'course_id' in updates:
        _validate_student_course(updates['course_id'], db)

    attendance_changed = 'attendance' in updates and updates['attendance'] != student.attendance
    for field, value in updates.items():
        setattr(student, field, value)
    if attendance_changed:
        _record_management_attendance(student, user, db)
    audit(db, user, 'student.updated', 'student', student.id, {
        'fields': sorted(updates),
        'data_origin': student.data_origin,
    })
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail='A student with that ID or email already exists.') from exc
    db.refresh(student)
    reassessment = _reassess_student_after_change(student, user, db)
    return {
        'student': StudentRecordOut.model_validate(student).model_dump(),
        'risk_reassessment': reassessment,
    }


@app.delete(f'{settings.api_v1_prefix}/students/{{student_id}}')
def delete_student_record(
    student_id: str,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    student = _management_student(student_id, db)
    audit(db, user, 'student.deleted', 'student', student.id, {
        'student_id': student.student_id,
        'data_origin': student.data_origin,
    })
    db.delete(student)
    db.commit()
    return {'deleted': True}


@app.post(f'{settings.api_v1_prefix}/students/import', response_model=StudentCsvImportResult)
async def import_student_records_csv(
    user: CurrentTeacher,
    file: UploadFile = File(...),
    data_origin: str = Form(...),
    source_description: str = Form(default=''),
    source_verified: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if data_origin not in STUDENT_IMPORT_ORIGINS:
        raise HTTPException(status_code=422, detail='Choose either DEMO DATA or INSTITUTIONAL IMPORT.')
    content = await file.read(STUDENT_IMPORT_MAX_FILE_BYTES + 1)
    await file.close()
    try:
        result = import_student_records(
            db,
            user_id=user.id,
            file_name=file.filename or 'student-records.csv',
            content=content,
            data_origin=data_origin,
            source_description=source_description,
            source_verified=source_verified,
        )
    except StudentRecordImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StudentRecordConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, user, 'student.csv_imported', 'student_import_batch', result['batch_id'], {
        'source_provenance': result['source_provenance'],
        'imported': result['imported'],
        'rejected': result['rejected'],
        'duplicates': result['duplicates'],
        'duplicate_import': result['duplicate_import'],
    })
    db.commit()
    return result


@app.get(f'{settings.api_v1_prefix}/students/export.csv')
def export_student_records_csv(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    search: str | None = None,
    department: str | None = None,
    program: str | None = None,
    semester: str | None = None,
    section: str | None = None,
    course: str | None = None,
) -> StreamingResponse:
    query = db.query(Student)
    if search and search.strip():
        search_value = search.strip()
        query = query.filter(Student.full_name.ilike(f'%{search_value}%') | Student.student_id.ilike(f'%{search_value}%'))
    if department:
        query = query.filter(Student.department == department)
    if program:
        query = query.filter(Student.program == program)
    if semester:
        query = query.filter(Student.semester == semester)
    if section:
        query = query.filter(Student.section == section)
    if course:
        query = query.filter(Student.course.has(Course.code == course))

    students = query.order_by(Student.full_name).all()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([
        'student_id',
        'full_name',
        'email',
        'program',
        'year',
        'semester',
        'section',
        'department',
        'course_code',
        'previous_gpa',
        'current_gpa',
        'attendance',
        'assessment',
        'assignments_completed',
        'data_origin',
    ])
    for student in students:
        writer.writerow([
            student.student_id,
            student.full_name,
            student.email or '',
            student.program or '',
            student.year or '',
            student.semester or '',
            student.section or '',
            student.department or '',
            student.course.code if student.course else '',
            student.previous_gpa,
            student.current_gpa,
            student.attendance,
            student.assessment,
            student.assignments_completed,
            student.data_origin,
        ])
    audit(db, user, 'student.csv_exported', 'student', detail={'rows': len(students)})
    db.commit()
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename="student-records.csv"'},
    )


@app.get(f'{settings.api_v1_prefix}/students/me')
def my_student_profile(student: CurrentStudent) -> StudentSelfOut:
    return StudentSelfOut.model_validate(student)


@app.get(f'{settings.api_v1_prefix}/students/me/momentum')
def my_student_momentum(student: CurrentStudent, db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(AcademicMomentum).filter(AcademicMomentum.student_id == student.id).order_by(AcademicMomentum.observed_at).all()
    grouped: dict[str, list[AcademicMomentum]] = {}
    for row in rows:
        grouped.setdefault(row.metric, []).append(row)
    return {'metrics': {metric: {'classification': calculate_momentum([item.value for item in history])['classification'], 'observations': [{'value': item.value, 'observed_at': item.observed_at.isoformat()} for item in history]} for metric, history in grouped.items()}}


@app.get(f'{settings.api_v1_prefix}/subjects/me')
def my_subjects(student: CurrentStudent, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if student.course_id is None:
        return []
    course = student.course
    subject = db.query(Subject).filter(Subject.id == course.subject_id).first() if course and course.subject_id else None
    assessments = db.query(Assessment).filter(Assessment.course_id == course.id).all() if course else []
    results = []
    for assessment in assessments:
        mark = db.query(Mark).filter(Mark.student_id == student.id, Mark.assessment_id == assessment.id).order_by(Mark.recorded_at.desc()).first()
        results.append({'assessment': assessment.title, 'score': mark.score if mark else None, 'maximum': assessment.maximum_marks, 'recorded_at': mark.recorded_at.isoformat() if mark else None})
    attendance_rows = db.query(Attendance).filter(Attendance.student_id == student.id, Attendance.course_id == course.id).all() if course else []
    recorded_rates = [item.attendance_rate for item in attendance_rows if item.attendance_rate is not None]
    attendance_rate = sum(recorded_rates) / len(recorded_rates) if recorded_rates else sum(item.attended for item in attendance_rows) / len(attendance_rows) * 100 if attendance_rows else None
    teacher_name = course.teacher.user.full_name if course and course.teacher and course.teacher.user else None
    return [{
        'course': course.name,
        'course_code': course.code,
        'subject_id': subject.id if subject else None,
        'section': course.section,
        'subject': subject.name if subject else course.name,
        'teacher': teacher_name,
        'attendance': attendance_rate,
        'assessments': results,
    }]


@app.get(f'{settings.api_v1_prefix}/support/resources')
def student_support_resources(student: CurrentStudent, query: str = 'academic support study planning mentoring') -> dict[str, Any]:
    result = retrieve(query)
    return {'student_id': student.student_id, **result}


def _student_access(student_id: int, user: CurrentUser, db: Session) -> Student:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    if user.role.upper() == 'STUDENT' and student.user_id != user.id:
        raise HTTPException(status_code=404, detail='Student not found')
    if user.role.upper() not in {'TEACHER', 'STUDENT'}:
        raise HTTPException(status_code=403, detail='Student access required')
    return student


def _build_support_recommendations(prediction: dict[str, Any], explanation: dict[str, Any]) -> list[dict[str, Any]]:
    risk_level = str(prediction.get('risk_level', 'LOW')).upper()
    contributions = explanation.get('contributions', {}) if isinstance(explanation, dict) else {}
    ranked = sorted(contributions.items(), key=lambda item: abs(float(item[1]) if isinstance(item[1], (int, float)) else 0.0), reverse=True)
    top_factor = ranked[0][0] if ranked else 'academic_progress'
    if risk_level == 'HIGH':
        return [
            {'title': 'Faculty check-in and support plan', 'action_type': 'FACULTY_CHECK_IN', 'priority': 'high', 'rationale': f'Highest risk signal is {top_factor} and needs a structured academic review.'},
            {'title': 'Attendance recovery plan', 'action_type': 'ATTENDANCE_REVIEW', 'priority': 'high', 'rationale': 'Review attendance barriers and create a recovery plan with missed learning targets.'},
            {'title': 'Assessment and deadline recovery', 'action_type': 'ASSESSMENT_RECOVERY', 'priority': 'medium', 'rationale': 'Assist with deadlines and assessment planning to stabilize academic momentum.'},
        ]
    if risk_level == 'MEDIUM':
        return [
            {'title': 'Targeted academic mentoring', 'action_type': 'MENTORING', 'priority': 'medium', 'rationale': f'Current risk is moderated by {top_factor}; a concise coaching plan will help reduce the trend.'},
            {'title': 'Engagement and resource check', 'action_type': 'RESOURCE_SUPPORT', 'priority': 'medium', 'rationale': 'Check whether study resources, assignments, and attendance support are sufficient.'},
        ]
    return [
        {'title': 'Maintain positive momentum', 'action_type': 'SUCCESS_MAINTENANCE', 'priority': 'low', 'rationale': 'Continue the current direction while monitoring for shifts in attendance or assessment performance.'},
    ]


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/support-profile')
def student_support_profile(student_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _student_access(student_id, user, db)
    features = student_features(student, db)
    prediction = predict_with_active_model(features, db)
    explanation = explain_with_active_model(features, db, target_class=prediction['risk_level'])
    evidence = [
        {'feature': name, 'impact': float(value), 'direction': 'positive' if float(value) > 0 else 'negative'}
        for name, value in sorted(explanation.get('contributions', {}).items(), key=lambda item: abs(float(item[1]) if isinstance(item[1], (int, float)) else 0.0), reverse=True)
    ]
    actions = db.query(SupportAction).filter(SupportAction.student_id == student.id).order_by(SupportAction.created_at.desc()).all()
    follow_ups = db.query(SupportFollowUp).filter(SupportFollowUp.student_id == student.id).order_by(SupportFollowUp.scheduled_date.desc(), SupportFollowUp.id.desc()).all()
    versions = db.query(SupportPlanVersion).filter(SupportPlanVersion.student_id == student.id).order_by(SupportPlanVersion.version.desc(), SupportPlanVersion.created_at.desc()).all()
    student_data, _ = student_risk_view(student, db)
    return {
        'student': {**student_data, 'id': student.id, 'student_id': student.student_id},
        'risk': {'level': prediction['risk_level'], 'probability': prediction['risk_probability'], 'class_probabilities': prediction.get('class_probabilities', {})},
        'risk_evidence': evidence,
        'recommended_actions': _build_support_recommendations(prediction, explanation),
        'support_actions': [
            {
                'id': item.id,
                'student_id': item.student_id,
                'title': item.title,
                'action_type': item.action_type,
                'status': normalize_support_status(item.status, default='PENDING_FACULTY_REVIEW'),
                'due_date': item.due_date.isoformat() if item.due_date else None,
                'notes': item.notes,
                'outcome': item.outcome,
                'completed_at': item.completed_at.isoformat() if item.completed_at else None,
            }
            for item in actions
        ],
        'follow_ups': [
            {
                'id': item.id,
                'student_id': item.student_id,
                'assigned_to': item.assigned_to,
                'scheduled_date': item.scheduled_date.isoformat() if item.scheduled_date else None,
                'status': normalize_support_status(item.status, default='SCHEDULED'),
                'notes': item.notes,
                'outcome': item.outcome,
                'completed_at': item.completed_at.isoformat() if item.completed_at else None,
            }
            for item in follow_ups
        ],
        'support_plan_versions': [
            {
                'id': item.id,
                'version': item.version,
                'summary': item.summary,
                'plan_json': item.plan_json,
                'created_at': item.created_at.isoformat(),
            }
            for item in versions
        ],
    }


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/support-plan')
def student_support_plan(student_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = student_support_profile(student_id, user, db)
    versions = profile.get('support_plan_versions') or []
    latest = versions[0] if versions else None
    return {
        'student_id': student_id,
        'risk_level': profile['risk']['level'],
        'plan': profile['recommended_actions'],
        'latest_version': latest,
        'version_history': versions,
    }


@app.post(f'{settings.api_v1_prefix}/students/{{student_id}}/support-plan')
def create_student_support_plan(student_id: int, user: CurrentUser, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _student_access(student_id, user, db)
    if user.role.upper() != 'TEACHER':
        raise HTTPException(status_code=403, detail='Only teachers can create support plans for students.')
    plan = payload.get('plan') or payload.get('recommendations') or student_support_profile(student_id, user, db)['recommended_actions']
    summary = str(payload.get('summary') or 'Support plan updated for review.')
    current_revision = db.query(SupportPlanVersion).filter(SupportPlanVersion.student_id == student.id).order_by(SupportPlanVersion.version.desc()).first()
    version = (current_revision.version if current_revision else 0) + 1
    record = SupportPlanVersion(student_id=student.id, teacher_id=user.id, version=version, summary=summary, plan_json={'plan': plan})
    db.add(record)
    db.commit()
    db.refresh(record)
    return {'id': record.id, 'student_id': student.id, 'version': record.version, 'summary': record.summary, 'plan': record.plan_json.get('plan', plan), 'created_at': record.created_at.isoformat()}


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/support-plan/history')
def student_support_plan_history(student_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _student_access(student_id, user, db)
    items = db.query(SupportPlanVersion).filter(SupportPlanVersion.student_id == student.id).order_by(SupportPlanVersion.version.desc(), SupportPlanVersion.created_at.desc()).all()
    return {'student_id': student.id, 'items': [{'id': item.id, 'version': item.version, 'summary': item.summary, 'plan': item.plan_json.get('plan', []), 'created_at': item.created_at.isoformat()} for item in items]}


def _recovery_student_access(student_id: int, user: User, db: Session) -> Student:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    role = user.role.upper()
    if role == 'STUDENT':
        if student.user_id != user.id:
            raise HTTPException(status_code=404, detail='Student not found')
        return student
    if role != 'TEACHER':
        raise HTTPException(status_code=403, detail='Recovery roadmap access required')
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise HTTPException(status_code=403, detail='Teacher profile is not linked')
    if student.course_id is None:
        query = db.query(Course.id).filter(
            Course.teacher_id == teacher.id,
            Course.section == student.section,
        ) if student.section else db.query(Course.id).filter(False)
    else:
        query = db.query(Course.id).filter(
            Course.teacher_id == teacher.id,
            Course.id == student.course_id,
        )
    if query.first() is None:
        raise HTTPException(status_code=404, detail='Student not found')
    return student


def _teacher_assigned_subject_ids(db: Session, user: User) -> set[int]:
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise HTTPException(status_code=403, detail='Teacher profile is not linked')
    return {
        subject_id for (subject_id,) in db.query(Course.subject_id)
        .filter(Course.teacher_id == teacher.id, Course.subject_id.is_not(None))
        .distinct()
        .all()
    }


def _recovery_plan_subject_ids(plan: SupportPlanVersion) -> set[int]:
    if plan.subject_id is not None:
        return {plan.subject_id}
    if not plan.is_blueprint_group:
        return set()
    plan_json = plan.plan_json or {}
    blueprint = plan_json.get('blueprint')
    subject_ids = {
        int(item['subject_id'])
        for item in blueprint.get('subjects', [])
        if item.get('subject_id') is not None
    } if isinstance(blueprint, dict) else set()
    subject_ids.update(
        int(item['subject_id'])
        for item in plan_json.get('tasks', [])
        if item.get('subject_id') is not None
    )
    return subject_ids


def _recovery_plan_access(plan_id: int, user: User, db: Session) -> SupportPlanVersion:
    plan = db.query(SupportPlanVersion).filter(SupportPlanVersion.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail='Recovery plan not found')
    _recovery_student_access(plan.student_id, user, db)
    if user.role.upper() == 'STUDENT':
        if (
            plan.status != PLAN_PUBLISHED
            or plan.is_blueprint_group
            or plan.subject_scope_review_required
            or plan.subject_id is None
        ):
            raise HTTPException(status_code=404, detail='Recovery plan not found')
    else:
        subject_ids = _recovery_plan_subject_ids(plan)
        allowed_subject_ids = _teacher_assigned_subject_ids(db, user)
        if not subject_ids or not subject_ids.issubset(allowed_subject_ids):
            raise HTTPException(status_code=404, detail='Recovery plan not found')
    return plan


def _recovery_task_access(task_id: int, user: User, db: Session, *, lock: bool = False) -> RecoveryTask:
    task_query = db.query(RecoveryTask).filter(RecoveryTask.id == task_id)
    if lock:
        task_query = task_query.with_for_update()
    task = task_query.first()
    if task is None:
        raise HTTPException(status_code=404, detail='Recovery task not found')
    _recovery_student_access(task.student_id, user, db)
    plan = db.query(SupportPlanVersion).filter(SupportPlanVersion.id == task.roadmap_version_id).first()
    if plan is None or plan.status != PLAN_PUBLISHED:
        raise HTTPException(status_code=404, detail='Recovery task not found')
    if user.role.upper() == 'STUDENT' and (
        task.subject_scope_review_required
        or task.subject_id is None
        or plan.subject_id != task.subject_id
        or plan.subject_scope_review_required
    ):
        raise HTTPException(status_code=404, detail='Recovery task not found')
    if user.role.upper() == 'TEACHER' and task.subject_id is not None:
        _teacher_subject_access(db, teacher_user=user, subject_id=task.subject_id)
    if task.status == TASK_DRAFT and user.role.upper() == 'STUDENT':
        raise HTTPException(status_code=404, detail='Recovery task not found')
    return task


def _recovery_task_out(task: RecoveryTask) -> RecoveryTaskOut:
    result = RecoveryTaskOut.model_validate(task).model_dump()
    plan = task.roadmap
    definition = next(
        (
            item for item in (plan.plan_json or {}).get('tasks', [])
            if item.get('task_key') == task.task_key
        ),
        {},
    )
    evidence_refs = definition.get('evidence_refs', [])
    evidence_records = plan.evidence_json.get('academic_records', [])
    try:
        activity = RecoveryActivity.model_validate(definition.get('activity'))
    except (ValidationError, TypeError):
        activity = RecoveryActivity.model_validate(legacy_recovery_activity(task))
    result.update({
        'evidence_refs': evidence_refs,
        'evidence': [item for item in evidence_records if item.get('ref') in evidence_refs],
        'field_provenance': definition.get('field_provenance', {}),
        'activity': activity.model_dump(mode='json'),
        'stage': definition.get('stage'),
    })
    return RecoveryTaskOut.model_validate(result)


def _published_recovery_tasks(
    db: Session,
    student_id: int,
    status: str | None,
    user: User | None = None,
) -> list[RecoveryTask]:
    query = (
        db.query(RecoveryTask)
        .join(SupportPlanVersion, SupportPlanVersion.id == RecoveryTask.roadmap_version_id)
        .filter(
            RecoveryTask.student_id == student_id,
            SupportPlanVersion.status == PLAN_PUBLISHED,
            SupportPlanVersion.is_blueprint_group.is_(False),
            SupportPlanVersion.subject_scope_review_required.is_(False),
            SupportPlanVersion.subject_id == RecoveryTask.subject_id,
            RecoveryTask.subject_id.is_not(None),
            RecoveryTask.subject_scope_review_required.is_(False),
            RecoveryTask.status != TASK_DRAFT,
            RecoveryTask.status != TASK_SUPERSEDED,
        )
    )
    if status:
        query = query.filter(RecoveryTask.status == status)
    if user is not None and user.role.upper() == 'TEACHER':
        teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
        if teacher is None:
            raise HTTPException(status_code=403, detail='Teacher profile is not linked')
        query = (
            query.join(Subject, Subject.id == RecoveryTask.subject_id)
            .join(Course, Course.subject_id == Subject.id)
            .filter(Course.teacher_id == teacher.id)
        )
    return query.order_by(RecoveryTask.scheduled_at.is_(None), RecoveryTask.scheduled_at, RecoveryTask.id).all()


def _recovery_plan_out(plan: SupportPlanVersion) -> RecoveryPlanOut:
    return RecoveryPlanOut(
        id=plan.id,
        student_id=plan.student_id,
        subject_id=plan.subject_id,
        subject_scope_review_required=plan.subject_scope_review_required,
        blueprint_group_id=plan.blueprint_group_id,
        is_blueprint_group=plan.is_blueprint_group,
        source_blueprint_plan_id=plan.source_blueprint_plan_id,
        teacher_id=plan.teacher_id,
        version=plan.version,
        status=plan.status,
        summary=plan.summary,
        plan=plan.plan_json,
        evidence=plan.evidence_json,
        generation_key=plan.generation_key,
        approved_by_user_id=plan.approved_by_user_id,
        approved_at=plan.approved_at,
        published_at=plan.published_at,
        created_at=plan.created_at,
    )


def _refresh_blueprint_assessment_status(db: Session, topic: dict[str, Any]) -> None:
    assignment_id = topic.get('assignment_id')
    if not assignment_id:
        topic.setdefault('assessment', {})['status'] = 'BLOCKED_NO_SOURCE_BACKED_ASSIGNMENT'
        return
    assessments = db.query(LearningAssessment).filter_by(
        assignment_id=assignment_id,
    ).order_by(LearningAssessment.version.desc()).all()
    published = next((item for item in assessments if item.status == 'PUBLISHED'), None)
    current = published or (assessments[0] if assessments else None)
    assessment_info = topic.setdefault('assessment', {})
    assessment_info['status'] = current.status if current else 'NOT_GENERATED'
    assessment_info['assessment_id'] = current.id if current else None
    if current is not None:
        questions = db.query(AssessmentQuestion).filter_by(assessment_id=current.id).all()
        assessment_info['question_count'] = len(questions)
        assessment_info['mcq_count'] = sum(item.question_type == 'MCQ' for item in questions)
        assessment_info['short_answer_count'] = sum(item.question_type == 'SHORT_ANSWER' for item in questions)
        assessment_info['validation_results'] = current.validation_results


def _recovery_blueprint_publish_blockers(db: Session, plan: SupportPlanVersion) -> list[str]:
    blueprint = (plan.plan_json or {}).get('blueprint')
    if not isinstance(blueprint, dict):
        return []
    blueprint = json.loads(json.dumps(blueprint))
    blockers: list[str] = []
    if blueprint.get('generation_status') != 'GENERATED_NEEDS_TEACHER_REVIEW':
        blockers.append('Valid AI blueprint recommendations are not available.')
    if not blueprint.get('syllabus', {}).get('reviewed'):
        blockers.append('Teacher review or acknowledgement of the syllabus context is required.')
    topic_items = [
        topic
        for subject in blueprint.get('subjects', [])
        for topic in subject.get('topics', [])
    ]
    if not topic_items:
        blockers.append('The blueprint contains no extracted topics to review.')
    for topic in topic_items:
        _refresh_blueprint_assessment_status(db, topic)
        decision = topic.get('decision')
        if decision not in {'INCLUDE', 'EXCLUDE'}:
            blockers.append(f'Topic "{topic.get("title", topic.get("topic_id"))}" needs an include or exclude decision.')
            continue
        if decision == 'EXCLUDE':
            continue
        if not topic.get('source_ready'):
            blockers.append(f'Included topic "{topic.get("title")}" has no readable approved source material.')
        if not topic.get('priority') or not topic.get('suggested_minutes'):
            blockers.append(f'Included topic "{topic.get("title")}" has no validated recommendation.')
        if blueprint.get('syllabus', {}).get('context') and not topic.get('syllabus_mapping'):
            blockers.append(f'Included topic "{topic.get("title")}" needs a teacher-confirmed syllabus mapping.')
        if topic.get('conflict_resolution_status') == 'NEEDS_TEACHER_REVIEW' and not topic.get('conflict_resolution'):
            blockers.append(f'Included topic "{topic.get("title")}" has an unresolved source-order conflict.')
        assessment = topic.get('assessment') or {}
        if assessment.get('status') != 'PUBLISHED':
            blockers.append(f'Included topic "{topic.get("title")}" needs a teacher-approved and published assessment.')
        elif (
            assessment.get('question_count') != 20
            or assessment.get('mcq_count') != 15
            or assessment.get('short_answer_count') != 5
            or not (assessment.get('validation_results') or {}).get('valid')
        ):
            blockers.append(f'Included topic "{topic.get("title")}" does not have a validated 15-MCQ/5-short-answer assessment.')
        if not topic.get('assignment_id'):
            blockers.append(f'Included topic "{topic.get("title")}" has no learning assignment.')
    for subject in blueprint.get('subjects', []):
        if not any(
            topic.get('decision') == 'INCLUDE'
            for topic in subject.get('topics', [])
        ):
            blockers.append(f'{subject.get("subject_name", "Selected subject")} has no included topic in the blueprint.')
        for module_number in (3, 4):
            if not any(
                topic.get('decision') == 'INCLUDE'
                and topic.get('module_number') == module_number
                for topic in subject.get('topics', [])
            ):
                blockers.append(
                    f'{subject.get("subject_name", "Eligible subject")} has no included, '
                    f'source-backed Module {module_number} topic for the global recovery stage.'
                )
    included_ids = {
        topic['topic_id'] for topic in topic_items if topic.get('decision') == 'INCLUDE'
    }
    if not included_ids:
        blockers.append('At least one supported topic must be included in the roadmap.')
    schedule = blueprint.get('schedule', [])
    included_subjects = {
        subject['subject_id']
        for subject in blueprint.get('subjects', [])
        if any(topic.get('decision') == 'INCLUDE' for topic in subject.get('topics', []))
    }
    topic_subjects = {
        topic['topic_id']: subject['subject_id']
        for subject in blueprint.get('subjects', [])
        for topic in subject.get('topics', [])
    }
    topic_stages = {
        topic['topic_id']: topic.get('stage')
        for subject in blueprint.get('subjects', [])
        for topic in subject.get('topics', [])
    }
    stage_dates: dict[str, list[date]] = {}
    scheduled_topic_ids = {
        item.get('topic_id') for item in schedule
        if item.get('activity') == 'TOPIC_STUDY'
    }
    for topic_id in included_ids - scheduled_topic_ids:
        blockers.append(f'Included topic {topic_id} has no scheduled study time.')
    daily_totals: dict[str, int] = {}
    topic_minutes: dict[int, int] = {}
    subject_activities: dict[int, set[str]] = {}
    for item in schedule:
        day = item.get('scheduled_date')
        subject_id = item.get('subject_id')
        activity = item.get('activity')
        if subject_id not in included_subjects:
            blockers.append('Every schedule entry must identify a subject with an included topic.')
        else:
            subject_activities.setdefault(subject_id, set()).add(activity)
        if activity == 'TOPIC_STUDY' and (
            item.get('topic_id') not in topic_subjects
            or topic_subjects.get(item.get('topic_id')) != subject_id
        ):
            blockers.append('A topic study schedule entry must match the topic subject.')
        expected_stage = (
            topic_stages.get(item.get('topic_id'))
            if activity == 'TOPIC_STUDY'
            else 'REVISION_ASSESSMENT'
        )
        if item.get('stage') != expected_stage:
            blockers.append('Every schedule entry must preserve its validated global recovery stage.')
        if item.get('provenance') != 'TEACHER_EDITED':
            blockers.append('A teacher must confirm every scheduled date before publication.')
        if day:
            daily_totals[day] = daily_totals.get(day, 0) + int(item.get('estimated_minutes', 0))
            if item.get('stage'):
                stage_dates.setdefault(item['stage'], []).append(date.fromisoformat(day))
        if activity == 'TOPIC_STUDY' and item.get('topic_id') in included_ids:
            topic_id = item['topic_id']
            topic_minutes[topic_id] = topic_minutes.get(topic_id, 0) + int(item.get('estimated_minutes', 0))
        if day and date.fromisoformat(day) < _institutional_today():
            blockers.append(f'Schedule entry on {day} is in the past; move it to a future date.')
        if item.get('topic_id') not in included_ids and item.get('topic_id') is not None:
            blockers.append(f'Schedule includes excluded or unconfirmed topic {item["topic_id"]}.')
    if any(total > 240 for total in topic_minutes.values()):
        blockers.append('A topic exceeds the maximum supported 240-minute backend-enforced study session.')
    if any(total > blueprint.get('available_minutes_per_day', 60) for total in daily_totals.values()):
        blockers.append('One or more scheduled days exceed the available study minutes.')
    if not schedule:
        blockers.append('No dated subject-specific schedule is available for publication.')
    if (
        stage_dates.get('MODULE_3')
        and stage_dates.get('MODULE_4')
        and max(stage_dates['MODULE_3']) >= min(stage_dates['MODULE_4'])
    ):
        blockers.append('Stage 2 must be scheduled after every Module 3 topic in Stage 1.')
    final_module_dates = stage_dates.get('MODULE_4') or stage_dates.get('MODULE_3') or []
    if (
        final_module_dates
        and stage_dates.get('REVISION_ASSESSMENT')
        and max(final_module_dates) >= min(stage_dates['REVISION_ASSESSMENT'])
    ):
        blockers.append('Revision and assessment practice must be scheduled after the Module 3 and Module 4 stages.')
    if (date.fromisoformat(blueprint['exam_date']) - _institutional_today()).days >= 4:
        for subject_id in included_subjects:
            missing = {'REVISION', 'MOCK_PRACTICE', 'RETENTION_CHECK'} - subject_activities.get(subject_id, set())
            if missing:
                blockers.append(
                    f'Subject {subject_id} is missing feasible pre-exam checkpoints: {", ".join(sorted(missing))}.'
                )
    plan.plan_json = {**plan.plan_json, 'blueprint': blueprint}
    flag_modified(plan, 'plan_json')
    return list(dict.fromkeys(blockers))


def _recovery_audit(
    db: Session,
    user: User,
    action: str,
    resource_type: str,
    resource_id: int,
    *,
    previous_state: str | None = None,
    new_state: str | None = None,
    reason: str | None = None,
    extra_detail: dict[str, Any] | None = None,
) -> None:
    detail = {
        'actor_role': user.role.upper(),
        'previous_state': previous_state,
        'new_state': new_state,
        'reason': reason,
    }
    if extra_detail:
        detail.update(extra_detail)
    db.add(AuditLog(
        actor_user_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        detail=detail,
    ))


@app.get(f'{settings.api_v1_prefix}/students/me/recovery-tasks', response_model=list[RecoveryTaskOut])
def list_my_recovery_tasks(
    student: CurrentStudent,
    db: Session = Depends(get_db),
    status: str | None = Query(default=None, pattern='^(AVAILABLE|IN_PROGRESS|SUBMITTED|NEEDS_REVISION|COMPLETED)$'),
) -> list[RecoveryTaskOut]:
    tasks = _published_recovery_tasks(db, student.id, status)
    return [_recovery_task_out(task) for task in tasks]


@app.get(f'{settings.api_v1_prefix}/students/me/recovery-timeline')
def my_recovery_timeline(
    student: CurrentStudent,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return recovery_timeline(student.id, user, db)


@app.post(
    f'{settings.api_v1_prefix}/students/me/recovery-tutor',
    response_model=RecoveryTutorOut,
)
def recovery_tutor(
    payload: RecoveryTutorRequest,
    student: CurrentStudent,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> RecoveryTutorOut:
    evidence = build_recovery_evidence(db, student)
    all_records = evidence['academic_records']
    academic_records = all_records[-40:]
    missing_information = list(evidence['missing_information'])
    if len(all_records) > len(academic_records):
        missing_information.append('Only 40 academic records are included in this tutor response; earlier records may be omitted.')
    missing_information.append(
        'Course-document retrieval is disabled because the available shared index has no enforceable course access scope.'
    )
    status = 'no_evidence'
    answer_source = 'deterministic_fallback'
    answer = (
        'The tutor is temporarily unavailable. You can still write down the concept or question you are working on, '
        'note what you already understand, and ask your teacher to select an appropriate course resource. '
        'No AI-generated answer or official academic change was made.'
    )
    suggestions: list[str] = []
    evidence_refs: list[str] = []
    cited_evidence: list[dict[str, Any]] = []
    provider_name: str | None = None
    answer_scope: str | None = None

    if re.fullmatch(
        r'(?:hi|hello|hey)(?:\s+there)?[!?.\s]*',
        payload.question,
        flags=re.IGNORECASE,
    ):
        status = 'answered'
        answer_source = 'built_in'
        answer_scope = 'GENERAL_EXPLANATION'
        answer = 'Hi! I can explain a general learning concept or help you interpret an academic record your teacher has shared.'
    else:
        try:
            provider = build_provider(settings)
            provider_name = provider.provider_name
            if provider_name not in {'ollama', 'ollama-local'} and not settings.allow_external_tutor_data:
                raise RuntimeError(
                    'External recovery tutoring is disabled. Set ALLOW_EXTERNAL_TUTOR_DATA=true only when institutional policy permits it.'
                )
            generate = getattr(provider, 'generate_recovery_tutor', None)
            if not callable(generate):
                raise RuntimeError('The configured language-model provider does not support recovery tutoring.')
            generated = generate(RecoveryTutorContext(
                question=payload.question,
                academic_evidence=academic_records,
                missing_information=missing_information,
                conversation_history=[
                    turn.model_dump(mode='json')
                    for turn in payload.conversation_history
                ],
            ))
            model_response = RecoveryTutorModelResponse.model_validate(json.loads(generated))
            validate_recovery_tutor_response(
                model_response,
                evidence_records=academic_records,
            )
        except (RuntimeError, TimeoutError, OSError, ValueError, TypeError, ValidationError) as exc:
            status = 'unavailable'
            logger.warning('Recovery tutor provider or response validation failed (%s).', type(exc).__name__)
            answer = (
                'The tutor is temporarily unavailable, so this was not an AI-generated answer. '
                'You can still note the concept you are working on and ask your teacher for a suitable course resource. '
                'Your academic records and official grades have not been changed.'
            )
        else:
            answer_source = 'provider'
            if model_response.supported:
                status = 'answered'
                answer = model_response.answer
                suggestions = model_response.practice_suggestions
                evidence_refs = model_response.evidence_refs
                cited_evidence = [record for record in academic_records if record.get('ref') in evidence_refs]
                answer_scope = model_response.answer_scope
            else:
                status = 'no_evidence' if not academic_records else 'unsupported'
                answer_source = 'deterministic_fallback'
                answer = (
                    'There is not enough verified academic evidence to answer this student-specific question. '
                    'Ask your teacher to review the relevant records with you.'
                    if not academic_records else
                    'This question cannot be answered from the verified academic evidence currently available. '
                    'Ask your teacher to review it with you.'
                )

    _recovery_audit(
        db,
        user,
        'recovery_tutor.requested',
        'recovery_tutor',
        student.id,
        new_state=status.upper(),
        reason='Student requested evidence-grounded tutoring; question and generated content are not stored.',
    )
    db.commit()
    return RecoveryTutorOut(
        status=status,
        answer_source=answer_source,
        answer_scope=answer_scope,
        answer=answer,
        practice_suggestions=suggestions,
        evidence_refs=evidence_refs,
        evidence=cited_evidence,
        missing_information=missing_information,
        provider=provider_name,
    )


@app.post(f'{settings.api_v1_prefix}/students/{{student_id}}/recovery-plans', response_model=RecoveryPlanOut, status_code=201)
def generate_recovery_plan(
    student_id: int,
    payload: RecoveryPlanGenerateRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryPlanOut:
    student = _recovery_student_access(student_id, user, db)
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise HTTPException(status_code=403, detail='Teacher profile is not linked')
    evidence = build_recovery_evidence(db, student)
    generation_key = stable_generation_key(student.id, evidence, payload.generation_key)
    plan, created = create_draft(
        db,
        student_id=student.id,
        teacher_id=teacher.id,
        evidence=evidence,
        generation_key=generation_key,
    )
    if created:
        _recovery_audit(
            db, user, 'recovery_plan.generated', 'recovery_plan', plan.id,
            new_state=PLAN_DRAFT,
        )
    db.commit()
    db.refresh(plan)
    return _recovery_plan_out(plan)


@app.post(f'{settings.api_v1_prefix}/students/{{student_id}}/recovery-blueprints', response_model=RecoveryPlanOut, status_code=201)
def generate_recovery_blueprint(
    student_id: int,
    payload: RecoveryBlueprintGenerateRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryPlanOut:
    student = _recovery_student_access(student_id, user, db)
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise HTTPException(status_code=403, detail='Teacher profile is not linked')
    today = _institutional_today()
    horizon = (payload.exam_date - today).days
    if horizon < 1 or horizon > 365:
        raise HTTPException(status_code=422, detail='FA-2 exam date must be within the next 365 days.')

    policy = current_subject_focus_policy(db)
    subject_context: dict[int, dict[str, Any]] = {}
    focus_response = build_teacher_subject_focus(
        db,
        user,
        student_id=student.id,
        first_internal_only=True,
    )
    subject_evidence: list[dict[str, Any]] = []
    for subject_data in focus_response['subjects']:
        subject_id = subject_data['id']
        student_evidence = next(
            (item for item in subject_data['students'] if item['id'] == student.id),
            None,
        )
        if student_evidence is None:
            continue
        subject = db.get(Subject, subject_id)
        if subject is None:
            continue
        evaluation = {
            key: student_evidence[key]
            for key in (
                'status',
                'support_required',
                'support_triggers',
                'reason',
                'latest_assessment',
                'latest_assessments',
                'official_failure',
                'official_failure_status',
                'evidence_scope',
                'history',
            )
        }
        triggers = evaluation['support_triggers']
        eligible = 'LOW_SCORE' in triggers
        latest_evidence = evaluation['latest_assessments']
        evidence_summary = {
            'subject_id': subject.id,
            'subject_code': subject.code,
            'subject_name': subject.name,
            'eligible': eligible,
            'status': evaluation['status'],
            'support_triggers': triggers,
            'official_failure_status': evaluation['official_failure_status'],
            'reason': evaluation['reason'],
            'evidence': latest_evidence,
        }
        subject_evidence.append(evidence_summary)
        if not eligible:
            continue
        academic_record = db.query(StudentSubjectAcademicRecord).filter_by(
            student_id=student.id,
            subject_id=subject.id,
            academic_period=payload.academic_period,
        ).first() if payload.academic_period else None
        subject_context[subject.id] = {
            'subject': subject,
            'academic_record': academic_record,
            'evaluation': evaluation,
            'eligibility': {
                **evidence_summary,
                'latest_evidence': latest_evidence,
            },
        }

    if not subject_context:
        raise HTTPException(
            status_code=422,
            detail={
                'message': 'No subjects qualify from the currently recorded first-internal evidence. Only uniquely dated, verified FA-1 marks strictly below 12/25 are eligible; official FAIL remains a separate support signal. Missing, ambiguous, unverified, or non-current-policy evidence remains insufficient.',
                'subjects': subject_evidence,
            },
        )

    blueprint_subjects: list[dict[str, Any]] = []
    topic_subjects: dict[int, int] = {}
    topic_module_numbers: dict[int, int] = {}
    stage_topic_subjects: dict[int, set[int]] = {3: set(), 4: set()}
    recommendations_context: list[dict[str, Any]] = []
    blockers: list[str] = []

    for subject_id in sorted(subject_context):
        context = subject_context[subject_id]
        subject = context['subject']
        academic_record = context['academic_record']
        eligibility = context['eligibility']
        focus_reason = (
            f"Eligible triggers: {', '.join(eligibility['support_triggers'])}. "
            f"{eligibility['reason']}"
        )
        modules = db.query(SubjectModule).filter_by(subject_id=subject.id).order_by(
            SubjectModule.module_number, SubjectModule.id,
        ).all()
        module_ids = [module.id for module in modules]
        resources = db.query(SubjectModuleResource).filter(
            SubjectModuleResource.subject_id == subject.id,
            SubjectModuleResource.module_id.in_(module_ids),
        ).order_by(SubjectModuleResource.created_at, SubjectModuleResource.id).all() if modules else []
        module_number_by_id = {module.id: module.module_number for module in modules}
        stage_module_ids = [
            module.id for module in modules if module.module_number in {3, 4}
        ]
        for module_number in (3, 4):
            if not any(module.module_number == module_number for module in modules):
                blockers.append(f'{subject.name}: Module {module_number} is not configured for the requested recovery stages.')
        topic_rows = db.query(ApprovedTopic).filter(
            ApprovedTopic.subject_id == subject.id,
            ApprovedTopic.approval_status == 'APPROVED',
            ApprovedTopic.module_id.in_(stage_module_ids),
        ).order_by(ApprovedTopic.module_id, ApprovedTopic.created_at, ApprovedTopic.id).all() if stage_module_ids else []
        topics: list[dict[str, Any]] = []
        for topic in topic_rows:
            resource = db.query(SubjectModuleResource).filter_by(
                id=topic.source_resource_id,
                subject_id=subject.id,
                module_id=topic.module_id,
            ).first() if topic.source_resource_id else None
            module = db.query(SubjectModule).filter_by(id=topic.module_id, subject_id=subject.id).first()
            source_ready = bool(
                resource
                and resource.processing_status == 'PROCESSED'
                and resource.extracted_text
                and resource.extracted_text.strip()
            )
            topic_item = {
                'topic_id': topic.id,
                'title': topic.title,
                'summary': topic.summary,
                'learning_objectives': topic.learning_objectives,
                'concepts': topic.concepts,
                'module_id': topic.module_id,
                'module_number': module.module_number if module else None,
                'stage': f'MODULE_{module.module_number}' if module and module.module_number in {3, 4} else None,
                'module_title': module.title if module else None,
                'source_resource_id': topic.source_resource_id,
                'source_material': {
                    'ref': f'resource:{resource.id}',
                    'filename': resource.original_filename,
                    'title': resource.title,
                    'content_hash': resource.content_hash,
                } if source_ready and resource else None,
                'source_ready': source_ready,
                'source_provenance': topic.source_provenance or {},
                'syllabus_mapping': None,
                'syllabus_mapping_status': 'NEEDS_TEACHER_MAPPING',
                'priority_basis': 'AI_PEDAGOGICAL_SEQUENCE_ONLY_NO_OFFICIAL_EXAM_WEIGHT',
                'dependencies': [],
                'dependency_status': 'NOT_SPECIFIED_IN_APPROVED_SOURCE',
                'conflict_resolution': None,
                'decision': 'PENDING',
                'priority': None,
                'suggested_minutes': None,
                'assessment': {
                    'required_mcq_count': 15,
                    'required_short_answer_count': 5,
                    'status': 'NOT_GENERATED',
                    'source_mapping_required': True,
                },
            }
            if not source_ready:
                blockers.append(f'{subject.name}: approved topic "{topic.title}" has no readable processed source file.')
            else:
                topic_subjects[topic.id] = subject.id
                topic_module_numbers[topic.id] = module_number_by_id.get(topic.module_id, 0)
                if topic_module_numbers[topic.id] in {3, 4}:
                    stage_topic_subjects[topic_module_numbers[topic.id]].add(subject.id)
                    recommendations_context.append({
                        'topic_id': topic.id,
                        'subject': subject.name,
                        'module': module.title if module else None,
                        'module_number': topic_module_numbers[topic.id],
                        'title': topic.title,
                        'summary': topic.summary,
                        'learning_objectives': topic.learning_objectives,
                        'concepts': topic.concepts,
                        'source': topic_item['source_material'],
                    })
            if (topic.source_provenance or {}).get('order_uncertain'):
                topic_item['conflict_resolution_status'] = 'NEEDS_TEACHER_REVIEW'
            else:
                topic_item['conflict_resolution_status'] = 'NO_RECORDED_ORDER_CONFLICT'

            assignment = db.query(TopicLearningAssignment).filter_by(
                student_id=student.id,
                approved_topic_id=topic.id,
            ).first()
            if source_ready and assignment is None:
                assignment = TopicLearningAssignment(
                    student_id=student.id,
                    subject_id=subject.id,
                    module_id=topic.module_id,
                    approved_topic_id=topic.id,
                    teacher_id=teacher.id,
                    title=topic.title,
                    learning_objective=(
                        topic.learning_objectives[0]
                        if topic.learning_objectives else topic.summary
                    )[:2000],
                    lesson_content=topic.summary[:10000],
                    expected_duration_minutes=30,
                    status='DRAFT',
                    mastery_criteria='Complete the teacher-approved topic assessment; mastery is evaluated only from recorded assessment evidence.',
                    source_provenance={
                        'origin': 'recovery_blueprint_draft',
                        'topic_id': topic.id,
                        'resource_id': topic.source_resource_id,
                        'roadmap_student_id': student.id,
                    },
                )
                db.add(assignment)
                db.flush()
            topic_item['assignment_id'] = assignment.id if assignment else None
            if assignment is None:
                topic_item['assessment']['status'] = 'BLOCKED_NO_SOURCE_BACKED_ASSIGNMENT'
            else:
                assessments = db.query(LearningAssessment).filter_by(
                    assignment_id=assignment.id,
                ).order_by(LearningAssessment.version.desc()).all()
                published_assessment = next(
                    (assessment for assessment in assessments if assessment.status == 'PUBLISHED'),
                    None,
                )
                topic_item['assessment']['status'] = (
                    'PUBLISHED' if published_assessment else
                    assessments[0].status if assessments else 'NOT_GENERATED'
                )
                topic_item['assessment']['assessment_id'] = published_assessment.id if published_assessment else None
            topics.append(topic_item)

        if not topic_rows:
            blockers.append(f'{subject.name}: no teacher-approved extracted topics are available yet.')

        recorded_evidence = eligibility['latest_evidence']
        blueprint_subjects.append({
            'subject_id': subject.id,
            'subject_name': subject.name,
            'focus_reason': focus_reason,
            'eligibility': eligibility,
            'evidence_source': 'DATED_FIRST_INTERNAL_ASSESSMENT_MARKS',
            'fa1_evidence': {
                'academic_period': academic_record.academic_period if academic_record else None,
                'period_linkage': 'ACADEMIC_RECORD_LINKED' if academic_record else 'NOT_RECORDED_FOR_THIS_PERIOD',
                'records': recorded_evidence,
            },
            'modules': [{
                'module_id': module.id,
                'module_number': module.module_number,
                'title': module.title,
                'resources': [{
                    'resource_id': resource.id,
                    'filename': resource.original_filename,
                    'processing_status': resource.processing_status,
                    'topic_map_status': (resource.provenance or {}).get('topic_map_status', 'NOT_PROCESSED'),
                } for resource in resources if resource.module_id == module.id],
            } for module in modules],
            'topics': topics,
        })

    missing_stage_subjects = {
        module_number: sorted(set(subject_context) - stage_topic_subjects[module_number])
        for module_number in (3, 4)
    }
    for module_number, subject_ids in missing_stage_subjects.items():
        for subject_id in subject_ids:
            blockers.append(
                f'{subject_context[subject_id]["subject"].name}: no approved, source-backed Module '
                f'{module_number} topics are available for the global recovery stage.'
            )
    stage_coverage_complete = not any(missing_stage_subjects.values())

    ai_recommendations: list[dict[str, Any]] = []
    generation_status = 'NOT_RUN_NO_APPROVED_SOURCE_BACKED_TOPICS'
    if recommendations_context:
        try:
            provider = build_provider(settings)
            raw = provider.generate_recovery_blueprint({
                'academic_period': payload.academic_period or 'NOT_SPECIFIED',
                'exam_date': payload.exam_date.isoformat(),
                'syllabus_context': payload.syllabus_context,
                'syllabus_source': payload.syllabus_source,
                'approved_source_backed_topics': recommendations_context,
            })
            ai_recommendations = validate_recovery_blueprint_response(
                raw,
                {item['topic_id'] for item in recommendations_context},
            )
            by_topic = {item['topic_id']: item for item in ai_recommendations}
            for subject_item in blueprint_subjects:
                for topic_item in subject_item['topics']:
                    if topic_item['topic_id'] in by_topic:
                        topic_item.update(by_topic[topic_item['topic_id']])
            generation_status = 'GENERATED_NEEDS_TEACHER_REVIEW'
        except (RuntimeError, ValueError, ValidationError) as exc:
            blockers.append(f'AI recommendations are unavailable or invalid: {exc}')
            generation_status = 'FAILED_EXPLICITLY'

    schedule: list[dict[str, Any]] = []
    schedule_blockers: list[str] = []
    if ai_recommendations and stage_coverage_complete:
        schedule, schedule_blockers = build_recovery_blueprint_schedule(
            ai_recommendations,
            topic_subjects,
            start_date=today,
            exam_date=payload.exam_date,
            minutes_per_day=payload.available_minutes_per_day or 60,
            topic_module_numbers=topic_module_numbers,
            eligible_subject_ids=set(subject_context),
        )
    blockers.extend(schedule_blockers)
    if ai_recommendations and not stage_coverage_complete:
        blockers.append(
            'The global schedule was not generated because every eligible subject needs approved, '
            'source-backed topics in both Module 3 and Module 4.'
        )
    plan_digest = hashlib.sha256(json.dumps({
        'student_id': student.id,
        'academic_period': payload.academic_period,
        'exam_date': payload.exam_date.isoformat(),
        'eligible_subject_ids': sorted(subject_context),
        'subject_evidence': subject_evidence,
        'syllabus_context': payload.syllabus_context,
        'syllabus_source': payload.syllabus_source,
        'acknowledge_no_syllabus': payload.acknowledge_no_syllabus,
        'available_minutes_per_day': payload.available_minutes_per_day or 60,
        'records': [item['fa1_evidence'] for item in blueprint_subjects],
        'topics': recommendations_context,
    }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    generation_key = f'blueprint-{plan_digest[:64]}'
    existing = db.query(SupportPlanVersion).filter_by(
        student_id=student.id,
        generation_key=generation_key,
    ).first()
    if existing is not None:
        db.rollback()
        return _recovery_plan_out(existing)
    previous = db.query(SupportPlanVersion).filter_by(student_id=student.id).order_by(
        SupportPlanVersion.version.desc(),
    ).first()
    evidence = build_recovery_evidence(db, student)
    blueprint = {
        'schema_version': 1,
        'status': 'DRAFT',
        'generation_status': generation_status,
        'academic_period': payload.academic_period,
        'exam_date': payload.exam_date.isoformat(),
        'generated_at': datetime.utcnow().isoformat(),
        'syllabus': {
            'context': payload.syllabus_context,
            'source': payload.syllabus_source,
            'status': 'PROVIDED_NEEDS_REVIEW' if payload.syllabus_context else 'NO_OFFICIAL_MAPPING_PROVIDED',
            'reviewed': False,
        },
        'available_minutes_per_day': payload.available_minutes_per_day or 60,
        'facts_provenance': 'VERIFIED_PERSISTED_RECORDS',
        'ai_recommendations_provenance': getattr(locals().get('provider'), 'provider_name', 'unavailable'),
        'subjects': blueprint_subjects,
        'subject_evidence': subject_evidence,
        'schedule': schedule,
        'assessment_policy': {
            'mcq_count_per_topic': 15,
            'short_answer_count_per_topic': 5,
            'answers_rubrics_and_source_mappings_required': True,
        },
        'blockers': list(dict.fromkeys(blockers)),
        'teacher_review': {
            'syllabus_reviewed': False,
            'topic_decisions_complete': False,
            'conflicts_resolved': False,
            'assessments_published': False,
        },
    }
    plan = SupportPlanVersion(
        student_id=student.id,
        subject_scope_review_required=False,
        blueprint_group_id=str(uuid4()),
        is_blueprint_group=True,
        teacher_id=teacher.id,
        version=(previous.version if previous else 0) + 1,
        summary='Teacher-reviewed recovery blueprint draft; no student-facing activities are published.',
        plan_json={'tasks': [], 'blueprint': blueprint},
        status=PLAN_DRAFT,
        evidence_json=evidence,
        generation_key=generation_key,
    )
    db.add(plan)
    db.flush()
    _recovery_audit(
        db, user, 'recovery_blueprint.generated', 'recovery_plan', plan.id,
        new_state=PLAN_DRAFT,
        reason=f'{generation_status}; {len(blockers)} blocker(s)',
        extra_detail={
            'student_id': student.id,
            'subject_eligibility': [{
                'subject_id': subject_id,
                'status': subject_context[subject_id]['eligibility']['status'],
                'support_triggers': subject_context[subject_id]['eligibility']['support_triggers'],
                'official_failure_status': subject_context[subject_id]['eligibility']['official_failure_status'],
                'latest_evidence': subject_context[subject_id]['eligibility']['latest_evidence'],
            } for subject_id in sorted(subject_context)],
            'insufficient_subject_evidence': [
                item for item in subject_evidence if not item['eligible']
            ],
        },
    )
    db.commit()
    db.refresh(plan)
    return _recovery_plan_out(plan)


@app.patch(f'{settings.api_v1_prefix}/recovery-plans/{{plan_id}}/blueprint', response_model=RecoveryPlanOut)
def edit_recovery_blueprint(
    plan_id: int,
    payload: RecoveryBlueprintEditRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryPlanOut:
    plan = _recovery_plan_access(plan_id, user, db)
    if plan.status != PLAN_DRAFT:
        raise HTTPException(status_code=409, detail='Only draft blueprints can be reviewed.')
    blueprint = (plan.plan_json or {}).get('blueprint')
    if not isinstance(blueprint, dict):
        raise HTTPException(status_code=409, detail='This recovery plan has no reviewable blueprint.')
    blueprint = json.loads(json.dumps(blueprint))
    topic_items = {
        topic['topic_id']: topic
        for subject in blueprint.get('subjects', [])
        for topic in subject.get('topics', [])
    }
    seen_decisions: set[int] = set()
    for item in payload.topic_decisions:
        if item.topic_id not in topic_items or item.topic_id in seen_decisions:
            raise HTTPException(status_code=422, detail='Topic decisions must uniquely reference topics in this blueprint.')
        seen_decisions.add(item.topic_id)
        topic_items[item.topic_id]['decision'] = item.decision
        topic_items[item.topic_id]['decision_provenance'] = 'TEACHER_CONFIRMED'
    for item in payload.syllabus_mappings:
        if item.topic_id not in topic_items:
            raise HTTPException(status_code=422, detail='Syllabus mappings must reference topics in this blueprint.')
        topic_items[item.topic_id]['syllabus_mapping'] = item.mapping
        topic_items[item.topic_id]['syllabus_mapping_status'] = 'TEACHER_CONFIRMED'
    for item in payload.conflict_resolutions:
        if item.topic_id not in topic_items:
            raise HTTPException(status_code=422, detail='Conflict resolutions must reference topics in this blueprint.')
        topic_items[item.topic_id]['conflict_resolution'] = item.resolution
        topic_items[item.topic_id]['conflict_resolution_status'] = 'TEACHER_RESOLVED'
        topic_items[item.topic_id]['conflict_resolution_provenance'] = 'TEACHER_CONFIRMED'
    if payload.syllabus_reviewed is not None:
        blueprint['syllabus']['reviewed'] = payload.syllabus_reviewed
        blueprint['syllabus']['reviewed_by_user_id'] = user.id if payload.syllabus_reviewed else None
        blueprint['syllabus']['reviewed_at'] = datetime.utcnow().isoformat() if payload.syllabus_reviewed else None
    if payload.schedule is not None:
        topic_subjects = {
            topic['topic_id']: subject['subject_id']
            for subject in blueprint.get('subjects', [])
            for topic in subject.get('topics', [])
        }
        exam_date = date.fromisoformat(blueprint['exam_date'])
        normalized_schedule: list[dict[str, Any]] = []
        daily_totals: dict[str, int] = {}
        for item in payload.schedule:
            scheduled = item.scheduled_date
            if scheduled > exam_date:
                raise HTTPException(status_code=422, detail='Schedule entries cannot occur after the FA-2 exam date.')
            if item.activity == 'TOPIC_STUDY':
                if item.topic_id not in topic_items or item.topic_id not in topic_subjects:
                    raise HTTPException(status_code=422, detail='Topic study entries must reference a blueprint topic.')
                if topic_items[item.topic_id]['decision'] == 'EXCLUDE':
                    raise HTTPException(status_code=422, detail='Excluded topics cannot remain on the schedule.')
                expected_subject_id = topic_subjects[item.topic_id]
                if item.subject_id is not None and item.subject_id != expected_subject_id:
                    raise HTTPException(status_code=422, detail='A topic schedule entry must remain in its source subject.')
                schedule_subject_id = expected_subject_id
            else:
                blueprint_subject_ids = {
                    subject['subject_id'] for subject in blueprint.get('subjects', [])
                }
                schedule_subject_id = item.subject_id
                if schedule_subject_id is None and len(blueprint_subject_ids) == 1:
                    schedule_subject_id = next(iter(blueprint_subject_ids))
                if schedule_subject_id not in blueprint_subject_ids:
                    raise HTTPException(
                        status_code=422,
                        detail='Subject-level checkpoints must identify a subject in this blueprint.',
                    )
            day = scheduled.isoformat()
            daily_totals[day] = daily_totals.get(day, 0) + item.estimated_minutes
            normalized_schedule.append({
                **item.model_dump(mode='json'),
                'subject_id': schedule_subject_id,
                'stage': (
                    topic_items[item.topic_id].get('stage')
                    if item.activity == 'TOPIC_STUDY'
                    else 'REVISION_ASSESSMENT'
                ),
                'provenance': 'TEACHER_EDITED',
            })
        if any(total > blueprint['available_minutes_per_day'] for total in daily_totals.values()):
            raise HTTPException(status_code=422, detail='Daily scheduled workload cannot exceed the stated available minutes.')
        blueprint['schedule'] = normalized_schedule
    for topic in topic_items.values():
        _refresh_blueprint_assessment_status(db, topic)
    plan.plan_json = {**plan.plan_json, 'blueprint': blueprint}
    blueprint['teacher_review'] = {
        'syllabus_reviewed': bool(blueprint['syllabus'].get('reviewed')),
        'topic_decisions_complete': all(topic.get('decision') in {'INCLUDE', 'EXCLUDE'} for topic in topic_items.values()),
        'conflicts_resolved': all(
            topic.get('conflict_resolution_status') != 'NEEDS_TEACHER_REVIEW'
            or topic.get('conflict_resolution')
            for topic in topic_items.values()
        ),
        'assessments_published': all(
            topic.get('decision') != 'INCLUDE' or topic.get('assessment', {}).get('status') == 'PUBLISHED'
            for topic in topic_items.values()
        ),
    }
    plan.plan_json = {**plan.plan_json, 'blueprint': blueprint}
    _recovery_audit(
        db, user, 'recovery_blueprint.reviewed', 'recovery_plan', plan.id,
        previous_state=PLAN_DRAFT, new_state=PLAN_DRAFT,
        reason='Teacher updated blueprint topics, mappings, schedule, or conflict resolutions.',
    )
    db.commit()
    db.refresh(plan)
    return _recovery_plan_out(plan)


@app.post(
    f'{settings.api_v1_prefix}/recovery-plans/{{plan_id}}/blueprint/revisions',
    response_model=RecoveryPlanOut,
    status_code=201,
)
def create_recovery_blueprint_revision(
    plan_id: int,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryPlanOut:
    source = _recovery_plan_access(plan_id, user, db)
    if source.status != PLAN_PUBLISHED or not isinstance((source.plan_json or {}).get('blueprint'), dict):
        raise HTTPException(status_code=409, detail='Only a published blueprint can start a reviewable revision.')
    latest_version = db.query(func.max(SupportPlanVersion.version)).filter_by(
        student_id=source.student_id,
    ).scalar() or source.version
    plan_data = json.loads(json.dumps(source.plan_json))
    blueprint = plan_data['blueprint']
    blueprint['status'] = 'DRAFT_REVISION'
    blueprint['generated_at'] = datetime.utcnow().isoformat()
    blueprint['source_version_id'] = source.id
    blueprint['blockers'] = []
    blueprint['teacher_review'] = {
        'syllabus_reviewed': False,
        'topic_decisions_complete': False,
        'conflicts_resolved': False,
        'assessments_published': False,
    }
    blueprint['syllabus']['reviewed'] = False
    blueprint['syllabus'].pop('reviewed_by_user_id', None)
    blueprint['syllabus'].pop('reviewed_at', None)
    for subject in blueprint.get('subjects', []):
        for topic in subject.get('topics', []):
            topic['decision'] = 'PENDING'
            topic.pop('decision_provenance', None)
    revision = SupportPlanVersion(
        student_id=source.student_id,
        subject_scope_review_required=False,
        blueprint_group_id=source.blueprint_group_id or str(source.id),
        is_blueprint_group=True,
        source_blueprint_plan_id=source.id,
        teacher_id=source.teacher_id,
        version=latest_version + 1,
        summary=f'Revision of recovery blueprint v{source.version}; review future work before publication.',
        plan_json={**plan_data, 'tasks': []},
        status=PLAN_DRAFT,
        evidence_json=json.loads(json.dumps(source.evidence_json or {})),
        generation_key=f'blueprint-revision-{uuid4().hex}',
    )
    db.add(revision)
    db.flush()
    _recovery_audit(
        db, user, 'recovery_blueprint.revision_created', 'recovery_plan', revision.id,
        previous_state=source.status, new_state=PLAN_DRAFT,
        reason=f'Revision based on published roadmap v{source.version}; existing work and assessment history remain unchanged.',
    )
    db.commit()
    db.refresh(revision)
    return _recovery_plan_out(revision)


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/recovery-plans', response_model=list[RecoveryPlanOut])
def list_recovery_plans(student_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> list[RecoveryPlanOut]:
    student = _recovery_student_access(student_id, user, db)
    query = db.query(SupportPlanVersion).filter(SupportPlanVersion.student_id == student.id)
    if user.role.upper() == 'STUDENT':
        query = query.filter(
            SupportPlanVersion.status == PLAN_PUBLISHED,
            SupportPlanVersion.is_blueprint_group.is_(False),
            SupportPlanVersion.subject_scope_review_required.is_(False),
            SupportPlanVersion.subject_id.is_not(None),
        )
    plans = query.order_by(SupportPlanVersion.version.desc(), SupportPlanVersion.created_at.desc()).all()
    if user.role.upper() == 'TEACHER':
        allowed_subject_ids = _teacher_assigned_subject_ids(db, user)
        plans = [
            plan for plan in plans
            if (subject_ids := _recovery_plan_subject_ids(plan))
            and subject_ids.issubset(allowed_subject_ids)
        ]
    return [_recovery_plan_out(plan) for plan in plans]


@app.patch(f'{settings.api_v1_prefix}/recovery-plans/{{plan_id}}', response_model=RecoveryPlanOut)
def edit_recovery_plan(
    plan_id: int,
    payload: RecoveryPlanEditRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryPlanOut:
    plan = _recovery_plan_access(plan_id, user, db)
    before = plan.status
    if payload.tasks is not None:
        if plan.subject_id is not None and any(
            task.subject_id not in {None, plan.subject_id}
            for task in payload.tasks
        ):
            raise HTTPException(status_code=422, detail='Edited tasks must remain within this roadmap subject.')
        if plan.is_blueprint_group:
            existing_subject_ids = {
                int(item['subject_id'])
                for item in (plan.plan_json or {}).get('tasks', [])
                if item.get('subject_id') is not None
            }
            if any(task.subject_id is None for task in payload.tasks):
                raise HTTPException(
                    status_code=422,
                    detail='Every task in a subject-group roadmap must retain its persisted subject ID.',
                )
            if any(task.subject_id not in existing_subject_ids for task in payload.tasks):
                raise HTTPException(
                    status_code=422,
                    detail='Edited tasks must remain within the subjects already present in this roadmap.',
                )
        for subject_id in {task.subject_id for task in payload.tasks if task.subject_id is not None}:
            _teacher_subject_access(db, teacher_user=user, subject_id=subject_id)
    try:
        edit_draft(plan, summary=payload.summary, tasks=payload.tasks)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _recovery_audit(db, user, 'recovery_plan.edited', 'recovery_plan', plan.id, previous_state=before, new_state=plan.status)
    db.commit()
    db.refresh(plan)
    return _recovery_plan_out(plan)


@app.post(f'{settings.api_v1_prefix}/recovery-plans/{{plan_id}}/approve', response_model=RecoveryPlanOut)
def approve_recovery_plan(
    plan_id: int,
    payload: RecoveryPlanDecisionRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryPlanOut:
    plan = _recovery_plan_access(plan_id, user, db)
    if plan.status == PLAN_DRAFT:
        blueprint_blockers = _recovery_blueprint_publish_blockers(db, plan)
        if blueprint_blockers:
            plan.plan_json = {
                **plan.plan_json,
                'blueprint': {
                    **plan.plan_json['blueprint'],
                    'blockers': blueprint_blockers,
                },
            }
            db.commit()
            raise HTTPException(
                status_code=409,
                detail={'message': 'Blueprint review requirements are not complete.', 'blockers': blueprint_blockers},
            )
        previous_state = plan.status
        plan.status = PLAN_APPROVED
        plan.approved_by_user_id = user.id
        plan.approved_at = datetime.utcnow()
        _recovery_audit(
            db, user, 'recovery_plan.approved', 'recovery_plan', plan.id,
            previous_state=previous_state, new_state=plan.status, reason=payload.reason,
        )
    elif plan.status != PLAN_APPROVED:
        raise HTTPException(status_code=409, detail='Only draft recovery plans can be approved.')
    db.commit()
    db.refresh(plan)
    return _recovery_plan_out(plan)


@app.post(f'{settings.api_v1_prefix}/recovery-plans/{{plan_id}}/reject', response_model=RecoveryPlanOut)
def reject_recovery_plan(
    plan_id: int,
    payload: RecoveryPlanDecisionRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryPlanOut:
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(status_code=422, detail='A rejection reason is required.')
    plan = _recovery_plan_access(plan_id, user, db)
    if plan.status not in {PLAN_DRAFT, PLAN_APPROVED}:
        raise HTTPException(status_code=409, detail='Only draft or approved recovery plans can be rejected.')
    previous_state = plan.status
    plan.status = PLAN_REJECTED
    _recovery_audit(
        db, user, 'recovery_plan.rejected', 'recovery_plan', plan.id,
        previous_state=previous_state, new_state=plan.status, reason=payload.reason.strip(),
    )
    db.commit()
    db.refresh(plan)
    return _recovery_plan_out(plan)


@app.post(f'{settings.api_v1_prefix}/recovery-plans/{{plan_id}}/publish', response_model=RecoveryPlanOut)
def publish_recovery_plan_endpoint(
    plan_id: int,
    payload: RecoveryPlanDecisionRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryPlanOut:
    plan = _recovery_plan_access(plan_id, user, db)
    blueprint_blockers = _recovery_blueprint_publish_blockers(db, plan)
    if blueprint_blockers:
        plan.plan_json = {
            **plan.plan_json,
            'blueprint': {
                **plan.plan_json['blueprint'],
                'blockers': blueprint_blockers,
            },
        }
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={'message': 'Blueprint review requirements are not complete.', 'blockers': blueprint_blockers},
        )
    previous_state = plan.status
    try:
        tasks = publish_plan(db, plan, user.id, datetime.utcnow())
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    blueprint = (plan.plan_json or {}).get('blueprint')
    if isinstance(blueprint, dict):
        blueprint['status'] = 'PUBLISHED'
        plan.plan_json = {**plan.plan_json, 'blueprint': blueprint}
        flag_modified(plan, 'plan_json')
    activated_assignment_count = 0
    if isinstance(blueprint, dict):
        for subject in blueprint.get('subjects', []):
            for topic in subject.get('topics', []):
                if topic.get('decision') == 'INCLUDE' and topic.get('assignment_id'):
                    assignment = db.query(TopicLearningAssignment).filter_by(
                        id=topic['assignment_id'],
                        student_id=plan.student_id,
                    ).first()
                    if assignment is not None:
                        activated_assignment_count += 1
                        if assignment.status == 'DRAFT':
                            assignment.status = 'ACTIVE'
                        scheduled_items = [
                            item for item in blueprint.get('schedule', [])
                            if item.get('activity') == 'TOPIC_STUDY'
                            and item.get('topic_id') == topic.get('topic_id')
                        ]
                        existing_session = db.query(LearningStudySession).filter_by(
                            assignment_id=assignment.id,
                            student_id=plan.student_id,
                        ).first()
                        has_attempt = db.query(AssessmentAttempt.id).filter_by(
                            assignment_id=assignment.id,
                            student_id=plan.student_id,
                        ).first() is not None
                        if (
                            scheduled_items
                            and assignment.status == 'ACTIVE'
                            and existing_session is None
                            and not has_attempt
                        ):
                            scheduled_items.sort(key=lambda item: item['scheduled_date'])
                            assignment.scheduled_at = datetime.fromisoformat(
                                f"{scheduled_items[0]['scheduled_date']}T00:00:00"
                            )
                            assignment.available_from = assignment.scheduled_at
                            total_minutes = sum(int(item['estimated_minutes']) for item in scheduled_items)
                            if 5 <= total_minutes <= 240:
                                assignment.expected_duration_minutes = total_minutes
    if previous_state != PLAN_PUBLISHED:
        student = db.query(Student).filter(Student.id == plan.student_id).first()
        if student and student.user_id:
            db.add(Notification(
                user_id=student.user_id,
                kind='recovery',
                title='Recovery roadmap published',
                body=plan.summary or 'A teacher published a recovery roadmap for you.',
                metadata_json={
                    'recovery_plan_id': plan.id,
                    'task_count': len(tasks),
                    'learning_assignment_count': activated_assignment_count,
                },
            ))
        _recovery_audit(
            db, user, 'recovery_plan.published', 'recovery_plan', plan.id,
            previous_state=previous_state, new_state=plan.status, reason=payload.reason,
        )
    db.commit()
    db.refresh(plan)
    return _recovery_plan_out(plan)


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/recovery-tasks', response_model=list[RecoveryTaskOut])
def list_recovery_tasks(
    student_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
    status: str | None = Query(default=None, pattern='^(AVAILABLE|IN_PROGRESS|SUBMITTED|NEEDS_REVISION|COMPLETED)$'),
) -> list[RecoveryTaskOut]:
    student = _recovery_student_access(student_id, user, db)
    tasks = _published_recovery_tasks(db, student.id, status, user)
    return [_recovery_task_out(task) for task in tasks]


@app.patch(f'{settings.api_v1_prefix}/recovery-tasks/{{task_id}}', response_model=RecoveryTaskOut)
def update_recovery_task_progress(
    task_id: int,
    payload: RecoveryTaskProgressRequest,
    student_user: CurrentUser,
    db: Session = Depends(get_db),
) -> RecoveryTaskOut:
    if student_user.role.upper() != 'STUDENT':
        raise HTTPException(status_code=403, detail='Only the assigned student can update task progress.')
    task = _recovery_task_access(task_id, student_user, db)
    event_type = 'STARTED' if payload.action == 'start' else 'PROGRESS_UPDATED'
    detail = {'action': payload.action, 'progress_percent': payload.progress_percent}
    existing = db.query(RecoveryTaskEvent).filter_by(idempotency_key=payload.idempotency_key).first()
    if existing is not None:
        try:
            add_task_event(
                db, task=task, actor_user_id=student_user.id, event_type=event_type,
                detail=detail, idempotency_key=payload.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _recovery_task_out(task)
    if payload.action == 'start':
        if task.status != TASK_AVAILABLE:
            raise HTTPException(status_code=409, detail='Only available tasks can be started.')
        task.status = TASK_IN_PROGRESS
    else:
        if task.status != TASK_IN_PROGRESS:
            raise HTTPException(status_code=409, detail='Progress can only be updated on an in-progress task.')
        task.progress_percent = payload.progress_percent or 0
    add_task_event(
        db, task=task, actor_user_id=student_user.id, event_type=event_type,
        detail=detail, idempotency_key=payload.idempotency_key,
    )
    _recovery_audit(db, student_user, f'recovery_task.{event_type.lower()}', 'recovery_task', task.id, new_state=task.status)
    db.commit()
    db.refresh(task)
    return _recovery_task_out(task)


@app.put(f'{settings.api_v1_prefix}/recovery-tasks/{{task_id}}/draft', response_model=RecoveryTaskOut)
def save_recovery_task_draft(
    task_id: int,
    payload: RecoveryTaskDraftSaveRequest,
    student_user: CurrentUser,
    db: Session = Depends(get_db),
) -> RecoveryTaskOut:
    if student_user.role.upper() != 'STUDENT':
        raise HTTPException(status_code=403, detail='Only the assigned student can save recovery work.')
    if db.get_bind().dialect.name == 'sqlite':
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
    task = _recovery_task_access(task_id, student_user, db, lock=True)
    if task.status not in {TASK_IN_PROGRESS, TASK_NEEDS_REVISION}:
        raise HTTPException(status_code=409, detail='Draft work can only be saved on an in-progress or revision-requested task.')
    detail = {'response': payload.response}
    existing = db.query(RecoveryTaskEvent).filter_by(idempotency_key=payload.idempotency_key).first()
    if existing is not None:
        try:
            add_task_event(
                db, task=task, actor_user_id=student_user.id, event_type='DRAFT_SAVED',
                detail=detail, idempotency_key=payload.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _recovery_task_out(task)
    task.student_response = payload.response
    add_task_event(
        db, task=task, actor_user_id=student_user.id, event_type='DRAFT_SAVED',
        detail=detail, idempotency_key=payload.idempotency_key,
    )
    _recovery_audit(db, student_user, 'recovery_task.draft_saved', 'recovery_task', task.id, new_state=task.status)
    db.commit()
    db.refresh(task)
    return _recovery_task_out(task)


@app.post(f'{settings.api_v1_prefix}/recovery-tasks/{{task_id}}/submit', response_model=RecoveryTaskOut)
def submit_recovery_task(
    task_id: int,
    payload: RecoveryTaskSubmitRequest,
    student_user: CurrentUser,
    db: Session = Depends(get_db),
) -> RecoveryTaskOut:
    if student_user.role.upper() != 'STUDENT':
        raise HTTPException(status_code=403, detail='Only the assigned student can submit recovery work.')
    task = _recovery_task_access(task_id, student_user, db)
    detail = {'response': payload.response}
    existing = db.query(RecoveryTaskEvent).filter_by(idempotency_key=payload.idempotency_key).first()
    if existing is not None:
        try:
            add_task_event(
                db, task=task, actor_user_id=student_user.id, event_type='SUBMITTED',
                detail=detail, idempotency_key=payload.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _recovery_task_out(task)
    if task.status not in {TASK_IN_PROGRESS, TASK_NEEDS_REVISION}:
        raise HTTPException(status_code=409, detail='Only in-progress or revision-requested tasks can be submitted.')
    previous_state = task.status
    task.status = TASK_SUBMITTED
    task.student_response = payload.response
    task.submitted_at = datetime.utcnow()
    if previous_state == TASK_NEEDS_REVISION:
        task.revision_count += 1
    add_task_event(
        db, task=task, actor_user_id=student_user.id, event_type='SUBMITTED',
        detail=detail, idempotency_key=payload.idempotency_key,
    )
    _recovery_audit(db, student_user, 'recovery_task.submitted', 'recovery_task', task.id, previous_state=previous_state, new_state=task.status)
    db.commit()
    db.refresh(task)
    return _recovery_task_out(task)


@app.patch(f'{settings.api_v1_prefix}/recovery-tasks/{{task_id}}/assess', response_model=RecoveryTaskOut)
def assess_recovery_task(
    task_id: int,
    payload: RecoveryTaskAssessmentRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryTaskOut:
    if db.get_bind().dialect.name == 'sqlite':
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
    task = _recovery_task_access(task_id, user, db, lock=True)
    event_type = 'ASSESSED'
    detail = {'decision': payload.decision, 'feedback': payload.feedback, 'score': payload.score}
    existing = db.query(RecoveryTaskEvent).filter_by(idempotency_key=payload.idempotency_key).first()
    if existing is not None:
        try:
            add_task_event(
                db, task=task, actor_user_id=user.id, event_type=event_type,
                detail=detail, idempotency_key=payload.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _recovery_task_out(task)
    previous_state = task.status
    next_state = TASK_COMPLETED if payload.decision == 'complete' else TASK_NEEDS_REVISION
    claimed = (
        db.query(RecoveryTask)
        .filter(RecoveryTask.id == task.id, RecoveryTask.status == TASK_SUBMITTED)
        .update({RecoveryTask.status: next_state}, synchronize_session=False)
    )
    if claimed != 1:
        raced_event = db.query(RecoveryTaskEvent).filter_by(idempotency_key=payload.idempotency_key).first()
        if raced_event is None:
            raise HTTPException(status_code=409, detail='Only submitted recovery work can be assessed.')
        try:
            add_task_event(
                db,
                task=task,
                actor_user_id=user.id,
                event_type=event_type,
                detail=detail,
                idempotency_key=payload.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        db.refresh(task)
        return _recovery_task_out(task)
    db.refresh(task)
    task.teacher_feedback = payload.feedback
    task.assessed_score = payload.score
    if next_state == TASK_COMPLETED:
        task.progress_percent = 100
        task.completed_at = datetime.utcnow()
    else:
        task.completed_at = None
    assessment_event, _ = add_task_event(
        db, task=task, actor_user_id=user.id, event_type=event_type,
        detail=detail, idempotency_key=payload.idempotency_key,
    )
    adaptation, adaptation_created = record_adaptation_decision(
        db,
        task=task,
        trigger_event=assessment_event,
    )
    if adaptation_created:
        _recovery_audit(
            db,
            user,
            'recovery_adaptation.generated',
            'recovery_adaptation',
            adaptation.id,
            new_state=adaptation.status,
            reason=adaptation.rationale,
        )
    student = db.query(Student).filter(Student.id == task.student_id).first()
    if student and student.user_id:
        db.add(Notification(
            user_id=student.user_id,
            kind='recovery',
            title='Recovery task feedback is ready',
            body=payload.feedback,
            metadata_json={'recovery_task_id': task.id, 'status': task.status},
        ))
    _recovery_audit(db, user, 'recovery_task.assessed', 'recovery_task', task.id, previous_state=previous_state, new_state=task.status)
    db.commit()
    db.refresh(task)
    return _recovery_task_out(task)


@app.put(f'{settings.api_v1_prefix}/recovery-tasks/{{task_id}}/activity', response_model=RecoveryTaskOut)
def propose_recovery_task_activity(
    task_id: int,
    payload: RecoveryTaskActivityEditRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryTaskOut:
    if db.get_bind().dialect.name == 'sqlite':
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
    task = _recovery_task_access(task_id, user, db, lock=True)
    proposal = payload.model_dump(mode='json', exclude={'idempotency_key'})
    existing = db.query(RecoveryTaskEvent).filter_by(idempotency_key=payload.idempotency_key).first()
    if existing is not None:
        if (
            existing.task_id != task.id
            or existing.actor_user_id != user.id
            or existing.event_type != 'ACTIVITY_PROPOSED'
            or existing.detail.get('proposal') != proposal
        ):
            raise HTTPException(status_code=409, detail='The idempotency key was already used for a different event.')
        return _recovery_task_out(task)
    if task.status not in {TASK_AVAILABLE, TASK_DRAFT}:
        raise HTTPException(status_code=409, detail='An activity can only be replaced before a student starts the task.')
    previous_status = task.status
    task.status = TASK_DRAFT
    add_task_event(
        db, task=task, actor_user_id=user.id, event_type='ACTIVITY_PROPOSED',
        detail={'proposal': proposal, 'previous_status': previous_status},
        idempotency_key=payload.idempotency_key,
    )
    _recovery_audit(
        db, user, 'recovery_task.activity_proposed', 'recovery_task', task.id,
        previous_state=previous_status, new_state=TASK_DRAFT,
        reason='Replacement activity is withheld from the student pending teacher approval.',
    )
    db.commit()
    db.refresh(task)
    return _recovery_task_out(task)


@app.post(f'{settings.api_v1_prefix}/recovery-tasks/{{task_id}}/approve-activity', response_model=RecoveryTaskOut)
def approve_recovery_task_activity(
    task_id: int,
    payload: RecoveryTaskActivityApprovalRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> RecoveryTaskOut:
    if db.get_bind().dialect.name == 'sqlite':
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
    task = _recovery_task_access(task_id, user, db, lock=True)
    proposals = (
        db.query(RecoveryTaskEvent)
        .filter_by(task_id=task.id, event_type='ACTIVITY_PROPOSED')
        .order_by(RecoveryTaskEvent.created_at.desc(), RecoveryTaskEvent.id.desc())
        .all()
    )
    if not proposals:
        raise HTTPException(status_code=409, detail='There is no replacement activity awaiting approval.')
    proposal_event = proposals[0]
    detail = {'proposal_event_id': proposal_event.id}
    existing = db.query(RecoveryTaskEvent).filter_by(idempotency_key=payload.idempotency_key).first()
    if existing is not None:
        try:
            add_task_event(
                db, task=task, actor_user_id=user.id, event_type='ACTIVITY_APPROVED',
                detail=detail, idempotency_key=payload.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _recovery_task_out(task)
    if task.status != TASK_DRAFT:
        raise HTTPException(status_code=409, detail='Only a withheld replacement activity can be approved.')
    proposal = proposal_event.detail.get('proposal')
    if not isinstance(proposal, dict):
        raise HTTPException(status_code=409, detail='The replacement activity proposal is malformed.')
    plan = task.roadmap
    definitions = [
        dict(item)
        for item in (plan.plan_json or {}).get('tasks', [])
        if isinstance(item, dict)
    ]
    definition = next((item for item in definitions if item.get('task_key') == task.task_key), None)
    if definition is None:
        raise HTTPException(status_code=409, detail='The recovery task definition could not be found.')
    approved_activity = RecoveryActivity.model_validate(proposal.get('activity'))
    definition.update({
        'title': proposal['title'],
        'learning_objective': proposal['learning_objective'],
        'instructions': proposal['instructions'],
        'completion_criteria': proposal['completion_criteria'],
        'resources': proposal['resources'],
        'activity': approved_activity.model_dump(mode='json'),
        'field_provenance': {
            **definition.get('field_provenance', {}),
            'content': 'TEACHER_AUTHORED_APPROVED',
            'activity': 'TEACHER_AUTHORED_APPROVED',
            'resources': 'TEACHER_PROVIDED' if proposal['resources'] else 'NO_VERIFIED_RESOURCES',
        },
    })
    plan.plan_json = {'tasks': definitions}
    task.title = proposal['title']
    task.learning_objective = proposal['learning_objective']
    task.instructions = proposal['instructions']
    task.completion_criteria = proposal['completion_criteria']
    task.resources = proposal['resources']
    task.status = TASK_AVAILABLE
    task.progress_percent = 0
    add_task_event(
        db, task=task, actor_user_id=user.id, event_type='ACTIVITY_APPROVED',
        detail=detail, idempotency_key=payload.idempotency_key,
    )
    _recovery_audit(
        db, user, 'recovery_task.activity_approved', 'recovery_task', task.id,
        previous_state=TASK_DRAFT, new_state=TASK_AVAILABLE,
        reason='Teacher approved the replacement activity before student access.',
    )
    db.commit()
    db.refresh(task)
    return _recovery_task_out(task)


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/recovery-timeline')
def recovery_timeline(student_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _recovery_student_access(student_id, user, db)
    plans_query = db.query(SupportPlanVersion).filter(SupportPlanVersion.student_id == student.id)
    if user.role.upper() == 'STUDENT':
        plans_query = plans_query.filter(
            SupportPlanVersion.status == PLAN_PUBLISHED,
            SupportPlanVersion.is_blueprint_group.is_(False),
            SupportPlanVersion.subject_scope_review_required.is_(False),
            SupportPlanVersion.subject_id.is_not(None),
        )
    plans = plans_query.order_by(SupportPlanVersion.created_at.desc()).all()
    if user.role.upper() == 'TEACHER':
        allowed_subject_ids = _teacher_assigned_subject_ids(db, user)
        plans = [
            plan for plan in plans
            if (subject_ids := _recovery_plan_subject_ids(plan))
            and subject_ids.issubset(allowed_subject_ids)
        ]
    plan_ids = [plan.id for plan in plans]
    items = [
        {
            'kind': 'recovery_plan',
            'plan_id': plan.id,
            'occurred_at': (plan.published_at or plan.created_at).isoformat(),
            'title': f'Recovery roadmap v{plan.version}',
            'summary': plan.summary,
            'status': plan.status,
        }
        for plan in plans
    ]
    if plan_ids:
        task_ids = [
            row[0] for row in db.query(RecoveryTask.id)
            .filter(RecoveryTask.roadmap_version_id.in_(plan_ids))
            .all()
        ]
        if task_ids:
            event_query = (
                db.query(RecoveryTaskEvent)
                .join(RecoveryTask, RecoveryTask.id == RecoveryTaskEvent.task_id)
                .filter(
                    RecoveryTaskEvent.task_id.in_(task_ids),
                    RecoveryTaskEvent.subject_id == RecoveryTask.subject_id,
                    RecoveryTask.subject_id.is_not(None),
                    RecoveryTask.subject_scope_review_required.is_(False),
                )
            )
            if user.role.upper() == 'TEACHER':
                event_query = event_query.filter(RecoveryTaskEvent.subject_id.in_(allowed_subject_ids))
            events = event_query.order_by(RecoveryTaskEvent.created_at.desc()).all()
            items.extend({
                'kind': 'recovery_task_event',
                'task_id': event.task_id,
                'event_id': event.id,
                'occurred_at': event.created_at.isoformat(),
                'title': event.event_type.replace('_', ' ').title(),
                'summary': event.detail,
                'status': event.event_type,
            } for event in events)
    items.sort(key=lambda item: item['occurred_at'], reverse=True)
    return {'student_id': student.id, 'items': items}


@app.get(f'{settings.api_v1_prefix}/teacher/recovery-monitoring')
def recovery_monitoring(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    student_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None, pattern='^(AVAILABLE|DRAFT|IN_PROGRESS|SUBMITTED|NEEDS_REVISION|COMPLETED)$'),
) -> dict[str, Any]:
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        raise HTTPException(status_code=403, detail='Teacher profile is not linked')
    if student_id is not None:
        _recovery_student_access(student_id, user, db)
    courses = db.query(Course).filter(Course.teacher_id == teacher.id).all()
    course_ids = [course.id for course in courses]
    empty_counts = {
        name: 0 for name in (
            TASK_AVAILABLE, TASK_DRAFT, TASK_IN_PROGRESS, TASK_SUBMITTED, TASK_NEEDS_REVISION,
            TASK_COMPLETED, 'overdue',
        )
    }
    if not course_ids:
        return {'counts': empty_counts, 'tasks': [], 'students': []}
    assigned_query = db.query(Student).filter(Student.course_id.in_(course_ids))
    assigned_students = assigned_query.order_by(Student.full_name).all()
    assigned_student_ids = [student.id for student in assigned_students]
    if student_id is not None:
        if student_id not in assigned_student_ids:
            raise HTTPException(status_code=404, detail='Student not found')
        assigned_student_ids = [student_id]
        assigned_students = [student for student in assigned_students if student.id == student_id]
    if not assigned_student_ids:
        return {'counts': empty_counts, 'tasks': [], 'students': []}
    query = (
        db.query(RecoveryTask)
        .join(SupportPlanVersion, SupportPlanVersion.id == RecoveryTask.roadmap_version_id)
        .join(Subject, Subject.id == RecoveryTask.subject_id)
        .join(Course, Course.subject_id == Subject.id)
        .filter(
            RecoveryTask.student_id.in_(assigned_student_ids),
            SupportPlanVersion.status == PLAN_PUBLISHED,
            SupportPlanVersion.is_blueprint_group.is_(False),
            SupportPlanVersion.subject_scope_review_required.is_(False),
            SupportPlanVersion.subject_id == RecoveryTask.subject_id,
            RecoveryTask.subject_scope_review_required.is_(False),
            RecoveryTask.status != TASK_SUPERSEDED,
            Course.teacher_id == teacher.id,
        )
    )
    if status:
        query = query.filter(RecoveryTask.status == status)
    tasks = query.order_by(RecoveryTask.deadline, RecoveryTask.id).all()
    pending_activity_by_task: dict[int, dict[str, Any]] = {}
    draft_task_ids = [task.id for task in tasks if task.status == TASK_DRAFT]
    if draft_task_ids:
        proposals = (
            db.query(RecoveryTaskEvent)
            .filter(
                RecoveryTaskEvent.task_id.in_(draft_task_ids),
                RecoveryTaskEvent.event_type == 'ACTIVITY_PROPOSED',
            )
            .order_by(RecoveryTaskEvent.created_at.desc(), RecoveryTaskEvent.id.desc())
            .all()
        )
        for event in proposals:
            proposal = event.detail.get('proposal')
            if event.task_id not in pending_activity_by_task and isinstance(proposal, dict):
                pending_activity_by_task[event.task_id] = proposal
    all_tasks = (
        db.query(RecoveryTask)
        .join(SupportPlanVersion, SupportPlanVersion.id == RecoveryTask.roadmap_version_id)
        .join(Subject, Subject.id == RecoveryTask.subject_id)
        .join(Course, Course.subject_id == Subject.id)
        .filter(
            RecoveryTask.student_id.in_(assigned_student_ids),
            SupportPlanVersion.status == PLAN_PUBLISHED,
            SupportPlanVersion.is_blueprint_group.is_(False),
            SupportPlanVersion.subject_scope_review_required.is_(False),
            SupportPlanVersion.subject_id == RecoveryTask.subject_id,
            RecoveryTask.subject_scope_review_required.is_(False),
            Course.teacher_id == teacher.id,
        )
        .all()
    )
    counts = {name: sum(task.status == name for task in all_tasks) for name in (
        TASK_AVAILABLE, TASK_DRAFT, TASK_IN_PROGRESS, TASK_SUBMITTED, TASK_NEEDS_REVISION, TASK_COMPLETED,
    )}
    counts['overdue'] = sum(
        task.deadline is not None and task.deadline < datetime.utcnow() and task.status != TASK_COMPLETED
        for task in all_tasks
    )
    return {
        'counts': counts,
        'tasks': [
            {
                **_recovery_task_out(task).model_dump(mode='json'),
                'student_name': task.student.full_name,
                'student_code': task.student.student_id,
                'pending_activity': pending_activity_by_task.get(task.id),
            }
            for task in tasks
        ],
        'students': [
            {
                'id': student.id,
                'student_id': student.student_id,
                'full_name': student.full_name,
                'section': student.section,
            }
            for student in assigned_students
        ],
    }


@app.get(
    f'{settings.api_v1_prefix}/teacher/recovery-adaptations',
    response_model=list[RecoveryAdaptationDecisionOut],
)
def list_teacher_recovery_adaptations(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    student_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RecoveryAdaptationDecisionOut]:
    if student_id is not None:
        student = _recovery_student_access(student_id, user, db)
        assigned_student_ids = [student.id]
    else:
        teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
        if teacher is None:
            raise HTTPException(status_code=403, detail='Teacher profile is not linked')
        courses = db.query(Course).filter(Course.teacher_id == teacher.id).all()
        course_ids = [course.id for course in courses]
        sections = [course.section for course in courses if course.section]
        if not course_ids:
            return []
        assigned_students = db.query(Student.id).filter(
            or_(
                Student.course_id.in_(course_ids),
                and_(Student.course_id.is_(None), Student.section.in_(sections)) if sections else False,
            )
        ).all()
        assigned_student_ids = [row[0] for row in assigned_students]

    if not assigned_student_ids:
        return []
    decisions = (
        db.query(RecoveryAdaptationDecision)
        .filter(RecoveryAdaptationDecision.student_id.in_(assigned_student_ids))
        .order_by(RecoveryAdaptationDecision.created_at.desc(), RecoveryAdaptationDecision.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        RecoveryAdaptationDecisionOut(
            id=decision.id,
            student_id=decision.student_id,
            roadmap_version_id=decision.roadmap_version_id,
            task_id=decision.task_id,
            trigger_event_id=decision.trigger_event_id,
            decision_type=decision.decision_type,
            status=decision.status,
            recommended_action=decision.recommended_action,
            rationale=decision.rationale,
            evidence=decision.evidence_json,
            created_at=decision.created_at,
        )
        for decision in decisions
    ]


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/support-timeline')
def student_support_timeline(student_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _student_access(student_id, user, db)
    risk_history = db.query(RiskSnapshot).filter(RiskSnapshot.student_id == student.id).order_by(RiskSnapshot.recorded_at.desc()).all()
    actions = db.query(SupportAction).filter(SupportAction.student_id == student.id).order_by(SupportAction.created_at.desc()).all()
    follow_ups = db.query(SupportFollowUp).filter(SupportFollowUp.student_id == student.id).order_by(SupportFollowUp.scheduled_date.desc(), SupportFollowUp.id.desc()).all()
    plan_versions = db.query(SupportPlanVersion).filter(SupportPlanVersion.student_id == student.id).order_by(SupportPlanVersion.version.desc(), SupportPlanVersion.created_at.desc()).all()
    events = []
    for item in risk_history:
        events.append({'kind': 'risk_snapshot', 'occurred_at': item.recorded_at.isoformat(), 'title': f'Risk snapshot: {item.risk_level}', 'summary': f'Probability {item.probability}', 'status': item.risk_level})
    for item in actions:
        occurred_at = (item.completed_at or item.due_date or item.created_at).isoformat() if item.completed_at or item.due_date or item.created_at else None
        events.append({'kind': 'support_action', 'occurred_at': occurred_at, 'title': item.title, 'summary': item.action_type, 'status': normalize_support_status(item.status, default='PENDING_FACULTY_REVIEW')})
    for item in follow_ups:
        occurred_at = (item.completed_at or item.scheduled_date or item.created_at).isoformat() if item.completed_at or item.scheduled_date or item.created_at else None
        events.append({'kind': 'follow_up', 'occurred_at': occurred_at, 'title': 'Follow-up scheduled', 'summary': item.notes or item.assigned_to, 'status': normalize_support_status(item.status, default='SCHEDULED')})
    for item in plan_versions:
        events.append({'kind': 'support_plan_version', 'occurred_at': item.created_at.isoformat(), 'title': f'Support plan v{item.version}', 'summary': item.summary or 'Support plan updated', 'status': 'UPDATED'})
    events.sort(key=lambda item: item['occurred_at'] or '1970-01-01T00:00:00', reverse=True)
    return {'student_id': student.id, 'items': events}


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/support-actions')
def list_student_support_actions(student_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _student_access(student_id, user, db)
    items = db.query(SupportAction).filter(SupportAction.student_id == student.id).order_by(SupportAction.created_at.desc()).all()
    return {'items': [{
        'id': item.id,
        'student_id': item.student_id,
        'title': item.title,
        'action_type': item.action_type,
        'status': normalize_support_status(item.status, default='PENDING_FACULTY_REVIEW'),
        'due_date': item.due_date.isoformat() if item.due_date else None,
        'notes': item.notes,
        'evidence': item.evidence,
        'outcome': item.outcome,
        'completed_at': item.completed_at.isoformat() if item.completed_at else None,
    } for item in items]}


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/support-effectiveness')
def student_support_effectiveness(student_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _student_access(student_id, user, db)
    snapshots = db.query(RiskSnapshot).filter(RiskSnapshot.student_id == student.id).order_by(RiskSnapshot.recorded_at.asc()).all()
    latest = snapshots[-1] if snapshots else None
    baseline = snapshots[0] if len(snapshots) > 1 else snapshots[0] if snapshots else None
    support_actions = db.query(SupportAction).filter(SupportAction.student_id == student.id).order_by(SupportAction.created_at.asc()).all()
    latest_completed = next((item for item in reversed(support_actions) if item.completed_at), None)
    baseline_prob = baseline.probability if baseline else student.risk_probability
    latest_prob = latest.probability if latest else student.risk_probability
    delta = latest_prob - baseline_prob if baseline is not None else 0.0
    outcome_summary = {
        'baseline_risk_probability': float(baseline_prob),
        'latest_risk_probability': float(latest_prob),
        'change_in_risk_probability': float(delta),
        'support_actions_completed': sum(1 for item in support_actions if normalize_support_status(item.status) == 'COMPLETED'),
        'latest_completed_action': {'id': latest_completed.id, 'title': latest_completed.title} if latest_completed else None,
        'observed_after_intervention': 'Observed change after the support plan and follow-up workflow.' if latest_completed else 'No completed support action has been recorded yet.',
    }
    return {'student_id': student.id, 'summary': outcome_summary, 'baseline': baseline.probability if baseline else None, 'current': latest.probability if latest else None, 'risk_probability_delta': delta}


@app.patch(f'{settings.api_v1_prefix}/support-actions/{{action_id}}')
def update_student_support_action(action_id: int, user: CurrentUser, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    action = db.query(SupportAction).filter(SupportAction.id == action_id).first()
    if action is None:
        raise HTTPException(status_code=404, detail='Support action not found')
    if user.role.upper() != 'TEACHER':
        raise HTTPException(status_code=403, detail='Only teachers can update support actions.')
    status = normalize_support_status(payload.get('status') or payload.get('state') or action.status, default='PENDING_FACULTY_REVIEW')
    action.status = status
    if payload.get('outcome') is not None:
        action.outcome = str(payload['outcome'])
    if status == 'COMPLETED' and not action.completed_at:
        action.completed_at = datetime.utcnow()
    if status in {'CANCELLED', 'MISSED'}:
        action.completed_at = action.completed_at or datetime.utcnow()
    notes = payload.get('notes')
    if notes is not None:
        action.notes = str(notes)
    db.commit()
    db.refresh(action)
    return {'id': action.id, 'status': action.status, 'outcome': action.outcome, 'completed_at': action.completed_at.isoformat() if action.completed_at else None}


@app.patch(f'{settings.api_v1_prefix}/support-follow-ups/{{follow_up_id}}')
def update_student_follow_up(follow_up_id: int, user: CurrentUser, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = db.query(SupportFollowUp).filter(SupportFollowUp.id == follow_up_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail='Follow-up not found')
    action = str(payload.get('action') or payload.get('status') or item.status).strip().lower()
    status = item.status
    if action in {'reschedule', 'rescheduled'}:
        status = 'SCHEDULED'
        scheduled = payload.get('scheduled_date') or payload.get('date')
        parsed = coerce_datetime(scheduled)
        if parsed:
            item.scheduled_date = parsed
    elif action in {'complete', 'completed'}:
        status = 'COMPLETED'
        item.completed_at = item.completed_at or datetime.utcnow()
    elif action in {'cancel', 'cancelled', 'canceled'}:
        status = 'CANCELLED'
        item.completed_at = item.completed_at or datetime.utcnow()
    elif action in {'miss', 'missed'}:
        status = 'MISSED'
        item.completed_at = item.completed_at or datetime.utcnow()
    elif action in {'schedule', 'scheduled'}:
        status = 'SCHEDULED'
    elif payload.get('status') is not None:
        status = normalize_support_status(payload['status'], default='SCHEDULED')
    item.status = normalize_support_status(status, default='SCHEDULED')
    if payload.get('outcome') is not None:
        item.outcome = str(payload['outcome'])
    if payload.get('notes') is not None:
        item.notes = str(payload['notes'])
    db.commit()
    db.refresh(item)
    return {'id': item.id, 'status': item.status, 'scheduled_date': item.scheduled_date.isoformat() if item.scheduled_date else None, 'outcome': item.outcome, 'completed_at': item.completed_at.isoformat() if item.completed_at else None}


@app.post(f'{settings.api_v1_prefix}/students/{{student_id}}/support-actions')
def create_student_support_action(student_id: int, user: CurrentUser, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _student_access(student_id, user, db)
    if user.role.upper() != 'TEACHER':
        raise HTTPException(status_code=403, detail='Only teachers can create or assign support actions.')
    title = str(payload.get('title') or 'Support action')
    action_type = str(payload.get('action_type') or 'GENERAL_SUPPORT').upper()
    due_date = payload.get('due_date')
    parsed_due_date = None
    if due_date:
        if isinstance(due_date, str):
            parsed_due_date = datetime.fromisoformat(due_date.replace('Z', '+00:00'))
        elif isinstance(due_date, datetime):
            parsed_due_date = due_date
    student_features_payload = student_features(student, db)
    risk_prediction = predict_with_active_model(student_features_payload, db)
    action = SupportAction(
        student_id=student.id,
        teacher_id=user.id,
        title=title,
        action_type=action_type,
        status='PENDING_FACULTY_REVIEW',
        due_date=parsed_due_date,
        evidence={
            'risk_level': risk_prediction['risk_level'],
            'risk_probability': risk_prediction['risk_probability'],
            'source': 'student_support_profile',
        },
        notes=str(payload.get('notes') or '')
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return JSONResponse(
        content={
            'id': action.id,
            'student_id': action.student_id,
            'title': action.title,
            'action_type': action.action_type,
            'status': action.status,
            'due_date': action.due_date.isoformat() if action.due_date else None,
            'notes': action.notes,
            'evidence': action.evidence,
        },
        status_code=201,
    )


@app.post(f'{settings.api_v1_prefix}/interventions/{{student_id}}/follow-up')
def create_student_follow_up(student_id: int, user: CurrentUser, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    student = _student_access(student_id, user, db)
    if user.role.upper() != 'TEACHER':
        raise HTTPException(status_code=403, detail='Only teachers can schedule follow-up checks.')
    scheduled = payload.get('scheduled_date')
    parsed_scheduled = None
    if scheduled:
        if isinstance(scheduled, str):
            parsed_scheduled = datetime.fromisoformat(scheduled.replace('Z', '+00:00'))
        elif isinstance(scheduled, datetime):
            parsed_scheduled = scheduled
    follow_up = SupportFollowUp(
        student_id=student.id,
        assigned_to=str(payload.get('assigned_to') or user.email),
        scheduled_date=parsed_scheduled,
        notes=str(payload.get('notes') or ''),
        status='SCHEDULED',
        outcome=None,
    )
    db.add(follow_up)
    db.commit()
    db.refresh(follow_up)
    return {
        'id': follow_up.id,
        'student_id': student.id,
        'assigned_to': follow_up.assigned_to,
        'scheduled_date': follow_up.scheduled_date.isoformat() if follow_up.scheduled_date else None,
        'status': follow_up.status,
        'notes': follow_up.notes,
    }


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}')
def get_student(student_id: str, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    query = db.query(Student)
    if student_id.isdigit():
        query = query.filter(Student.id == int(student_id))
    else:
        query = query.filter(Student.student_id == student_id)
    student = query.first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    if user.role.upper() == 'STUDENT':
        if student.user_id != user.id:
            raise HTTPException(status_code=404, detail='Student not found')
        return {'student': StudentSelfOut.model_validate(student).model_dump()}
    if user.role.upper() != 'TEACHER':
        raise HTTPException(status_code=403, detail='Student access required')
    student_data, _ = student_risk_view(student, db)
    observations = db.query(AcademicMomentum).filter(AcademicMomentum.student_id == student.id).order_by(AcademicMomentum.observed_at).all()
    submissions = db.query(Submission).filter(Submission.student_id == student.id).all()
    snapshots = db.query(RiskSnapshot).filter(RiskSnapshot.student_id == student.id).order_by(RiskSnapshot.recorded_at).all()
    return {
        'student': student_data,
        'momentum': [{'metric': item.metric, 'value': item.value, 'observed_at': item.observed_at.isoformat()} for item in observations],
        'submissions': [SubmissionOut.model_validate(item).model_dump() for item in submissions],
        'risk_history': [{'risk_level': item.risk_level, 'recorded_at': item.recorded_at.isoformat()} for item in snapshots],
        'academic_records': student_subject_records(student, db),
    }


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/risk')
def current_student_risk(student_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    student_data, prediction = student_risk_view(student, db)
    features = student_features(student, db)
    return {
        'student': student_data,
        'features': features,
        'prediction': prediction,
        'risk_status': student_data['risk_status'],
        'risk_status_message': student_data['risk_status_message'],
    }


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/momentum')
def student_momentum(student_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    observations = db.query(AcademicMomentum).filter(AcademicMomentum.student_id == student.id).order_by(AcademicMomentum.observed_at).all()
    grouped: dict[str, list[Any]] = {}
    for item in observations:
        grouped.setdefault(item.metric, []).append(item)
    metrics = {}
    for metric, items in grouped.items():
        result = calculate_momentum([item.value for item in items])
        result['observations'] = [{'value': item.value, 'observed_at': item.observed_at.isoformat()} for item in items]
        metrics[metric] = result
    return {'student_id': student.student_id, 'metrics': metrics}


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/explanation')
def student_explanation(student_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    risk_status, status_message = student_risk_availability(student, db)
    if risk_status in {'unavailable', 'insufficient-data'}:
        raise HTTPException(status_code=503 if risk_status == 'unavailable' else 409, detail=status_message)
    features = student_features(student, db)
    try:
        demo_model = risk_status == 'unvalidated-demo-model'
        prediction = predict_with_demo_preview(features) if demo_model else predict_with_active_model(features, db)
        explanation = (
            explain_with_demo_preview(features, target_class=prediction['risk_level'])
            if demo_model else explain_with_active_model(features, db, target_class=prediction['risk_level'])
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail='Risk model or SHAP background is unavailable.') from exc
    return {
        'student': {'id': student.id, 'student_id': student.student_id, 'full_name': student.full_name},
        'prediction': prediction,
        'features': features,
        'explanation': explanation,
        'risk_status': risk_status,
        'risk_status_message': status_message,
    }


def _build_risk_summary(prediction: dict[str, Any], explanation: dict[str, Any]) -> dict[str, Any]:
    target_class = str(prediction.get('risk_level', '')).upper()
    if target_class not in {'HIGH', 'MEDIUM', 'LOW'} or str(explanation.get('target_class', '')).upper() != target_class:
        return {'status': 'unavailable', 'provider': None, 'error': 'The predicted class and SHAP target do not match.'}

    class_probabilities = prediction.get('class_probabilities')
    risk_probability = prediction.get('risk_probability')
    class_probability = class_probabilities.get(target_class) if isinstance(class_probabilities, dict) else None
    if (
        prediction.get('risk_probability_semantics') != 'P(risk_level)'
        or not isinstance(class_probabilities, dict)
        or not isinstance(class_probability, (int, float))
        or isinstance(class_probability, bool)
        or not isinstance(risk_probability, (int, float))
        or isinstance(risk_probability, bool)
        or not math.isfinite(float(risk_probability))
        or not 0 <= float(risk_probability) <= 1
        or not math.isfinite(float(class_probability))
        or not math.isclose(float(class_probability), float(risk_probability), rel_tol=1e-5, abs_tol=1e-5)
    ):
        return {'status': 'unavailable', 'provider': None, 'error': 'The predicted class probability is incomplete or inconsistent.'}

    waterfall = explanation.get('waterfall')
    if not isinstance(waterfall, list):
        return {'status': 'unavailable', 'provider': None, 'error': 'The SHAP feature evidence is unavailable.'}
    evidence_by_feature: dict[str, RiskSummaryEvidence] = {}
    for item in waterfall:
        if not isinstance(item, dict):
            continue
        feature = item.get('feature')
        value = item.get('value')
        contribution = item.get('contribution')
        if (
            feature not in FEATURES
            or feature in evidence_by_feature
            or isinstance(value, bool)
            or isinstance(contribution, bool)
            or not isinstance(value, (int, float))
            or not isinstance(contribution, (int, float))
            or not math.isfinite(float(value))
            or not math.isfinite(float(contribution))
        ):
            continue
        evidence_by_feature[feature] = RiskSummaryEvidence(
            feature=feature,
            value=float(value),
            contribution=float(contribution),
        )

    if set(evidence_by_feature) != set(FEATURES):
        return {'status': 'unavailable', 'provider': None, 'error': 'The SHAP response does not contain the complete model feature evidence.'}

    feature_values = explanation.get('feature_values')
    contributions = explanation.get('contributions')
    base_value = explanation.get('base_value')
    if not isinstance(feature_values, dict) or not isinstance(contributions, dict) or not isinstance(base_value, (int, float)) or isinstance(base_value, bool) or not math.isfinite(float(base_value)):
        return {'status': 'unavailable', 'provider': None, 'error': 'The SHAP feature values or base value are unavailable.'}
    if any(
        not isinstance(feature_values.get(name), (int, float))
        or isinstance(feature_values.get(name), bool)
        or not isinstance(contributions.get(name), (int, float))
        or isinstance(contributions.get(name), bool)
        or not math.isclose(float(feature_values[name]), evidence_by_feature[name].value, rel_tol=1e-5, abs_tol=1e-5)
        or not math.isclose(float(contributions[name]), evidence_by_feature[name].contribution, rel_tol=1e-5, abs_tol=1e-5)
        for name in FEATURES
    ):
        return {'status': 'unavailable', 'provider': None, 'error': 'The SHAP waterfall does not match its feature values and contributions.'}
    if not math.isclose(
        float(base_value) + sum(item.contribution for item in evidence_by_feature.values()),
        float(risk_probability),
        rel_tol=1e-5,
        abs_tol=1e-5,
    ):
        return {'status': 'unavailable', 'provider': None, 'error': 'The SHAP contributions do not reconcile with the predicted class probability.'}

    ranked_evidence = sorted(evidence_by_feature.values(), key=lambda item: abs(item.contribution), reverse=True)
    summary_evidence = [item for item in ranked_evidence if item.contribution != 0][:5]
    if not summary_evidence:
        return {'status': 'unavailable', 'provider': None, 'error': 'The model returned no non-zero SHAP contributions to summarize.'}

    context = RiskSummaryContext(
        target_class=target_class,
        risk_probability=float(risk_probability),
        evidence=summary_evidence,
    )
    provider = build_provider(settings)
    try:
        response = provider.generate_risk_summary(context)
    except RuntimeError as exc:
        return {'status': 'unavailable', 'provider': provider.provider_name, 'error': str(exc)}

    validation_errors = validate_risk_summary_selection(response, context)
    if validation_errors:
        return {
            'status': 'unavailable',
            'provider': provider.provider_name,
            'error': 'The AI provider returned an invalid evidence selection: ' + ' '.join(validation_errors),
        }

    selected_names = json.loads(response)['signals']
    evidence_by_name = {item.feature: item for item in context.evidence}
    return {
        'status': 'ready',
        'provider': provider.provider_name,
        'selected_signals': [evidence_by_name[name].model_dump() for name in selected_names],
    }


@app.get(f'{settings.api_v1_prefix}/students/{{student_id}}/shap')
def student_shap(student_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    risk_status, status_message = student_risk_availability(student, db)
    if risk_status in {'unavailable', 'insufficient-data'}:
        raise HTTPException(status_code=503 if risk_status == 'unavailable' else 409, detail=status_message)
    demo_model = risk_status == 'unvalidated-demo-model'
    features = student_features(student, db)
    try:
        prediction = predict_with_demo_preview(features) if demo_model else predict_with_active_model(features, db)
        explanation = (
            explain_with_demo_preview(features, target_class=prediction['risk_level'])
            if demo_model else explain_with_active_model(features, db, target_class=prediction['risk_level'])
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail='Risk model is not trained. Run the documented training command first.') from exc
    if not demo_model:
        record = RiskPrediction(student_id=student.id, risk_level=prediction['risk_level'], probability=prediction['risk_probability'], model_version=prediction['model_version'], prediction_timestamp=prediction['prediction_timestamp'], input_features=features)
        db.add(record)
        db.flush()
        from backend.app.models import ShapExplanation

        db.add(ShapExplanation(prediction_id=record.id, values=explanation['contributions'], base_value=explanation['base_value'], model_version=explanation['model_version']))
        student.risk_level = prediction['risk_level']
        student.risk_probability = prediction['risk_probability']
        db.add(RiskSnapshot(student_id=student.id, risk_level=prediction['risk_level'], probability=prediction['risk_probability'], recorded_at=prediction['prediction_timestamp'].replace(tzinfo=None)))
        db.commit()
    return {
        'prediction': prediction,
        'explanation': explanation,
        'risk_summary': _build_risk_summary(prediction, explanation),
        'question': 'Why is this student at risk?',
        'risk_status': risk_status,
        'risk_status_message': status_message,
    }


@app.post(f'{settings.api_v1_prefix}/risk/predict')
def risk_prediction(payload: RiskFeatureInput, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    demo_model = early_warning_uses_demo_model(db)
    student = None
    risk_status = 'unvalidated-demo-model' if demo_model else 'available'
    if payload.student_id is not None:
        student = db.query(Student).filter(Student.id == payload.student_id).first()
        if student is None:
            raise HTTPException(status_code=404, detail='Student not found')
        risk_status, status_message = student_risk_availability(student, db, demo_model=demo_model)
        if risk_status in {'unavailable', 'insufficient-data'}:
            raise HTTPException(status_code=503 if risk_status == 'unavailable' else 409, detail=status_message)
    try:
        result = (
            predict_with_demo_preview(payload.feature_dict())
            if demo_model else predict_with_active_model(payload.feature_dict(), db)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail='Risk model is not trained. Run the documented training command first.') from exc
    if student is not None and not demo_model:
        student.risk_level = result['risk_level']
        student.risk_probability = result['risk_probability']
        db.add(RiskPrediction(student_id=student.id, risk_level=result['risk_level'], probability=result['risk_probability'], model_version=result['model_version'], prediction_timestamp=result['prediction_timestamp'], input_features=payload.feature_dict()))
        db.add(RiskSnapshot(student_id=student.id, risk_level=result['risk_level'], probability=result['risk_probability'], recorded_at=result['prediction_timestamp'].replace(tzinfo=None)))
        db.commit()
    result['risk_status'] = risk_status
    result['risk_status_message'] = (
        'Unvalidated DEMO DATA preview; the result was not saved to student risk history.'
        if demo_model else None
    )
    return result


@app.get(f'{settings.api_v1_prefix}/models/registry')
def list_model_registry(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    entries = db.query(ModelRegistryEntry).order_by(ModelRegistryEntry.created_at.desc()).all()
    return {
        'current_production_model': production_model_registry_entry(),
        'history': [{
            'id': entry.id,
            'model_id': entry.model_id,
            'model_version': entry.model_version,
            'model_type': entry.model_type,
            'feature_contract_version': entry.feature_contract_version,
            'feature_contract': entry.feature_schema,
            'training_period': entry.training_period,
            'training_dataset_version': entry.training_dataset_version,
            'training_dataset_hash': entry.training_dataset_hash,
            'training_timestamp': entry.training_timestamp.isoformat() if entry.training_timestamp else None,
            'validation_metrics': entry.validation_metrics,
            'artifact_identifier': entry.artifact_identifier,
            'artifact_hash': entry.artifact_hash,
            'status': entry.status,
            'deployment_status': entry.deployment_status,
            'notes': entry.notes,
            'created_at': entry.created_at.isoformat(),
            'created_by_user_id': entry.created_by_user_id,
            'dataset_id': entry.dataset_id,
            'dataset_version': entry.dataset_version,
            'dataset_fingerprint': entry.dataset_fingerprint,
            'target_definition': entry.target_definition,
            'aggregation_strategy': entry.aggregation_strategy,
            'training_periods': entry.training_periods,
            'validation_periods': entry.validation_periods,
            'test_periods': entry.test_periods,
            'algorithm': entry.algorithm,
            'hyperparameters': entry.hyperparameters,
            'random_seed': entry.random_seed,
            'validation_status': entry.validation_status,
            'approval_status': entry.approval_status,
            'approved_by_user_id': entry.approved_by_user_id,
            'approved_at': entry.approved_at.isoformat() if entry.approved_at else None,
            'previous_production_model_version': entry.previous_production_model_version,
            'promoted_at': entry.promoted_at.isoformat() if entry.promoted_at else None,
            'retired_at': entry.retired_at.isoformat() if entry.retired_at else None,
            'rejected_by_user_id': entry.rejected_by_user_id,
            'rejected_at': entry.rejected_at.isoformat() if entry.rejected_at else None,
            'rejection_reason': entry.rejection_reason,
        } for entry in entries],
    }


@app.post(f'{settings.api_v1_prefix}/models/registry', status_code=201)
def create_model_registry_entry(payload: ModelRegistryEntryInput, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    current_production = production_model_registry_entry()
    if payload.model_version == current_production['model_version']:
        raise HTTPException(status_code=409, detail='The current production model is already represented by the live artifact.')
    if db.query(ModelRegistryEntry.id).filter(ModelRegistryEntry.model_version == payload.model_version).first():
        raise HTTPException(status_code=409, detail='A registry entry with this model version already exists.')
    if any((payload.training_dataset_hash, payload.training_timestamp, payload.validation_metrics, payload.artifact_identifier, payload.artifact_hash)):
        raise HTTPException(status_code=422, detail='Training, artifact, and evaluation metadata must be produced by the Phase 3 pipeline, not supplied by a registry request.')
    entry = ModelRegistryEntry(
        model_id=payload.model_version,
        model_version=payload.model_version,
        model_type=payload.model_type,
        feature_schema=payload.feature_schema,
        feature_contract_version=payload.feature_contract_version,
        training_period=payload.training_period,
        training_dataset_version=payload.training_dataset_version,
        training_dataset_hash=payload.training_dataset_hash,
        training_timestamp=payload.training_timestamp,
        validation_metrics=payload.validation_metrics,
        artifact_identifier=payload.artifact_identifier,
        artifact_hash=payload.artifact_hash,
        notes=payload.notes,
        metrics=payload.validation_metrics,
        status=payload.status,
        deployment_status='SHADOW',
        created_by_user_id=user.id,
        validation_status='not_run',
        approval_status='not_approved',
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail='A registry entry with this model version already exists.') from None
    db.refresh(entry)
    phase3_audit(db, user, 'model.candidate.registered', result='candidate_metadata_only', model_version=entry.model_version, resource_id=entry.id, new_state={'status': entry.status, 'validation_status': entry.validation_status, 'approval_status': entry.approval_status, 'deployment_status': entry.deployment_status})
    return {
        'id': entry.id,
        'model_id': entry.model_id,
        'model_version': entry.model_version,
        'model_type': entry.model_type,
        'feature_contract_version': entry.feature_contract_version,
        'feature_contract': entry.feature_schema,
        'training_period': entry.training_period,
        'training_dataset_version': entry.training_dataset_version,
        'training_dataset_hash': entry.training_dataset_hash,
        'training_timestamp': entry.training_timestamp.isoformat() if entry.training_timestamp else None,
        'validation_metrics': entry.validation_metrics,
        'artifact_identifier': entry.artifact_identifier,
        'artifact_hash': entry.artifact_hash,
        'status': entry.status,
        'notes': entry.notes,
        'created_at': entry.created_at.isoformat(),
        'created_by_user_id': entry.created_by_user_id,
        'validation_status': entry.validation_status,
        'approval_status': entry.approval_status,
    }


@app.post(f'{settings.api_v1_prefix}/risk/simulate')
def risk_simulation(payload: RiskSimulationInput, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == payload.student_id).first() if payload.student_id is not None else None
    if student is None and payload.student_id is not None:
        raise HTTPException(status_code=404, detail='Student not found')
    if student is None:
        raise HTTPException(status_code=422, detail='A valid student_id is required for the simulation.')
    return simulate_student_risk(student, db, payload.scenario)


@app.get(f'{settings.api_v1_prefix}/risk/data-quality')
def risk_data_quality(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    return compute_data_quality_report(db)


@app.get(f'{settings.api_v1_prefix}/risk/transitions')
def risk_transitions(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    return risk_transition_report(db)


def phase3_policy_payload(policy: Phase3PolicyApproval | None) -> dict[str, Any]:
    if policy is None:
        return {'status': 'unapproved', 'approved_policy': None}
    return {
        'status': 'approved',
        'approved_policy': {
            'id': policy.id,
            'aggregation_strategy': policy.aggregation_strategy,
            'feature_contract': policy.feature_contract,
            'approved_by_user_id': policy.approved_by_user_id,
            'approved_at': policy.approved_at.isoformat(),
            'approval_note': policy.approval_note,
        },
    }


@app.get(f'{settings.api_v1_prefix}/ml/phase3/policy')
def phase3_policy(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    policy = db.query(Phase3PolicyApproval).order_by(Phase3PolicyApproval.id.desc()).first()
    return {
        **phase3_policy_payload(policy),
        'supported_aggregation_strategies': ['equal_subject_mean', FEATURE_SNAPSHOT_AGGREGATION_STRATEGY],
        'supported_feature_contract': ['fa1_mean', 'fa2_mean', 'assignment_mean', 'attendance_mean'],
        'supported_feature_contracts': {
            'equal_subject_mean': ['fa1_mean', 'fa2_mean', 'assignment_mean', 'attendance_mean'],
            FEATURE_SNAPSHOT_AGGREGATION_STRATEGY: list(FEATURES),
        },
        'note': 'Methodology approval is separate from data readiness and never creates candidate data or candidate models.',
    }


@app.post(f'{settings.api_v1_prefix}/ml/phase3/policy/approve')
def approve_phase3_policy(
    payload: Phase3PolicyApprovalRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not payload.explicit_approval:
        raise HTTPException(status_code=422, detail='Explicit faculty approval is required for the Phase 3 policy.')
    previous = db.query(Phase3PolicyApproval).order_by(Phase3PolicyApproval.id.desc()).first()
    previous_state = {
        'aggregation_strategy': previous.aggregation_strategy,
        'feature_contract': previous.feature_contract,
        'approved_by_user_id': previous.approved_by_user_id,
    } if previous else {'status': 'unapproved'}
    approval = Phase3PolicyApproval(
        aggregation_strategy=payload.aggregation_strategy,
        feature_contract=payload.feature_contract,
        approved_by_user_id=user.id,
        approved_at=datetime.utcnow(),
        approval_note=payload.approval_note,
    )
    db.add(approval)
    db.flush()
    new_state = {
        'aggregation_strategy': approval.aggregation_strategy,
        'feature_contract': approval.feature_contract,
        'approved_by_user_id': approval.approved_by_user_id,
    }
    phase3_audit(
        db, user, 'phase3.policy.approved', result='approved',
        reason=payload.approval_note, resource_id=approval.id,
        previous_state=previous_state, new_state=new_state,
    )
    return {'status': 'approved', 'approved_policy': phase3_policy_payload(approval)['approved_policy']}


@app.get(f'{settings.api_v1_prefix}/ml/phase3/readiness')
def phase3_readiness(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    dataset = build_phase3_dataset(db)
    phase3_audit(
        db, user, 'phase3.validation.checked', result='ready' if dataset['readiness']['training_ready'] else 'blocked',
        reason='; '.join(dataset['readiness']['blockers']), dataset_id=dataset['dataset_id'],
        dataset_version=dataset['dataset_version'], dataset_fingerprint=dataset['dataset_fingerprint'],
    )
    return phase3_public_report(dataset)


@app.get(f'{settings.api_v1_prefix}/ml/phase3/coverage')
def phase3_coverage(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    dataset = build_phase3_dataset(db)
    phase3_audit(
        db, user, 'phase3.validation.coverage_viewed', result='measured', dataset_id=dataset['dataset_id'],
        dataset_version=dataset['dataset_version'], dataset_fingerprint=dataset['dataset_fingerprint'],
    )
    return {
        'dataset_id': dataset['dataset_id'],
        'dataset_version': dataset['dataset_version'],
        'dataset_fingerprint': dataset['dataset_fingerprint'],
        'created_at': dataset['created_at'],
        'counts': dataset['counts'],
        'coverage': dataset['coverage'],
        'readiness': dataset['readiness'],
        'rejected_rows': dataset['rejected_rows'],
        'duplicate_import_rows': dataset['duplicate_import_rows'],
    }


@app.get(f'{settings.api_v1_prefix}/ml/phase3/dataset')
def phase3_dataset_export(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    aggregation_strategy: str | None = None,
    feature_contract: list[str] | None = Query(None),
) -> StreamingResponse:
    dataset = build_phase3_dataset(db, aggregation_strategy=aggregation_strategy, feature_contract=feature_contract)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=PHASE3_DATASET_FIELDS)
    writer.writeheader()
    export_rows = dataset['candidate_rows'] if aggregation_strategy and feature_contract else dataset['rows']
    for row in export_rows:
        values = dict(row)
        for name in ('exclusion_reasons', 'source_record_ids', 'source_subjects', 'source_batch_ids', 'source_filenames', 'source_checksums', 'feature_observed_at'):
            if name in values:
                values[name] = json.dumps(values[name], separators=(',', ':'))
        writer.writerow(values)
    phase3_audit(
        db, user, 'phase3.dataset.generated', result='aggregate_candidate_dataset_exported' if aggregation_strategy and feature_contract else 'source_observation_dataset_exported', dataset_id=dataset['dataset_id'],
        dataset_version=dataset['dataset_version'], dataset_fingerprint=dataset['dataset_fingerprint'],
    )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename="phase3-candidate-dataset.csv"',
            'X-Dataset-Id': dataset['dataset_id'],
            'X-Dataset-Version': dataset['dataset_version'],
            'X-Dataset-Fingerprint': dataset['dataset_fingerprint'],
            'X-Training-Eligible': str(dataset['training_eligible']).lower(),
        },
    )


def candidate_registry_payload(entry: ModelRegistryEntry) -> dict[str, Any]:
    return {
        'id': entry.id,
        'model_version': entry.model_version,
        'status': entry.status,
        'model_type': entry.model_type,
        'feature_contract': entry.feature_schema,
        'feature_contract_version': entry.feature_contract_version,
        'dataset_id': entry.dataset_id,
        'dataset_version': entry.dataset_version,
        'dataset_fingerprint': entry.dataset_fingerprint,
        'target_definition': entry.target_definition,
        'aggregation_strategy': entry.aggregation_strategy,
        'training_periods': entry.training_periods,
        'validation_periods': entry.validation_periods,
        'test_periods': entry.test_periods,
        'algorithm': entry.algorithm,
        'hyperparameters': entry.hyperparameters,
        'random_seed': entry.random_seed,
        'training_timestamp': entry.training_timestamp.isoformat() if entry.training_timestamp else None,
        'validation_metrics': entry.validation_metrics,
        'artifact_identifier': entry.artifact_identifier,
        'artifact_hash': entry.artifact_hash,
        'validation_status': entry.validation_status,
        'approval_status': entry.approval_status,
        'created_by_user_id': entry.created_by_user_id,
        'created_at': entry.created_at.isoformat(),
        'notes': entry.notes,
        'deployment_status': entry.deployment_status,
        'rejected_by_user_id': entry.rejected_by_user_id,
        'rejected_at': entry.rejected_at.isoformat() if entry.rejected_at else None,
        'rejection_reason': entry.rejection_reason,
    }


@app.get(f'{settings.api_v1_prefix}/ml/phase3/candidate')
def phase3_candidate(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    entry = db.query(ModelRegistryEntry).filter(
        ModelRegistryEntry.status == 'candidate',
        ModelRegistryEntry.artifact_identifier.is_not(None),
        ModelRegistryEntry.dataset_fingerprint.is_not(None),
    ).order_by(ModelRegistryEntry.created_at.desc()).first()
    return {'status': 'available' if entry else 'unavailable', 'candidate': candidate_registry_payload(entry) if entry else None}


@app.get(f'{settings.api_v1_prefix}/ml/phase3/evaluation')
def phase3_evaluation(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    entry = db.query(ModelRegistryEntry).filter(
        ModelRegistryEntry.status == 'candidate',
        ModelRegistryEntry.artifact_identifier.is_not(None),
        ModelRegistryEntry.validation_metrics.is_not(None),
    ).order_by(ModelRegistryEntry.created_at.desc()).first()
    return {'status': 'available' if entry and entry.validation_metrics else 'unavailable', 'model_version': entry.model_version if entry else None, 'evaluation': entry.validation_metrics if entry else None, 'detail': None if entry else 'No validated candidate evaluation is available.'}


@app.get(f'{settings.api_v1_prefix}/ml/phase3/compare')
def phase3_compare(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    production = production_model_registry_entry()
    entry = db.query(ModelRegistryEntry).filter(
        ModelRegistryEntry.status == 'candidate',
        ModelRegistryEntry.artifact_identifier.is_not(None),
    ).order_by(ModelRegistryEntry.created_at.desc()).first()
    common_test_set: dict[str, Any] | None = None
    common_test_error: str | None = None
    if entry and entry.aggregation_strategy == FEATURE_SNAPSHOT_AGGREGATION_STRATEGY:
        try:
            dataset = build_phase3_dataset(
                db,
                aggregation_strategy=entry.aggregation_strategy,
                feature_contract=entry.feature_schema,
            )
            if not dataset['readiness']['training_ready']:
                raise ValueError('; '.join(dataset['readiness']['blockers']))
            if entry.dataset_fingerprint != dataset['dataset_fingerprint']:
                raise ValueError('Candidate dataset fingerprint no longer matches current source data.')
            test_periods = set(entry.test_periods or [])
            test_rows = [
                row for row in dataset['candidate_rows']
                if row['academic_period'] in test_periods
            ]
            if not test_rows:
                raise ValueError('No current, temporally eligible observations match the candidate test periods.')
            candidate_path, _ = verify_candidate_artifact(entry, CANDIDATE_ARTIFACT_DIRECTORY)
            import pandas as pd

            common_test_set = evaluate_common_test_set(
                pd.DataFrame(test_rows),
                candidate_artifact_path=candidate_path,
                production_artifact_path=DEFAULT_ARTIFACT,
            )
        except (DeploymentArtifactError, KeyError, OSError, TypeError, ValueError) as exc:
            common_test_error = f'Common-test-set evaluation is unavailable: {exc}'
        if common_test_set is not None:
            return {
                'status': 'available',
                'comparison': {
                    'compatible': True,
                    'blockers': [],
                    'feature_contract': {
                        'compatible': entry.feature_schema == production['feature_contract'] == FEATURES,
                        'production': production['feature_contract'],
                        'candidate': entry.feature_schema,
                    },
                    'dataset_identity': {
                        'compatible': True,
                        'production_identity': 'production model evaluated on the candidate independent test rows',
                        'candidate_identity': entry.dataset_id,
                        'production_hash': production.get('training_dataset_hash'),
                        'candidate_hash': entry.dataset_fingerprint,
                        'hashes_match': False,
                        'same_temporal_test_rows_verified': True,
                        'common_test_row_identity_sha256': common_test_set['row_identity_sha256'],
                    },
                    'common_test_set': common_test_set,
                    'winner': None,
                    'interpretation': (
                        'Both models were evaluated on the same temporally held-out imported feature snapshots '
                        'and recorded independent outcomes. Metrics are descriptive; no model is automatically selected. '
                        'Import provenance does not independently authenticate the source records.'
                    ),
                },
                'production': {
                    'model_version': production['model_version'],
                    'demo_data': production['training_provenance']['demo_data'],
                    'feature_contract': production['feature_contract'],
                    'feature_contract_version': production['feature_contract_version'],
                    'dataset_identity': production['training_provenance'].get('source'),
                    'dataset_hash': production.get('training_dataset_hash'),
                    'training_timestamp': production.get('training_timestamp'),
                    'provenance': production['training_provenance'],
                    'metrics': common_test_set['production'],
                    'metric_context': 'Computed on the exact independent test rows shared with this candidate; the production artifact training provenance remains unchanged.',
                },
                'candidate': candidate_registry_payload(entry),
                'candidate_test_evaluation': common_test_set['candidate'],
                'candidate_test_provenance': {
                    'dataset_id': entry.dataset_id,
                    'dataset_version': entry.dataset_version,
                    'dataset_hash': entry.dataset_fingerprint,
                    'test_periods': entry.test_periods,
                    'feature_contract': entry.feature_schema,
                    'feature_contract_version': entry.feature_contract_version,
                    'artifact_hash': entry.artifact_hash,
                    'common_test_row_identity_sha256': common_test_set['row_identity_sha256'],
                },
            }
    production_contract = production['feature_contract']
    candidate_contract = entry.feature_schema if entry else None
    production_hash = production.get('training_dataset_hash')
    candidate_hash = entry.dataset_fingerprint if entry else None
    production_test = production.get('validation_metrics')
    candidate_test = (entry.validation_metrics or {}).get('test') if entry else None
    blockers: list[str] = []
    if entry is None:
        blockers.append('No candidate model with a registered artifact is available.')
    else:
        if common_test_error:
            blockers.append(common_test_error)
        if candidate_contract != production_contract:
            blockers.append('Candidate and production feature contracts are not identical.')
        if not candidate_hash or not entry.dataset_id or not entry.test_periods:
            blockers.append('Candidate test evaluation is missing dataset identity, fingerprint, or temporal test-period provenance.')
        if not isinstance(candidate_test, dict) or not candidate_test.get('sample_count'):
            blockers.append('Candidate held-out test metrics are unavailable.')
        if production['training_provenance'].get('demo_data') is True:
            blockers.append('Production evaluation provenance identifies demo/synthetic training data, not the candidate test dataset.')
        if not production_hash:
            blockers.append('Production evaluation has no verifiable training dataset hash.')
        elif candidate_hash and production_hash != candidate_hash:
            blockers.append('Production and candidate dataset hashes identify different evaluation data.')
        if not isinstance(production_test, dict):
            blockers.append('Production evaluation metrics are unavailable.')
        else:
            blockers.append('Production metrics do not identify the candidate temporal test rows and cannot be treated as common-test-set metrics.')
        blockers.append('A shared test-row identity and outcome provenance cannot be verified from the production artifact metadata.')
    blockers = list(dict.fromkeys(blockers))
    feature_contract_compatible = bool(entry and candidate_contract == production_contract)
    dataset_hashes_match = bool(entry and production_hash and candidate_hash and production_hash == candidate_hash)
    return {
        'status': 'unavailable',
        'comparison': {
            'compatible': False,
            'blockers': blockers,
            'feature_contract': {
                'compatible': feature_contract_compatible,
                'production': production_contract,
                'candidate': candidate_contract,
            },
            'dataset_identity': {
                'compatible': False,
                'production_identity': production['training_provenance'].get('source'),
                'candidate_identity': entry.dataset_id if entry else None,
                'production_hash': production_hash,
                'candidate_hash': candidate_hash,
                'hashes_match': dataset_hashes_match,
                'same_temporal_test_rows_verified': False,
            },
            'common_test_set': None,
            'winner': None,
            'interpretation': 'No comparative performance claim is made unless both models are evaluated on the exact same held-out rows with compatible feature contracts and verified provenance.',
        },
        'production': {
            'model_version': production['model_version'],
            'demo_data': production['training_provenance']['demo_data'],
            'feature_contract': production_contract,
            'feature_contract_version': production['feature_contract_version'],
            'dataset_identity': production['training_provenance'].get('source'),
            'dataset_hash': production_hash,
            'training_timestamp': production.get('training_timestamp'),
            'provenance': production['training_provenance'],
            'metrics': production_test,
            'metric_context': 'Artifact-recorded production metrics only; not a comparable evaluation and not evidence validating imported academic outcomes.',
        },
        'candidate': candidate_registry_payload(entry) if entry else None,
        'candidate_test_evaluation': candidate_test,
        'candidate_test_provenance': {
            'dataset_id': entry.dataset_id,
            'dataset_version': entry.dataset_version,
            'dataset_hash': candidate_hash,
            'test_periods': entry.test_periods,
            'feature_contract': candidate_contract,
            'feature_contract_version': entry.feature_contract_version,
            'artifact_hash': entry.artifact_hash,
        } if entry else None,
    }


@app.get(f'{settings.api_v1_prefix}/ml/phase3/shap')
def phase3_candidate_shap(user: CurrentTeacher, db: Session = Depends(get_db), student_id: int | None = None) -> dict[str, Any]:
    entry = db.query(ModelRegistryEntry).filter(
        ModelRegistryEntry.status == 'candidate',
        ModelRegistryEntry.validation_status == 'passed',
    ).order_by(ModelRegistryEntry.created_at.desc()).first()
    if entry is None or not entry.artifact_identifier:
        raise HTTPException(status_code=404, detail='Candidate SHAP unavailable because no validated candidate model exists.')
    student = db.query(Student).filter(Student.id == student_id).first() if student_id is not None else None
    if student_id is not None and student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    dataset = build_phase3_dataset(db, aggregation_strategy=entry.aggregation_strategy, feature_contract=entry.feature_schema)
    candidate_row = next((row for row in dataset['candidate_rows'] if row['student_identifier'] == student.student_id), None) if student else None
    if student and candidate_row is None:
        raise HTTPException(status_code=404, detail='No temporally eligible candidate dataset row exists for this student.')
    feature_values = {name: candidate_row[name] for name in entry.feature_schema} if candidate_row else {}
    try:
        artifact_path = candidate_artifact_path(entry, CANDIDATE_ARTIFACT_DIRECTORY)
    except DeploymentArtifactError as exc:
        raise HTTPException(status_code=404, detail=f'Validated candidate artifact is unavailable: {exc}') from exc
    try:
        explanation = explain_candidate(artifact_path, feature_values)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail='Candidate SHAP input does not match the validated candidate feature contract.') from exc
    return explanation


@app.post(f'{settings.api_v1_prefix}/ml/phase3/train')
def phase3_train_candidate(payload: Phase3TrainingRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> JSONResponse:
    dataset = build_phase3_dataset(
        db,
        aggregation_strategy=payload.aggregation_strategy,
        feature_contract=payload.feature_contract,
    )
    blockers = list(dataset['readiness']['blockers'])
    if not dataset['readiness']['training_ready']:
        reason = '; '.join(blockers)
        phase3_audit(
            db, user, 'phase3.training.blocked', result='blocked', reason=reason, dataset_id=dataset['dataset_id'],
            dataset_version=dataset['dataset_version'], dataset_fingerprint=dataset['dataset_fingerprint'],
        )
        return JSONResponse(status_code=409, content={'detail': {'message': 'Candidate training blocked: real-data readiness criteria are not satisfied.', 'blockers': blockers, 'dataset_id': dataset['dataset_id'], 'dataset_fingerprint': dataset['dataset_fingerprint']}})
    import pandas as pd

    candidate_frame = pd.DataFrame(dataset['candidate_rows'])
    try:
        trained = train_candidate(
            candidate_frame,
            output_directory=CANDIDATE_ARTIFACT_DIRECTORY,
            feature_contract=payload.feature_contract,
            target_column='risk_target',
            period_column='academic_period',
            identity_column='student_identifier',
            dataset_id=dataset['dataset_id'],
            dataset_version=dataset['dataset_version'],
            dataset_fingerprint=dataset['dataset_fingerprint'],
            target_definition=dataset['target_definition'],
            aggregation_strategy=payload.aggregation_strategy,
            feature_contract_version=(
                FEATURE_CONTRACT_VERSION
                if dataset['feature_contract_version'] == FEATURE_CONTRACT_VERSION
                else 'phase3-candidate-explicit-v1'
            ),
        )
    except ValueError as exc:
        reason = str(exc)
        phase3_audit(
            db, user, 'phase3.training.blocked', result='blocked', reason=reason, dataset_id=dataset['dataset_id'],
            dataset_version=dataset['dataset_version'], dataset_fingerprint=dataset['dataset_fingerprint'],
        )
        return JSONResponse(status_code=409, content={'detail': {'message': 'Candidate training blocked by temporal split or dataset validation.', 'blockers': [reason], 'dataset_id': dataset['dataset_id'], 'dataset_fingerprint': dataset['dataset_fingerprint']}})

    metadata = trained['metadata']
    entry = ModelRegistryEntry(
        model_id=metadata['model_version'],
        model_version=metadata['model_version'],
        model_type='classification',
        feature_schema=metadata['feature_contract'],
        feature_contract_version=metadata['feature_contract_version'],
        training_dataset_version=metadata['dataset_version'],
        training_dataset_hash=metadata['dataset_fingerprint'],
        training_timestamp=datetime.fromisoformat(metadata['training_timestamp']).replace(tzinfo=None),
        validation_metrics={
            'validation': metadata['validation_metrics'],
            'test': metadata['test_metrics'],
            'feature_importance': metadata['feature_importance'],
            'subgroup_evaluation': metadata['subgroup_evaluation'],
        },
        artifact_identifier=trained['artifact_path'].name,
        artifact_hash=trained['artifact_hash'],
        status='candidate',
        deployment_status='SHADOW',
        created_by_user_id=user.id,
        dataset_id=metadata['dataset_id'],
        dataset_version=metadata['dataset_version'],
        dataset_fingerprint=metadata['dataset_fingerprint'],
        target_definition=metadata['target_definition'],
        aggregation_strategy=metadata['aggregation_strategy'],
        training_periods=metadata['training_periods'],
        validation_periods=metadata['validation_periods'],
        test_periods=metadata['test_periods'],
        algorithm=metadata['algorithm'],
        hyperparameters=metadata['hyperparameters'],
        random_seed=metadata['random_seed'],
        validation_status='not_run',
        approval_status='not_approved',
        notes='Candidate artifact; production model remains unchanged pending independent governance review.',
    )
    db.add(entry)
    db.flush()
    phase3_audit(
        db, user, 'phase3.training.completed', result='candidate_created', dataset_id=dataset['dataset_id'],
        dataset_version=dataset['dataset_version'], dataset_fingerprint=dataset['dataset_fingerprint'],
        model_version=metadata['model_version'], resource_id=entry.id,
        new_state={'status': entry.status, 'validation_status': entry.validation_status, 'approval_status': entry.approval_status, 'deployment_status': entry.deployment_status},
    )
    phase3_audit(
        db, user, 'phase3.evaluation.completed', result='evaluated_on_temporal_holdouts',
        reason='validation and test metrics are recorded with split periods and sample distributions.',
        dataset_id=dataset['dataset_id'], dataset_version=dataset['dataset_version'],
        dataset_fingerprint=dataset['dataset_fingerprint'], model_version=metadata['model_version'], resource_id=entry.id,
        new_state={'status': entry.status, 'validation_status': entry.validation_status, 'approval_status': entry.approval_status, 'deployment_status': entry.deployment_status},
    )
    return JSONResponse(status_code=201, content={
        'status': 'candidate',
        'registry_entry': candidate_registry_payload(entry),
        'evaluation': {
            'validation': metadata['validation_metrics'],
            'test': metadata['test_metrics'],
            'feature_importance': metadata['feature_importance'],
            'subgroup_evaluation': metadata['subgroup_evaluation'],
        },
        'production_artifact_changed': False,
    })


def candidate_validation_blockers(entry: ModelRegistryEntry, dataset: dict[str, Any]) -> list[str]:
    blockers = list(dataset['readiness']['blockers'])
    if entry.dataset_fingerprint != dataset['dataset_fingerprint']:
        blockers.append('Candidate dataset fingerprint no longer matches the current source data.')
    required = {
        'artifact identifier': entry.artifact_identifier,
        'artifact hash': entry.artifact_hash,
        'dataset fingerprint': entry.dataset_fingerprint,
        'feature contract': entry.feature_schema,
        'target definition': entry.target_definition,
        'aggregation strategy': entry.aggregation_strategy,
        'training timestamp': entry.training_timestamp,
        'test metrics': entry.validation_metrics,
    }
    blockers.extend(f'Candidate {name} is unavailable.' for name, value in required.items() if not value)
    if entry.status != 'candidate':
        blockers.append('Only candidate registry entries may be validated.')
    try:
        candidate_artifact_path(entry, CANDIDATE_ARTIFACT_DIRECTORY)
    except DeploymentArtifactError as exc:
        blockers.append(str(exc))
    return blockers


@app.post(f'{settings.api_v1_prefix}/models/registry/{{entry_id}}/validate')
def validate_model_registry_entry(entry_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> JSONResponse:
    entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail='Model registry entry not found.')
    try:
        dataset = build_phase3_dataset(db, aggregation_strategy=entry.aggregation_strategy, feature_contract=entry.feature_schema)
    except ValueError:
        dataset = build_phase3_dataset(db)
    blockers = candidate_validation_blockers(entry, dataset)
    previous_state = {'validation_status': entry.validation_status, 'approval_status': entry.approval_status, 'status': entry.status}
    entry.validation_status = 'blocked' if blockers else 'passed'
    phase3_audit(
        db, user, 'model.candidate.validation.blocked' if blockers else 'model.candidate.validation.passed',
        result='blocked' if blockers else 'passed', reason='; '.join(blockers) if blockers else 'Candidate validation passed.',
        dataset_id=dataset['dataset_id'], dataset_version=dataset['dataset_version'],
        dataset_fingerprint=dataset['dataset_fingerprint'], model_version=entry.model_version, resource_id=entry.id,
        previous_state=previous_state, new_state={'validation_status': entry.validation_status, 'approval_status': entry.approval_status, 'status': entry.status},
    )
    status_code = 409 if blockers else 200
    return JSONResponse(status_code=status_code, content={'detail': {'result': 'blocked' if blockers else 'passed', 'model_version': entry.model_version, 'blockers': blockers}})


class ConcurrentDeploymentError(RuntimeError):
    pass


def persisted_deployment_snapshot(db: Session) -> dict[str, Any]:
    production = production_model_registry_entry()
    state = db.query(ModelDeploymentState).filter(ModelDeploymentState.id == 1).first()
    if state is None:
        return {'model_version': production['model_version'], 'artifact_hash': production['artifact_hash'], 'deployment_status': 'PRODUCTION_FALLBACK'}
    entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.model_version == state.active_model_version).first()
    return {
        'model_version': state.active_model_version,
        'artifact_hash': state.active_artifact_hash,
        'deployment_status': entry.deployment_status if entry else 'PRODUCTION_FALLBACK',
        'artifact_identifier': entry.artifact_identifier if entry else DEFAULT_ARTIFACT.name,
    }


def deployment_snapshot(db: Session) -> dict[str, Any]:
    production = production_model_registry_entry()
    state = db.query(ModelDeploymentState).filter(ModelDeploymentState.id == 1).first()
    if state is None:
        return {'model_version': production['model_version'], 'artifact_hash': production['artifact_hash'], 'deployment_status': 'PRODUCTION_FALLBACK'}
    path = selected_model_artifact(db)
    entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.model_version == state.active_model_version).first()
    return {
        'model_version': state.active_model_version,
        'artifact_hash': state.active_artifact_hash,
        'deployment_status': entry.deployment_status if entry else 'PRODUCTION_FALLBACK',
        'artifact_identifier': path.name,
    }


def add_deployment_event(
    db: Session,
    user: User,
    *,
    event: str,
    previous_state: dict[str, Any],
    new_state: dict[str, Any],
    candidate: ModelRegistryEntry | None,
    reason: str,
    status: str,
    rollback_of_event_id: int | None = None,
) -> ModelDeploymentEvent:
    event_row = ModelDeploymentEvent(
        event=event,
        previous_version=previous_state.get('model_version'),
        new_version=new_state.get('model_version'),
        candidate_version=candidate.model_version if candidate else None,
        artifact_hash=candidate.artifact_hash if candidate else new_state.get('artifact_hash'),
        actor_user_id=user.id,
        actor_role=user.role.upper(),
        occurred_at=datetime.utcnow(),
        reason=reason,
        status=status,
        rollback_of_event_id=rollback_of_event_id,
        dataset_hash=candidate.dataset_fingerprint if candidate else None,
        feature_contract=candidate.feature_schema if candidate else None,
        evaluation_reference=candidate.validation_metrics if candidate else None,
        previous_state=previous_state,
        new_state=new_state,
    )
    db.add(event_row)
    if event == 'PROMOTE' and status == 'blocked':
        db.add(AuditLog(
            actor_user_id=user.id, action='model.promotion.blocked', resource_type='model_registry_entry',
            resource_id=candidate.model_version if candidate else None,
            detail={'actor_role': user.role.upper(), 'reason': reason, 'previous_state': previous_state, 'new_state': new_state},
        ))
    if event == 'ROLLBACK' and status == 'blocked':
        db.add(AuditLog(
            actor_user_id=user.id, action='model.rollback.blocked', resource_type='model_registry_entry',
            resource_id=candidate.model_version if candidate else previous_state.get('model_version'),
            detail={'actor_role': user.role.upper(), 'reason': reason, 'previous_state': previous_state, 'new_state': new_state},
        ))
    db.add(AuditLog(
        actor_user_id=user.id,
        action=f'model.deployment.{event.casefold()}.{status}',
        resource_type='model_deployment',
        resource_id=candidate.model_version if candidate else new_state.get('model_version'),
        detail={
            'event': event,
            'status': status,
            'reason': reason,
            'actor_role': user.role.upper(),
            'candidate_version': candidate.model_version if candidate else None,
            'artifact_hash': candidate.artifact_hash if candidate else new_state.get('artifact_hash'),
            'dataset_hash': candidate.dataset_fingerprint if candidate else None,
            'feature_contract': candidate.feature_schema if candidate else None,
            'evaluation_reference': candidate.validation_metrics if candidate else None,
            'rollback_of_event_id': rollback_of_event_id,
            'previous_state': previous_state,
            'new_state': new_state,
        },
    ))
    return event_row


def _apply_active_version(
    db: Session,
    *,
    previous_state: dict[str, Any],
    new_state: dict[str, Any],
) -> None:
    current = db.query(ModelDeploymentState).filter(ModelDeploymentState.id == 1).with_for_update().first()
    if current is None:
        current = ModelDeploymentState(
            id=1,
            active_model_version=previous_state['model_version'],
            active_artifact_hash=previous_state['artifact_hash'],
        )
        db.add(current)
        db.flush()
    changed = db.query(ModelDeploymentState).filter(
        ModelDeploymentState.id == 1,
        ModelDeploymentState.active_model_version == previous_state['model_version'],
        ModelDeploymentState.active_artifact_hash == previous_state['artifact_hash'],
    ).update({
        ModelDeploymentState.active_model_version: new_state['model_version'],
        ModelDeploymentState.active_artifact_hash: new_state['artifact_hash'],
        ModelDeploymentState.updated_at: datetime.utcnow(),
    }, synchronize_session=False)
    if changed != 1:
        raise ConcurrentDeploymentError('Active local model version changed during the transaction.')
    current.active_model_version = new_state['model_version']
    current.active_artifact_hash = new_state['artifact_hash']
    current.updated_at = datetime.utcnow()


@app.post(f'{settings.api_v1_prefix}/models/registry/{{entry_id}}/promote')
def promote_model_registry_entry(entry_id: int, payload: ModelPromotionRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> JSONResponse:
    entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail='Model registry entry not found.')
    try:
        dataset = build_phase3_dataset(db, aggregation_strategy=entry.aggregation_strategy, feature_contract=entry.feature_schema)
    except ValueError:
        dataset = build_phase3_dataset(db)
    try:
        previous_state = deployment_snapshot(db)
    except HTTPException as exc:
        previous_state = persisted_deployment_snapshot(db)
        active_error = str(exc.detail)
    else:
        active_error = None
    blockers = list(dataset['readiness']['blockers'])
    blockers.extend(candidate_validation_blockers(entry, dataset))
    if active_error:
        blockers.append(active_error)
    if entry.created_by_user_id is None:
        blockers.append('Candidate creator identity is unavailable; independent approval cannot be verified.')
    if not payload.explicit_approval:
        blockers.append('Explicit authorized deployment approval was not provided.')
    if len(payload.approval_note.strip()) < 10:
        blockers.append('A deployment reason of at least 10 characters is required.')
    if entry.status != 'candidate':
        blockers.append('Only a non-rejected candidate may be deployed.')
    if entry.validation_status != 'passed':
        blockers.append('Candidate validation has not passed.')
    approval_error = independent_candidate_approval_error(db, entry)
    if approval_error:
        blockers.append(approval_error)
    try:
        verified_path, _ = verify_candidate_artifact(entry, CANDIDATE_ARTIFACT_DIRECTORY)
    except DeploymentArtifactError as exc:
        blockers.append(str(exc))
        verified_path = None
    if previous_state.get('model_version') == entry.model_version:
        blockers.append('This candidate is already the active local model.')
    blockers = list(dict.fromkeys(blockers))
    if blockers:
        reason = '; '.join(blockers)
        unchanged_state = dict(previous_state)
        add_deployment_event(
            db, user, event='PROMOTE', previous_state=previous_state, new_state=unchanged_state,
            candidate=entry, reason=reason, status='blocked',
        )
        db.commit()
        return JSONResponse(status_code=409, content={'detail': {'result': 'blocked', 'message': 'Local model deployment blocked by readiness, governance, or artifact compatibility checks.', 'blockers': blockers}})

    if verified_path is None:
        raise HTTPException(status_code=500, detail='Candidate passed deployment checks without a verified artifact path.')
    new_state = {
        'model_version': entry.model_version,
        'artifact_hash': entry.artifact_hash,
        'deployment_status': 'LOCAL_ACTIVE',
        'artifact_identifier': verified_path.name,
    }
    try:
        _apply_active_version(db, previous_state=previous_state, new_state=new_state)
        old_entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.model_version == previous_state['model_version']).first()
        if old_entry is not None:
            old_entry.deployment_status = 'SHADOW'
        entry.deployment_status = 'LOCAL_ACTIVE'
        entry.previous_production_model_version = previous_state['model_version']
        entry.promoted_at = datetime.utcnow()
        entry.retired_at = None
        add_deployment_event(
            db, user, event='PROMOTE', previous_state=previous_state, new_state=new_state,
            candidate=entry, reason=payload.approval_note, status='succeeded',
        )
        db.commit()
    except (IntegrityError, ConcurrentDeploymentError):
        db.rollback()
        return JSONResponse(status_code=409, content={'detail': {'result': 'blocked', 'message': 'Concurrent local deployment state change; no deployment was applied.', 'blockers': ['Active version changed during deployment.']}})
    return JSONResponse(status_code=200, content={
        'result': 'deployed', 'deployment_target': 'LOCAL', 'active_model_version': entry.model_version,
        'artifact_hash': entry.artifact_hash, 'production_artifact_changed': False,
    })


@app.post(f'{settings.api_v1_prefix}/models/registry/{{entry_id}}/approve')
def approve_model_registry_entry(entry_id: int, payload: ModelApprovalRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> JSONResponse:
    entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail='Model registry entry not found.')
    try:
        dataset = build_phase3_dataset(db, aggregation_strategy=entry.aggregation_strategy, feature_contract=entry.feature_schema)
    except ValueError:
        dataset = build_phase3_dataset(db)
    previous_state = {'validation_status': entry.validation_status, 'approval_status': entry.approval_status, 'status': entry.status}
    blockers = candidate_validation_blockers(entry, dataset)
    if entry.created_by_user_id is None:
        blockers.append('Candidate creator identity is unavailable; independent approval cannot be verified.')
    elif entry.created_by_user_id == user.id:
        blockers.append('The candidate creator cannot approve their own candidate.')
    if entry.validation_status != 'passed':
        blockers.append('Candidate validation has not passed.')
    if not payload.explicit_approval:
        blockers.append('Explicit candidate approval was not provided.')
    if blockers:
        reason = '; '.join(blockers)
        phase3_audit(
            db, user, 'model.candidate.approval.blocked', result='blocked', reason=reason,
            dataset_id=dataset['dataset_id'], dataset_version=dataset['dataset_version'],
            dataset_fingerprint=dataset['dataset_fingerprint'], model_version=entry.model_version,
            resource_id=entry.id, previous_state=previous_state, new_state=previous_state,
        )
        return JSONResponse(status_code=409, content={'detail': {'result': 'blocked', 'message': 'Candidate approval blocked by readiness or validation gates.', 'blockers': blockers}})
    entry.approval_status = 'approved'
    entry.approved_by_user_id = user.id
    entry.approved_at = datetime.utcnow()
    entry.notes = f'{entry.notes or ""}\nApproval note: {payload.approval_note}'.strip()
    phase3_audit(
        db, user, 'model.candidate.approval.granted', result='approved_for_review', reason=payload.approval_note,
        dataset_id=dataset['dataset_id'], dataset_version=dataset['dataset_version'],
        dataset_fingerprint=dataset['dataset_fingerprint'], model_version=entry.model_version,
        resource_id=entry.id, previous_state=previous_state,
        new_state={'validation_status': entry.validation_status, 'approval_status': entry.approval_status, 'status': entry.status},
    )
    return JSONResponse(status_code=200, content={'result': 'approved', 'model_version': entry.model_version, 'approval_status': entry.approval_status, 'approved_by_user_id': entry.approved_by_user_id, 'approved_at': entry.approved_at.isoformat(), 'production_changed': False})


@app.post(f'{settings.api_v1_prefix}/models/registry/{{entry_id}}/reject')
def reject_model_registry_entry(entry_id: int, payload: ModelRejectionRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> JSONResponse:
    entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail='Model registry entry not found.')
    previous_state = {'status': entry.status, 'deployment_status': entry.deployment_status, 'approval_status': entry.approval_status}
    active = persisted_deployment_snapshot(db)
    rejection_blockers = []
    if entry.status != 'candidate':
        rejection_blockers.append('Only a candidate registry entry can be rejected.')
    if active['model_version'] == entry.model_version:
        rejection_blockers.append('The active local model cannot be rejected; roll it back first.')
    if rejection_blockers:
        reason = '; '.join(rejection_blockers)
        add_deployment_event(
            db, user, event='REJECT', previous_state=active, new_state=active, candidate=entry,
            reason=reason, status='blocked',
        )
        db.commit()
        return JSONResponse(status_code=409, content={'detail': {'result': 'blocked', 'message': 'Candidate rejection was blocked.', 'blockers': rejection_blockers}})
    entry.status = 'rejected'
    entry.deployment_status = 'REJECTED'
    entry.rejected_by_user_id = user.id
    entry.rejected_at = datetime.utcnow()
    entry.rejection_reason = payload.rejection_reason
    new_state = {'status': entry.status, 'deployment_status': entry.deployment_status, 'approval_status': entry.approval_status}
    add_deployment_event(
        db, user, event='REJECT', previous_state=active, new_state=active, candidate=entry,
        reason=payload.rejection_reason, status='rejected',
    )
    db.add(AuditLog(
        actor_user_id=user.id, action='model.candidate.rejected', resource_type='model_registry_entry',
        resource_id=str(entry.id), detail={
            'actor_role': user.role.upper(), 'reason': payload.rejection_reason,
            'previous_state': previous_state, 'new_state': new_state,
            'active_model_version': active['model_version'], 'dataset_hash': entry.dataset_fingerprint,
            'feature_contract': entry.feature_schema, 'evaluation_reference': entry.validation_metrics,
        },
    ))
    db.commit()
    return JSONResponse(status_code=200, content={'result': 'rejected', 'model_version': entry.model_version, 'status': entry.status, 'production_changed': False})


@app.post(f'{settings.api_v1_prefix}/models/registry/{{entry_id}}/rollback')
def rollback_model_registry_entry(entry_id: int, payload: ModelRollbackRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> JSONResponse:
    entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail='Model registry entry not found.')
    previous_state = persisted_deployment_snapshot(db)
    if not payload.explicit_approval:
        reason = 'Explicit faculty approval is required for local rollback.'
        add_deployment_event(
            db, user, event='ROLLBACK', previous_state=previous_state, new_state=previous_state,
            candidate=entry, reason=reason, status='blocked',
        )
        db.commit()
        return JSONResponse(status_code=409, content={'detail': {'result': 'blocked', 'message': reason, 'blockers': [reason]}})
    blockers: list[str] = []
    production = production_model_registry_entry()
    if entry.model_version != previous_state['model_version'] or entry.deployment_status != 'LOCAL_ACTIVE':
        blockers.append('The requested registry entry is not the active local deployment.')
    if previous_state['model_version'] == production['model_version']:
        blockers.append('Rollback unavailable: no verified prior local deployment exists.')
    deployed_event = None
    if not blockers:
        deployed_event = db.query(ModelDeploymentEvent).filter(
            ModelDeploymentEvent.event == 'PROMOTE',
            ModelDeploymentEvent.status == 'succeeded',
            ModelDeploymentEvent.new_version == previous_state['model_version'],
        ).order_by(ModelDeploymentEvent.id.desc()).first()
        if deployed_event is None or not deployed_event.previous_version:
            blockers.append('Rollback unavailable: no verified prior deployment is recorded.')
    target_version = deployed_event.previous_version if deployed_event else previous_state['model_version']
    target_entry = db.query(ModelRegistryEntry).filter(ModelRegistryEntry.model_version == target_version).first() if target_version else None
    if not blockers and target_version == production['model_version']:
        previous_hash = (deployed_event.previous_state or {}).get('artifact_hash')
        try:
            production_hash_matches = sha256_file(DEFAULT_ARTIFACT).casefold() == str(production['artifact_hash']).casefold()
        except OSError:
            production_hash_matches = False
        if previous_hash != production['artifact_hash'] or not production_hash_matches:
            blockers.append('Rollback target production artifact hash is unavailable or changed.')
        new_state = {'model_version': target_version, 'artifact_hash': production['artifact_hash'], 'deployment_status': 'PRODUCTION_FALLBACK', 'artifact_identifier': DEFAULT_ARTIFACT.name}
    elif not blockers:
        if target_entry is None or target_entry.status != 'candidate' or target_entry.deployment_status == 'REJECTED':
            blockers.append('Rollback target registry candidate is unavailable or rejected.')
        elif target_entry.validation_status != 'passed':
            blockers.append('Rollback target no longer has passed validation.')
        if not blockers and target_entry is not None:
            approval_error = independent_candidate_approval_error(db, target_entry)
            if approval_error:
                blockers.append(f'Rollback target {approval_error[0].lower()}{approval_error[1:]}')
        target_hash = (deployed_event.previous_state or {}).get('artifact_hash') if deployed_event else None
        if not blockers and (not target_hash or target_entry is None or target_entry.artifact_hash != target_hash):
            blockers.append('Rollback target artifact hash does not match its prior deployment record.')
        target_path = None
        if not blockers and target_entry is not None:
            try:
                target_path, _ = verify_candidate_artifact(target_entry, CANDIDATE_ARTIFACT_DIRECTORY)
            except DeploymentArtifactError as exc:
                blockers.append(f'Rollback target verification failed: {exc}')
        new_state = {
            'model_version': target_version,
            'artifact_hash': target_hash,
            'deployment_status': 'LOCAL_ACTIVE',
            'artifact_identifier': target_path.name if target_path else None,
        }
    else:
        new_state = dict(previous_state)
    if blockers:
        reason = '; '.join(blockers)
        add_deployment_event(
            db, user, event='ROLLBACK', previous_state=previous_state, new_state=previous_state,
            candidate=entry, reason=reason, status='blocked',
            rollback_of_event_id=deployed_event.id if deployed_event else None,
        )
        db.commit()
        return JSONResponse(status_code=409, content={'detail': {'result': 'blocked', 'message': 'Rollback is unavailable because no verified prior deployment can be restored.', 'blockers': blockers}})
    try:
        _apply_active_version(db, previous_state=previous_state, new_state=new_state)
        entry.deployment_status = 'SHADOW'
        entry.retired_at = datetime.utcnow()
        if target_entry is not None:
            target_entry.deployment_status = 'LOCAL_ACTIVE'
            target_entry.retired_at = None
        add_deployment_event(
            db, user, event='ROLLBACK', previous_state=previous_state, new_state=new_state,
            candidate=target_entry, reason=payload.rollback_note, status='succeeded',
            rollback_of_event_id=deployed_event.id if deployed_event else None,
        )
        db.commit()
    except (IntegrityError, ConcurrentDeploymentError):
        db.rollback()
        return JSONResponse(status_code=409, content={'detail': {'result': 'blocked', 'message': 'Concurrent local deployment state change; rollback was not applied.'}})
    return JSONResponse(status_code=200, content={'result': 'rolled_back', 'deployment_target': 'LOCAL', 'active_model_version': target_version, 'rollback_of_event_id': deployed_event.id if deployed_event else None})


@app.get(f'{settings.api_v1_prefix}/models/deployments')
def model_deployment_history(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        active = deployment_snapshot(db)
    except HTTPException as exc:
        raise HTTPException(status_code=503, detail=f'Active deployment is unsafe or unavailable: {exc.detail}') from exc
    events = db.query(ModelDeploymentEvent).order_by(ModelDeploymentEvent.id.desc()).all()
    return {
        'deployment_target': 'LOCAL',
        'active': active,
        'history': [{
            'id': item.id, 'event': item.event, 'previous_version': item.previous_version,
            'new_version': item.new_version, 'candidate_version': item.candidate_version,
            'artifact_hash': item.artifact_hash, 'actor_user_id': item.actor_user_id,
            'actor_role': item.actor_role, 'occurred_at': item.occurred_at.isoformat(),
            'reason': item.reason, 'status': item.status,
            'rollback_of_event_id': item.rollback_of_event_id, 'dataset_hash': item.dataset_hash,
            'feature_contract': item.feature_contract, 'evaluation_reference': item.evaluation_reference,
            'previous_state': item.previous_state, 'new_state': item.new_state,
        } for item in events],
    }


@app.get(f'{settings.api_v1_prefix}/ml/phase3/audit')
def phase3_governance_audit(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    entries = db.query(AuditLog).filter(
        AuditLog.resource_type.in_(['phase3_model_governance', 'model_deployment', 'model_registry_entry']),
    ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).all()
    return {
        'items': [{
            'id': item.id,
            'actor_user_id': item.actor_user_id,
            'action': item.action,
            'resource_type': item.resource_type,
            'resource_id': item.resource_id,
            'occurred_at': item.created_at.isoformat(),
            'detail': item.detail,
        } for item in entries],
    }


@app.get(f'{settings.api_v1_prefix}/risk/monitoring')
def risk_monitoring(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    students = db.query(Student).all()
    risk_views, _, momentum_by_student = student_risk_views(students, db)
    demo_model = early_warning_uses_demo_model(db)
    scorable_students = [
        student for student, (values, _) in zip(students, risk_views)
        if values['risk_status'] in {'available', 'unvalidated-demo-model'}
    ]
    scorable_ids = {student.id for student in scorable_students}
    current_predictions = [prediction for _, prediction in risk_views if prediction['risk_level'] in {'HIGH', 'MEDIUM', 'LOW'}]
    counts = {level: sum(prediction['risk_level'] == level for prediction in current_predictions) for level in ('HIGH', 'MEDIUM', 'LOW')}
    history_query = db.query(RiskSnapshot).filter(RiskSnapshot.student_id.in_(scorable_ids))
    history = history_query.order_by(RiskSnapshot.recorded_at).all()
    transition_report = risk_transition_report(db, scorable_ids)
    transitions = [{
        **item,
        'name': item['student_name'],
        'from': item['previous_risk'],
        'to': item['new_risk'],
    } for item in transition_report['transitions']]
    momentum = {level: 0 for level in ('IMPROVING', 'STABLE', 'DECLINING', 'VOLATILE', 'INSUFFICIENT_DATA')}
    for student in scorable_students:
        level = momentum_by_student[student.id].get('assessment', calculate_momentum([]))['classification']
        momentum[level] = momentum.get(level, 0) + 1
    origin_by_student = {student.id: student.data_origin for student in students}
    return {
        'distribution': counts,
        'momentum': momentum,
        'transitions': transitions,
        'transition_history_status': transition_report['history_status'],
        'students_with_insufficient_transition_history': transition_report['students_with_insufficient_history'],
        'risk_status': 'unvalidated-demo-model' if demo_model else 'available',
        'history_note': (
            'Unvalidated demo history is restricted to DEMO DATA records. Historical saved class labels may predate the current argmax policy.'
            if demo_model else
            'Historical saved class labels; they may predate the current argmax policy. Current distribution is calculated from the current model.'
        ),
        'history': [{
            'student_id': item.student_id,
            'data_origin': origin_by_student.get(item.student_id, 'LEGACY / UNVERIFIED'),
            'risk_level': item.risk_level,
            'recorded_at': item.recorded_at.isoformat(),
        } for item in history],
    }


@app.get(f'{settings.api_v1_prefix}/risk/high-risk-demo')
def high_risk_demo(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    students = db.query(Student).order_by(Student.student_id).all()
    risk_views, features_by_student, _ = student_risk_views(students, db)
    demo_model = early_warning_uses_demo_model(db)
    candidates = [
        (student, features_by_student[student.id], prediction)
        for student, (values, prediction) in zip(students, risk_views)
        if values['risk_status'] in {'available', 'unvalidated-demo-model'}
    ]
    if not candidates:
        raise HTTPException(status_code=404, detail='No model-eligible student records are available for this demonstration.')
    predicted_high = [item for item in candidates if item[2]['risk_level'] == 'HIGH']
    selection_pool = predicted_high or candidates
    student, features, prediction = min(
        selection_pool,
        key=lambda item: (-item[2]['class_probabilities'].get('HIGH', 0.0), item[0].student_id.casefold()),
    )
    try:
        explanation = (
            explain_with_demo_preview(features, target_class=prediction['risk_level'])
            if demo_model else explain_with_active_model(features, db, target_class=prediction['risk_level'])
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail='SHAP model background is unavailable.') from exc
    return {
        'student': {
            'id': student.id,
            'student_id': student.student_id,
            'full_name': student.full_name,
            'data_origin': student.data_origin,
            'risk_status': 'unvalidated-demo-model' if demo_model else 'available',
        },
        'prediction': prediction,
        'features': features,
        'explanation': explanation,
        'high_risk_available': bool(predicted_high),
        'selection_note': 'Highest current P(HIGH) among existing model-predicted HIGH students.' if predicted_high else 'No current prediction is HIGH; showing the existing student with the highest P(HIGH) without changing the canonical prediction.',
    }


@app.get(f'{settings.api_v1_prefix}/early-warnings')
def early_warnings(
    user: CurrentTeacher,
    db: Session = Depends(get_db),
    severity: str | None = Query(default=None),
    department: str | None = Query(default=None),
    course: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=50),
) -> dict[str, Any]:
    demo_model = early_warning_uses_demo_model(db)
    data_status = 'unvalidated-demo-model' if demo_model else 'validated-model'
    message = (
        'DEMO DATA / RULE-GENERATED PREVIEW: predictions use the unvalidated synthetic CSV candidate model. '
        'Labels were generated by transparent rules, not historical outcomes, and must not be used as institutional decisions.'
        if demo_model else None
    )
    excluded_non_demo_count = 0
    insufficient_data_count = 0

    def empty_response() -> dict[str, Any]:
        return {
            'summary': {
                'signals': 0,
                'highPriority': 0,
                'watchList': 0,
                'recoveredThisMonth': 0,
                'modelRefreshedAt': datetime.utcnow().isoformat(),
            },
            'items': [],
            'total': 0,
            'nextPage': None,
            'dataStatus': data_status,
            'message': message,
            'excludedNonDemoCount': excluded_non_demo_count,
            'insufficientDataCount': insufficient_data_count,
        }

    wanted_class = None
    if severity:
        wanted_class = severity.strip().upper()
        wanted_class = {'AT RISK': 'HIGH', 'WATCH': 'MEDIUM', 'NORMAL': 'LOW'}.get(wanted_class, wanted_class)
        if wanted_class not in {'HIGH', 'MEDIUM', 'LOW'}:
            raise HTTPException(status_code=422, detail='severity must be High, Medium, or Low.')

    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        return empty_response()
    courses = db.query(Course).filter(Course.teacher_id == teacher.id).all()
    course_ids = [item.id for item in courses]
    sections = sorted({item.section for item in courses if item.section})
    if not course_ids:
        return empty_response()
    assigned_filter = or_(
        Student.course_id.in_(course_ids),
        and_(Student.course_id.is_(None), Student.section.in_(sections)) if sections else False,
    )
    students_query = db.query(Student).options(joinedload(Student.course)).filter(assigned_filter)
    if department:
        students_query = students_query.filter(Student.department == department)
    if course:
        students_query = students_query.filter(or_(Course.code == course, Course.name == course))
        students_query = students_query.join(Course, Course.id == Student.course_id)
    if q and q.strip():
        needle = f'%{q.strip()}%'
        students_query = students_query.filter(or_(Student.full_name.ilike(needle), Student.student_id.ilike(needle)))
    all_students = students_query.order_by(Student.full_name, Student.id).all()

    if demo_model:
        eligible_students = [student for student in all_students if student.data_origin == 'DEMO DATA']
        excluded_non_demo_count = len(all_students) - len(eligible_students)
        all_students = eligible_students
        if excluded_non_demo_count:
            message = (
                f'{message} {excluded_non_demo_count} non-demo or unverified record(s) were excluded from risk scoring.'
            )

    if not all_students:
        return empty_response()

    risk_views, features_by_student, momentum_by_student = student_risk_views(all_students, db)
    if len(risk_views) != len(all_students):
        raise HTTPException(status_code=503, detail='The active risk model did not return a prediction for every authorized student.')
    qualified_rows = [
        (student, risk_view)
        for student, risk_view in zip(all_students, risk_views)
        if risk_view_is_scorable(risk_view)
    ]
    insufficient_data_count = len(all_students) - len(qualified_rows)
    if insufficient_data_count:
        message = (
            f'{message} {insufficient_data_count} record(s) with insufficient model-supported signals were excluded.'
            if message else
            f'{insufficient_data_count} record(s) with insufficient model-supported signals were excluded.'
        )
    all_students = [student for student, _ in qualified_rows]
    risk_views = [risk_view for _, risk_view in qualified_rows]
    if not all_students:
        return empty_response()
    snapshots_by_student: dict[int, list[RiskSnapshot]] = {}
    for snapshot in db.query(RiskSnapshot).filter(
        RiskSnapshot.student_id.in_([student.id for student in all_students]),
    ).order_by(RiskSnapshot.student_id, RiskSnapshot.recorded_at).all():
        snapshots_by_student.setdefault(snapshot.student_id, []).append(snapshot)

    items: list[dict[str, Any]] = []
    for student, (_, prediction) in zip(all_students, risk_views):
        features = features_by_student[student.id]
        predicted_class = str(prediction.get('risk_level', '')).upper()
        if predicted_class not in {'HIGH', 'MEDIUM', 'LOW'}:
            raise HTTPException(status_code=503, detail='Active risk model returned an unsupported predicted class.')
        probability = high_class_probability(prediction)
        priority = priority_for_probability(probability)
        risk_class = 'At risk' if priority == 'High' else 'Watch' if priority == 'Medium' else 'Normal'
        class_rank = {'High': 2, 'Medium': 1, 'Low': 0}[priority]

        factor_labels = {
            'attendance': 'Attendance',
            'missing_submissions': 'Missing submissions',
            'activity_per_week': 'Portal activity',
            'attendance_change': 'Attendance',
            'assignment_submission_rate': 'Assignment submission rate',
            'assignments_completed': 'Assignments completed',
        }
        try:
            explanation = (
                explain_with_demo_preview(features, target_class='HIGH')
                if demo_model
                else explain_with_active_model(features, db, target_class='HIGH')
            )
            positive = [
                (name, value)
                for name, value in explanation['contributions'].items()
                if name in FEATURES and value > 0
            ]
            positive.sort(key=lambda item: item[1], reverse=True)
            top_factor_name = positive[0][0] if positive else 'Not enough data yet'
        except (FileNotFoundError, ValueError):
            top_factor_name = 'Not enough data yet'
        top_factor = factor_labels.get(top_factor_name, top_factor_name.replace('_', ' ').capitalize())

        student_signals = 0
        if features['attendance_change'] < -10:
            student_signals += 1
        if features['missing_submissions'] > 0:
            student_signals += 1
        if features.get('assignment_submission_rate', 0.0) < 0.75:
            student_signals += 1
        if features['activity_per_week'] < 12:
            student_signals += 1
        snapshots = snapshots_by_student.get(student.id, [])
        if prediction['risk_level'] == 'HIGH':
            student_signals += 1
        if len(snapshots) > 1:
            previous, current = snapshots[-2], snapshots[-1]
            order = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
            if order.get(current.risk_level, 0) > order.get(previous.risk_level, 0):
                student_signals += 1

        items.append({
            'studentId': student.id,
            'name': student.full_name,
            'department': student.department,
            'dataOrigin': student.data_origin,
            'riskStatus': 'unvalidated-demo-model' if demo_model else 'available',
            'riskProbability': round(probability, 6),
            'riskClass': risk_class,
            'priority': priority,
            'topFactor': top_factor,
            '_classRank': class_rank,
            '_signalCount': student_signals,
            '_rawDecisionScore': float(prediction.get('raw_high_decision_score', probability)),
        })

    high_priority = sum(item['priority'] == 'High' for item in items)
    watch_list = sum(item['priority'] == 'Medium' for item in items)
    low_priority = sum(item['priority'] == 'Low' for item in items)
    month_cutoff = datetime.utcnow() - timedelta(days=30)
    recovered = 0
    for student_id in [item['studentId'] for item in items]:
        snapshots = [
            snapshot for snapshot in snapshots_by_student.get(student_id, [])
            if snapshot.recorded_at >= month_cutoff
            and (snapshot.model_version or '').startswith('early-warning:')
        ]
        if len(snapshots) >= 2:
            order = {'Low': 0, 'Medium': 1, 'High': 2}
            first_priority = priority_for_probability(float(snapshots[0].probability))
            latest_priority = priority_for_probability(float(snapshots[-1].probability))
            if order[latest_priority] < order[first_priority]:
                recovered += 1

    items.sort(key=lambda item: (
        -item['_classRank'],
        -item['riskProbability'],
        -item['_rawDecisionScore'],
        -item['_signalCount'],
        item['name'].casefold(),
        item['studentId'],
    ))
    if wanted_class:
        wanted_priority = {'HIGH': 'High', 'MEDIUM': 'Medium', 'LOW': 'Low'}[wanted_class]
        items = [item for item in items if item['priority'] == wanted_priority]
    high_priority = sum(item['priority'] == 'High' for item in items)
    watch_list = sum(item['priority'] == 'Medium' for item in items)
    low_priority = sum(item['priority'] == 'Low' for item in items)
    total = len(items)
    start = (page - 1) * pageSize
    paged = [{key: value for key, value in item.items() if not key.startswith('_')} for item in items[start:start + pageSize]]
    next_page = page + 1 if start + pageSize < total else None
    return {
        'summary': {
            'signals': total,
            'highPriority': high_priority,
            'watchList': watch_list,
            'lowPriority': low_priority,
            'recoveredThisMonth': recovered,
            'modelRefreshedAt': datetime.utcnow().isoformat(),
        },
        'items': paged,
        'total': total,
        'nextPage': next_page,
        'dataStatus': data_status,
        'message': message,
        'excludedNonDemoCount': excluded_non_demo_count,
        'insufficientDataCount': insufficient_data_count,
    }


@app.get(f'{settings.api_v1_prefix}/early-warnings/{{student_id}}')
def early_warning_detail(student_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = early_warning_authorized_student(db, user, student_id)
    demo_model = early_warning_uses_demo_model(db)
    if demo_model and student.data_origin != 'DEMO DATA':
        raise HTTPException(status_code=503, detail='The active model is trained on DEMO DATA and cannot assess this non-demo or unverified record.')
    usable_signals = student_model_signal_counts([student], db)[student.id]
    if usable_signals < MINIMUM_USABLE_SIGNALS:
        raise HTTPException(status_code=409, detail='There are not enough model-supported signals to produce an Early Warning prediction.')
    features = student_features(student, db)
    prediction = predict_with_demo_preview(features) if demo_model else predict_with_active_model(features, db)
    class_probabilities = prediction['class_probabilities']
    probability = high_class_probability(prediction)
    priority = priority_for_probability(probability)
    risk_class = 'At risk' if priority == 'High' else 'Watch' if priority == 'Medium' else 'Normal'

    explanation = (
        explain_with_demo_preview(features, target_class='HIGH')
        if demo_model
        else explain_with_active_model(features, db, target_class='HIGH')
    )
    normalised = normalise_positive_shap([
        (name, value)
        for name, value in explanation['contributions'].items()
        if name in FEATURES
    ])
    positive = sorted(normalised.items(), key=lambda item: item[1], reverse=True)

    labels = {
        'attendance': ('Attendance', '%'),
        'missing_submissions': ('Missing submissions', 'assignments'),
        'activity_per_week': ('Portal activity', 'logins/week'),
        'assignments_completed': ('Assignments completed', 'assignments'),
        'assignment_submission_rate': ('Assignment submission rate', '%'),
        'attendance_change': ('Attendance change', '%'),
    }
    grouped_contributions: dict[str, float] = {}
    for name, contribution in positive:
        key = 'attendance' if name == 'attendance_change' else name
        grouped_contributions[key] = grouped_contributions.get(key, 0.0) + contribution
    factors: list[dict[str, Any]] = []
    for name, contribution in sorted(grouped_contributions.items(), key=lambda item: item[1], reverse=True)[:5]:
        label, unit = labels.get(name, (name.replace('_', ' ').capitalize(), ''))
        current_value = float(features.get(name, 0.0))
        previous_value = current_value
        if name == 'attendance':
            change = float(features.get(f'{name}_change', 0.0))
            direction = 'down' if change < 0 else 'up'
            movement = f', {direction} {abs(change):.1f}% across recent observations' if change else ''
            detail = f'{label} {current_value:.1f}%{movement}'
        elif name == 'missing_submissions':
            detail = f'{current_value:.0f} outstanding assignments'
        elif name == 'activity_per_week':
            detail = f'{current_value:.1f} logins per week'
        elif name == 'assignments_completed':
            detail = f'{current_value:.0f} assignments completed'
        elif name == 'assignment_submission_rate':
            detail = f'{current_value * 100:.0f}% of assignments submitted'
        else:
            detail = f'{label} {current_value:.1f}'
        factors.append({
            'key': name,
            'label': label,
            'detail': detail,
            'currentValue': current_value,
            'previousValue': previous_value,
            'unit': unit,
            'contributionShare': round(contribution, 6),
        })

    trend = weekly_snapshot_trend(db, student.id)
    if trend:
        week_change = round((trend[-1]['risk'] - trend[-2]['risk']) * 100, 1) if len(trend) > 1 else 0.0
    else:
        week_change = 0.0

    no_action_features = dict(features)
    no_action_features['attendance'] = max(0.0, features['attendance'] + features['attendance_change'] * 2)
    no_action_features['missing_submissions'] = max(0.0, features['missing_submissions'])
    no_action = predict_with_demo_preview(no_action_features) if demo_model else predict_with_active_model(no_action_features, db)
    no_action_probability = high_class_probability(no_action)

    plan_features = dict(features)
    plan_features['attendance'] = max(features['attendance'], 85.0)
    plan_features['missing_submissions'] = 0.0
    plan_features['assignment_submission_rate'] = 1.0
    plan_features['activity_per_week'] = max(features.get('activity_per_week', 12.0), 12.0)
    plan_features['attendance_change'] = max(0.0, features['attendance_change'])
    with_plan = predict_with_demo_preview(plan_features) if demo_model else predict_with_active_model(plan_features, db)
    with_plan_probability = high_class_probability(with_plan)

    max_class_probability = max(float(value) for value in class_probabilities.values()) if class_probabilities else 0.0
    confidence = round(max(0.0, min(1.0, max_class_probability * (usable_signals / 5))), 6)

    precautions_by_factor = {
        'attendance': 'Agree an 85% attendance target for three weeks and review progress every Friday.',
        'missing_submissions': 'Set a clear deadline for each missing item and offer one supervised resubmission.',
        'activity_per_week': 'Ask what is blocking portal activity and re-enable deadline reminders.',
        'assignments_completed': 'Agree a clear deadline for each outstanding item and check completion together.',
        'assignment_submission_rate': 'Set weekly submission checkpoints and improve the assignment completion rate.',
    }
    precautions = [
        {
            'factorKey': factor['key'],
            'text': precautions_by_factor.get(factor['key'], f'Review {factor["label"].lower()} with the student and agree on a small check-in plan.'),
        }
        for factor in factors[:3]
    ]

    interventions = db.query(Intervention).filter(
        Intervention.student_id == student.id,
    ).order_by(Intervention.created_at.desc()).all()
    actions = [{
        'type': item.type or item.title,
        'label': item.title,
        'at': item.created_at.isoformat(),
        'by': item.teacher_id,
    } for item in interventions]

    audit(db, user, 'early_warning.detail.viewed', 'student', student.id)
    db.commit()
    return {
        'studentId': student.id,
        'name': student.full_name,
        'department': student.department,
        'dataOrigin': student.data_origin,
        'riskStatus': 'unvalidated-demo-model' if demo_model else 'available',
        'riskProbability': round(probability, 6),
        'riskClass': risk_class,
        'priority': priority,
        'confidence': confidence,
        'signalsUsed': usable_signals,
        'weekChange': week_change,
        'insufficientData': False,
        'trend': trend,
        'forecast': {
            'noAction': round(min(1.0, max(0.0, no_action_probability)), 6),
            'withPlan': round(min(1.0, max(0.0, with_plan_probability)), 6),
        },
        'factors': factors,
        'precautions': precautions,
        'actions': actions,
        'dataStatus': 'unvalidated-demo-model' if demo_model else 'validated-model',
        'message': (
            'DEMO / RULE-GENERATED PREVIEW: candidate prediction and explanation use synthetic training data.'
            if demo_model else None
        ),
    }


SIMULATION_REQUESTS: dict[tuple[int, int], list[datetime]] = {}


def risk_view_is_scorable(risk_view: tuple[Any, dict[str, Any]]) -> bool:
    values, prediction = risk_view
    if isinstance(values, dict):
        return values.get('risk_status') in {'available', 'unvalidated-demo-model'}
    return str(prediction.get('risk_level', '')).upper() in {'HIGH', 'MEDIUM', 'LOW'}


@app.post(f'{settings.api_v1_prefix}/early-warnings/{{student_id}}/simulate')
def early_warning_simulate(
    student_id: int,
    payload: EarlyWarningSimulationRequest,
    user: CurrentTeacher,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    now = datetime.utcnow()
    key = (user.id, student_id)
    recent = [stamp for stamp in SIMULATION_REQUESTS.get(key, []) if now - stamp < timedelta(minutes=1)]
    if len(recent) >= 30:
        raise HTTPException(status_code=429, detail='Simulation rate limit exceeded. Try again shortly.')
    recent.append(now)
    SIMULATION_REQUESTS[key] = recent

    student = early_warning_authorized_student(db, user, student_id)
    demo_model = early_warning_uses_demo_model(db)
    if demo_model and student.data_origin != 'DEMO DATA':
        raise HTTPException(status_code=503, detail='The active model is trained on DEMO DATA and cannot simulate this non-demo or unverified record.')
    if student_model_signal_counts([student], db)[student.id] < MINIMUM_USABLE_SIGNALS:
        raise HTTPException(status_code=409, detail='There are not enough model-supported signals to simulate this student.')
    supported_targets = {
        'attendance', 'missing_submissions', 'activity_per_week',
        'attendance_change', 'assignment_submission_rate', 'assignments_completed',
    }
    unsupported = sorted(set(payload.recoveries) - supported_targets)
    if unsupported:
        raise HTTPException(status_code=422, detail=f'Unsupported recovery factor(s): {", ".join(unsupported)}')
    if not payload.recoveries:
        raise HTTPException(status_code=422, detail='Provide at least one recovery fraction.')
    for factor, value in payload.recoveries.items():
        if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
            raise HTTPException(status_code=422, detail=f'{factor} recovery must be between 0 and 1.')

    features = student_features(student, db)
    candidate = dict(features)
    activity_history = [
        observation.value for observation in db.query(AcademicMomentum).filter(
            AcademicMomentum.student_id == student.id,
            AcademicMomentum.metric == 'activity',
        ).order_by(AcademicMomentum.observed_at, AcademicMomentum.id).all()
    ]
    targets = {
        'attendance': max(features['attendance'], 85.0),
        'missing_submissions': 0.0,
        'activity_per_week': max(
            features['activity_per_week'],
            sum(activity_history[:-4]) / len(activity_history[:-4]) if len(activity_history) > 4 else features['activity_per_week'],
        ),
        'attendance_change': max(0.0, features['attendance_change']),
        'assignment_submission_rate': 1.0,
        'assignments_completed': max(
            features['assignments_completed'],
            max(features['assignments_completed'], 0.0) + features['missing_submissions'],
        ),
    }
    baseline_prediction = predict_with_demo_preview(features) if demo_model else predict_with_active_model(features, db)
    for factor, recovery in payload.recoveries.items():
        start = float(features[factor])
        target = float(targets[factor])
        candidate[factor] = start + (target - start) * float(recovery)
    simulated_prediction = predict_with_demo_preview(candidate) if demo_model else predict_with_active_model(candidate, db)
    probability = high_class_probability(simulated_prediction)
    baseline_probability = high_class_probability(baseline_prediction)
    risk_class = 'At risk' if probability >= HIGH_PRIORITY_THRESHOLD else 'Watch' if probability >= MEDIUM_PRIORITY_THRESHOLD else 'Normal'
    audit(db, user, 'early_warning.simulated', 'student', student.id)
    db.commit()
    return {
        'riskProbability': round(probability, 6),
        'riskClass': risk_class,
        'delta': round((baseline_probability - probability) * 100, 2),
        'before': {
            'riskLevel': baseline_prediction['risk_level'],
            'riskProbability': round(baseline_probability, 6),
            'classProbabilities': baseline_prediction['class_probabilities'],
        },
        'after': {
            'riskLevel': simulated_prediction['risk_level'],
            'riskProbability': round(probability, 6),
            'classProbabilities': simulated_prediction['class_probabilities'],
        },
        'demoMode': bool(demo_model),
        'label': 'DEMO / RULE-GENERATED TRAINING DATA' if demo_model else 'MODEL PREDICTED',
        'modelVersion': simulated_prediction['model_version'],
    }


def save_early_warning_intervention(
    db: Session,
    user: User,
    student: Student,
    *,
    action_type: str,
    title: str,
    body: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    item = Intervention(
        student_id=student.id,
        teacher_id=teacher.id if teacher else None,
        type=action_type,
        payload=payload,
        title=title,
        body=body,
        status='saved',
        sent_at=datetime.utcnow(),
    )
    db.add(item)
    db.flush()
    if student.user_id:
        db.add(Notification(
            user_id=student.user_id,
            kind='support',
            title=title,
            body=body,
            metadata_json={'intervention_id': item.id, 'type': action_type, **payload},
        ))
    audit(db, user, f'early_warning.{action_type}', 'intervention', item.id)
    db.commit()
    db.refresh(item)
    return {
        'id': item.id,
        'type': action_type,
        'label': item.title,
        'at': item.created_at.isoformat(),
        'by': item.teacher_id,
        'payload': payload,
        'status': item.status,
    }


@app.post(f'{settings.api_v1_prefix}/interventions/meetings', status_code=201)
def early_warning_meeting(payload: MeetingInterventionRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = early_warning_authorized_student(db, user, payload.studentId)
    body = f'{payload.note.strip()} Meeting scheduled for {payload.slotStart.isoformat()} for {payload.durationMin} minutes.' if payload.note.strip() else f'Meeting scheduled for {payload.slotStart.isoformat()} for {payload.durationMin} minutes.'
    return save_early_warning_intervention(
        db, user, student,
        action_type='meeting',
        title='Host a meeting',
        body=body,
        payload={'slotStart': payload.slotStart.isoformat(), 'durationMin': payload.durationMin, 'note': payload.note},
    )


@app.post(f'{settings.api_v1_prefix}/interventions/tasks', status_code=201)
def early_warning_task(payload: TaskInterventionRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = early_warning_authorized_student(db, user, payload.studentId)
    body = f'{payload.taskType}. Due {payload.dueAt.isoformat()}.' + (f' {payload.note.strip()}' if payload.note.strip() else '')
    return save_early_warning_intervention(
        db, user, student,
        action_type='task',
        title='Assign a recovery task',
        body=body,
        payload={'taskType': payload.taskType, 'dueAt': payload.dueAt.isoformat(), 'note': payload.note},
    )


@app.post(f'{settings.api_v1_prefix}/interventions/mentor-alert', status_code=201)
def early_warning_mentor_alert(payload: MentorAlertRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = early_warning_authorized_student(db, user, payload.studentId)
    return save_early_warning_intervention(
        db, user, student,
        action_type='mentor_alert',
        title='Alert class mentor',
        body=payload.message,
        payload={'message': payload.message},
    )


@app.post(f'{settings.api_v1_prefix}/subject-recovery/enrol', status_code=201)
def early_warning_subject_recovery(payload: SubjectRecoveryRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = early_warning_authorized_student(db, user, payload.studentId)
    body = 'Student added to subject recovery.' + (f' Subject ID: {payload.subjectId}.' if payload.subjectId else '')
    return save_early_warning_intervention(
        db, user, student,
        action_type='subject_recovery',
        title='Add to Subject recovery',
        body=body,
        payload={'subjectId': payload.subjectId},
    )


@app.get(f'{settings.api_v1_prefix}/risk/feature-importance')
def risk_feature_importance(user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    students = db.query(Student).limit(100).all()
    features_by_student, _ = student_feature_batch(students, db)
    rows = [features_by_student[student.id] for student in students]
    try:
        demo_model = early_warning_uses_demo_model(db)
        artifact_path = demo_preview_artifact_path() if demo_model else selected_model_artifact(db)
        result = global_importance(rows, artifact_path)
        result['dataStatus'] = 'unvalidated-demo-model' if demo_model else 'validated-model'
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail='Risk model is not trained. Run the documented training command first.') from exc
    except (ValueError, KeyError, AttributeError) as exc:
        raise HTTPException(
            status_code=503,
            detail='Risk feature importance is unavailable because the active model does not support the marks-free explanation contract.',
        ) from exc


@app.get(f'{settings.api_v1_prefix}/assignments', response_model=list[AssignmentOut])
def list_assignments(user: CurrentUser, db: Session = Depends(get_db), subject_code: str | None = None) -> list[Assignment]:
    query = db.query(Assignment)
    if subject_code:
        subject = db.query(Subject).filter(Subject.code == subject_code.upper()).first()
        if subject is None:
            raise HTTPException(status_code=404, detail='Subject not found')
        query = query.filter(Assignment.subject_id == subject.id)
    if user.role.upper() == 'STUDENT':
        student = db.query(Student).filter(Student.user_id == user.id).first()
        if student is None:
            raise HTTPException(status_code=403, detail='Student profile is not linked')
        assigned_ids = select(AssignmentStudent.assignment_id).where(AssignmentStudent.student_id == student.id)
        query = query.filter((Assignment.status == 'active') & ((Assignment.id.in_(assigned_ids)) | (Assignment.section == student.section)))
    return query.order_by(Assignment.due_date).all()


def resolve_assignment_subject(db: Session, subject_name: str) -> tuple[int | None, str]:
    subject = db.query(Subject).filter((Subject.code.ilike(subject_name)) | (Subject.name.ilike(subject_name))).first()
    if subject:
        return subject.id, subject.name
    if db.query(Subject.id).first():
        raise HTTPException(status_code=422, detail='Choose a subject from the institutional subject list.')
    return None, subject_name


@app.post(f'{settings.api_v1_prefix}/assignments', response_model=AssignmentOut, status_code=201)
def create_assignment(payload: AssignmentCreate, user: CurrentTeacher, db: Session = Depends(get_db)) -> Assignment:
    values = payload.model_dump()
    if values['max_marks'] > 25:
        raise HTTPException(status_code=422, detail='New assignments cannot exceed 25 marks.')
    subject_id, subject_name = resolve_assignment_subject(db, values['subject'])
    values.update(subject=subject_name, subject_id=subject_id)
    assignment = Assignment(**values)
    db.add(assignment)
    db.flush()
    audit(db, user, 'assignment.created', 'assignment', assignment.id)
    eligible_students = db.query(Student).filter(Student.section == assignment.section).all() if assignment.section else db.query(Student).all()
    for student in eligible_students:
        db.add(AssignmentStudent(assignment_id=assignment.id, student_id=student.id))
        if student.user_id:
            db.add(Notification(user_id=student.user_id, kind='assignment', title='New assignment', body=assignment.title, metadata_json={'assignment_id': assignment.id}))
    db.commit()
    db.refresh(assignment)
    return assignment


@app.post(f'{settings.api_v1_prefix}/assignments/{{assignment_id}}/duplicate', response_model=AssignmentOut, status_code=201)
def duplicate_assignment(assignment_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> Assignment:
    original = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if original is None:
        raise HTTPException(status_code=404, detail='Assignment not found')
    duplicate = Assignment(
        title=f'{original.title} (copy)', subject=original.subject, course=original.course, section=original.section,
        subject_id=original.subject_id,
        description=original.description, instructions=original.instructions, start_date=original.start_date,
        due_date=original.due_date, max_marks=original.max_marks, status='draft',
        allowed_file_types=original.allowed_file_types, max_file_size=original.max_file_size, rubric_id=original.rubric_id,
    )
    db.add(duplicate)
    audit(db, user, 'assignment.duplicated', 'assignment', original.id)
    db.commit()
    db.refresh(duplicate)
    return duplicate


@app.patch(f'{settings.api_v1_prefix}/assignments/{{assignment_id}}/status', response_model=AssignmentOut)
def change_assignment_status(assignment_id: int, payload: AssignmentStatusUpdate, user: CurrentTeacher, db: Session = Depends(get_db)) -> Assignment:
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if assignment is None:
        raise HTTPException(status_code=404, detail='Assignment not found')
    assignment.status = payload.status
    audit(db, user, f'assignment.{payload.status}', 'assignment', assignment.id)
    db.commit()
    db.refresh(assignment)
    return assignment


@app.patch(f'{settings.api_v1_prefix}/assignments/{{assignment_id}}', response_model=AssignmentOut)
def update_assignment(assignment_id: int, payload: AssignmentCreate, user: CurrentTeacher, db: Session = Depends(get_db)) -> Assignment:
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if assignment is None:
        raise HTTPException(status_code=404, detail='Assignment not found')
    values = payload.model_dump()
    if values['max_marks'] > max(25, assignment.max_marks):
        raise HTTPException(status_code=422, detail='Assignment maximum cannot be increased above 25 marks.')
    subject_id, subject_name = resolve_assignment_subject(db, values['subject'])
    values.update(subject=subject_name, subject_id=subject_id)
    for key, value in values.items():
        setattr(assignment, key, value)
    audit(db, user, 'assignment.updated', 'assignment', assignment.id)
    db.commit()
    db.refresh(assignment)
    return assignment


@app.get(f'{settings.api_v1_prefix}/submissions', response_model=list[SubmissionOut])
def list_submissions(user: CurrentUser, db: Session = Depends(get_db)) -> list[Submission]:
    query = db.query(Submission)
    if user.role.upper() == 'STUDENT':
        student = db.query(Student).filter(Student.user_id == user.id).first()
        query = query.filter(Submission.student_id == student.id if student else -1)
    return query.order_by(Submission.submitted_at.desc()).all()


@app.post(f'{settings.api_v1_prefix}/submissions', response_model=SubmissionOut, status_code=201)
async def submit_assignment(
    student: CurrentStudent,
    db: Session = Depends(get_db),
    assignment_id: int = Form(...),
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
) -> Submission:
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id, Assignment.status == 'active').first()
    if assignment is None:
        raise HTTPException(status_code=404, detail='Active assignment not found')
    assigned = db.query(AssignmentStudent.id).filter(AssignmentStudent.assignment_id == assignment.id, AssignmentStudent.student_id == student.id).first()
    if assigned is None and assignment.section != student.section:
        raise HTTPException(status_code=403, detail='Assignment is not assigned to this student')
    late = bool(assignment.due_date and datetime.utcnow() > assignment.due_date)
    existing = db.query(Submission).filter(Submission.assignment_id == assignment.id, Submission.student_id == student.id).first()
    if existing is not None and existing.status == 'graded':
        raise HTTPException(status_code=409, detail='Graded submissions cannot be replaced')
    page_files = files or ([file] if file else [])
    if not page_files:
        raise HTTPException(status_code=400, detail='Upload at least one assignment file.')
    saved_pages = []
    try:
        for page_file in page_files:
            saved_pages.append(await store_submission(page_file, student.id, assignment, settings))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if existing is None:
        existing = Submission(student_id=student.id, assignment_id=assignment.id)
        db.add(existing)
        db.flush()
    version = db.query(SubmissionVersion).filter(SubmissionVersion.submission_id == existing.id).count()
    for page_number, (saved_path, original_name) in enumerate(saved_pages, start=1):
        db.add(SubmissionVersion(
            submission_id=existing.id,
            version_number=version + page_number,
            page_number=page_number,
            page_count=len(saved_pages),
            file_name=original_name,
            stored_path=str(saved_path),
        ))
    existing.file_name = ', '.join(original_name for _, original_name in saved_pages)[:255]
    existing.stored_path = str(saved_pages[-1][0])
    existing.page_count = len(saved_pages)
    existing.submitted_at = datetime.utcnow()
    existing.status = 'late' if late else 'submitted'
    actor = db.query(User).filter(User.id == student.user_id).first()
    if actor:
        audit(db, actor, 'submission.created', 'submission', existing.id)
    db.commit()
    db.refresh(existing)
    return existing


@app.get(f'{settings.api_v1_prefix}/assignments/{{assignment_id}}/submissions', response_model=list[SubmissionOut])
def assignment_submissions(assignment_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> list[Submission]:
    if db.query(Assignment.id).filter(Assignment.id == assignment_id).first() is None:
        raise HTTPException(status_code=404, detail='Assignment not found')
    return db.query(Submission).filter(Submission.assignment_id == assignment_id).all()


@app.get(f'{settings.api_v1_prefix}/assignments/{{assignment_id}}/summary')
def assignment_summary(assignment_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if assignment is None:
        raise HTTPException(status_code=404, detail='Assignment not found')
    students = db.query(Student).join(AssignmentStudent).filter(AssignmentStudent.assignment_id == assignment.id).all()
    submissions = db.query(Submission).filter(Submission.assignment_id == assignment.id).all()
    counts = {name: 0 for name in ('assigned', 'submitted', 'pending', 'not_submitted', 'late', 'graded')}
    counts['assigned'] = len(students)
    counts['submitted'] = sum(bool(item.stored_path) and item.status in {'submitted', 'late', 'graded'} for item in submissions)
    counts['late'] = sum(bool(item.stored_path) and item.status == 'late' for item in submissions)
    counts['graded'] = sum(bool(item.stored_path) and item.status == 'graded' for item in submissions)
    submissions_by_student = {item.student_id: item for item in submissions}
    tracking = []
    for item in students:
        submission = submissions_by_student.get(item.id)
        uploaded = bool(submission and submission.stored_path)
        status = submission.status if uploaded and submission else 'not_submitted'
        if not uploaded:
            counts['not_submitted'] += 1
        tracking.append({
            'id': item.id,
            'student_id': item.student_id,
            'name': item.full_name,
            'department': item.department,
            'section': item.section,
            'uploaded': uploaded,
            'status': status,
            'pages': submission.page_count if uploaded and submission else 0,
            'submitted_at': submission.submitted_at.isoformat() if uploaded and submission and submission.submitted_at else None,
            'deadline': assignment.due_date.isoformat() if assignment.due_date else None,
            'grade': submission.grade if submission else None,
            'feedback': submission.feedback if submission else None,
            'submission_id': submission.id if submission else None,
        })
    counts['pending'] = counts['not_submitted']
    return {'assignment': AssignmentOut.model_validate(assignment).model_dump(), 'counts': counts, 'students': tracking}


@app.post(f'{settings.api_v1_prefix}/assignments/{{assignment_id}}/rubric', status_code=201)
def create_rubric(assignment_id: int, payload: RubricCreate, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if assignment is None:
        raise HTTPException(status_code=404, detail='Assignment not found')
    if sum(item.maximum_score for item in payload.criteria) > assignment.max_marks:
        raise HTTPException(status_code=422, detail='Rubric total exceeds the assignment maximum')
    rubric = db.query(Rubric).filter(Rubric.id == assignment.rubric_id).first() if assignment.rubric_id else None
    if rubric is None:
        rubric = Rubric(title=payload.title)
        db.add(rubric)
    else:
        rubric.criteria.clear()
        rubric.title = payload.title
    rubric.criteria = [RubricCriterion(name=item.name, maximum_score=item.maximum_score) for item in payload.criteria]
    db.flush()
    assignment.rubric_id = rubric.id
    audit(db, user, 'rubric.created', 'assignment', assignment.id)
    db.commit()
    return {'id': rubric.id, 'title': rubric.title, 'criteria': [{'id': item.id, 'name': item.name, 'maximum_score': item.maximum_score} for item in rubric.criteria]}


@app.get(f'{settings.api_v1_prefix}/assignments/{{assignment_id}}/rubric')
def get_rubric(assignment_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, Any]:
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if assignment is None or assignment.rubric_id is None:
        raise HTTPException(status_code=404, detail='Rubric not found')
    rubric = db.query(Rubric).filter(Rubric.id == assignment.rubric_id).first()
    return {'id': rubric.id, 'title': rubric.title, 'criteria': [{'id': item.id, 'name': item.name, 'maximum_score': item.maximum_score} for item in rubric.criteria]}


@app.post(f'{settings.api_v1_prefix}/assignments/{{assignment_id}}/reminders')
def send_assignment_reminders(assignment_id: int, student_ids: list[int], user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, int]:
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if assignment is None:
        raise HTTPException(status_code=404, detail='Assignment not found')
    pending = db.query(Student).join(AssignmentStudent).filter(AssignmentStudent.assignment_id == assignment_id, Student.id.in_(student_ids)).all()
    created = 0
    for student in pending:
        has_submission = db.query(Submission.id).filter(Submission.assignment_id == assignment_id, Submission.student_id == student.id).first()
        if not has_submission and student.user_id:
            db.add(Notification(user_id=student.user_id, kind='deadline', title='Assignment reminder', body=f'Reminder: {assignment.title} is awaiting your submission.', metadata_json={'assignment_id': assignment.id}))
            created += 1
    audit(db, user, 'assignment.reminders_sent', 'assignment', assignment_id)
    db.commit()
    return {'reminders_created': created}


@app.get(f'{settings.api_v1_prefix}/submissions/{{submission_id}}/versions')
def submission_versions(submission_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    owner_id = db.query(Student.user_id).filter(Student.id == submission.student_id).scalar() if submission else None
    if submission is None or (user.role.upper() == 'STUDENT' and owner_id != user.id):
        raise HTTPException(status_code=404, detail='Submission not found')
    versions = db.query(SubmissionVersion).filter(SubmissionVersion.submission_id == submission.id).order_by(SubmissionVersion.version_number.desc()).all()
    return [{'id': item.id, 'version_number': item.version_number, 'page_number': item.page_number, 'page_count': item.page_count, 'file_name': item.file_name, 'submitted_at': item.submitted_at.isoformat()} for item in versions]


@app.get(f'{settings.api_v1_prefix}/submissions/{{submission_id}}/versions/{{version_id}}/file')
def download_submission_version(submission_id: int, version_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> FileResponse:
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    owner_id = db.query(Student.user_id).filter(Student.id == submission.student_id).scalar() if submission else None
    if submission is None or (user.role.upper() == 'STUDENT' and owner_id != user.id):
        raise HTTPException(status_code=404, detail='Submission not found')
    version = db.query(SubmissionVersion).filter_by(id=version_id, submission_id=submission.id).first()
    if version is None:
        raise HTTPException(status_code=404, detail='Submission page not found')
    file_path = Path(version.stored_path).resolve()
    root = settings.upload_directory.resolve()
    if root not in file_path.parents or not file_path.is_file():
        raise HTTPException(status_code=404, detail='Submission file not found')
    media_type = mimetypes.guess_type(version.file_name)[0] or 'application/octet-stream'
    safe_inline = media_type in {'application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'text/plain'}
    return FileResponse(file_path, filename=version.file_name, media_type=media_type, content_disposition_type='inline' if safe_inline else 'attachment')


@app.get(f'{settings.api_v1_prefix}/submissions/{{submission_id}}/file')
def download_submission(submission_id: int, user: CurrentUser, db: Session = Depends(get_db), inline: bool = False) -> FileResponse:
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    student_owner = db.query(Student.user_id).filter(Student.id == submission.student_id).scalar() if submission else None
    if submission is None or (user.role.upper() == 'STUDENT' and student_owner != user.id):
        raise HTTPException(status_code=404, detail='Submission not found')
    file_path = Path(submission.stored_path or '').resolve()
    root = settings.upload_directory.resolve()
    if root not in file_path.parents or not file_path.is_file():
        raise HTTPException(status_code=404, detail='Submission file not found')
    media_type = mimetypes.guess_type(submission.file_name or file_path.name)[0] or 'application/octet-stream'
    safe_inline = inline and media_type in {'application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'text/plain'}
    return FileResponse(file_path, filename=submission.file_name or file_path.name, media_type=media_type, content_disposition_type='inline' if safe_inline else 'attachment')


@app.patch(f'{settings.api_v1_prefix}/submissions/{{submission_id}}/grade', response_model=SubmissionOut)
def grade_submission(submission_id: int, payload: GradeRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> Submission:
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if submission is None:
        raise HTTPException(status_code=404, detail='Submission not found')
    assignment = db.query(Assignment).filter(Assignment.id == submission.assignment_id).first()
    if assignment is None:
        raise HTTPException(status_code=404, detail='Assignment not found')
    rubric_total = 0.0
    if payload.rubric_scores:
        if assignment.rubric_id is None:
            raise HTTPException(status_code=422, detail='This assignment has no rubric')
        criteria = {item.id: item for item in db.query(RubricCriterion).filter(RubricCriterion.rubric_id == assignment.rubric_id).all()}
        if set(payload.rubric_scores) - set(criteria):
            raise HTTPException(status_code=422, detail='A rubric criterion does not belong to this assignment')
        for criterion_id, score in payload.rubric_scores.items():
            criterion = criteria[criterion_id]
            if score < 0 or score > criterion.maximum_score:
                raise HTTPException(status_code=422, detail=f'Score exceeds the maximum for {criterion.name}')
            rubric_total += score
            existing_score = db.query(RubricScore).filter(RubricScore.submission_id == submission.id, RubricScore.criterion_id == criterion_id).first()
            if existing_score:
                existing_score.score = score
            else:
                db.add(RubricScore(submission_id=submission.id, criterion_id=criterion_id, score=score))
    final_grade = rubric_total if payload.rubric_scores else payload.grade
    if final_grade > assignment.max_marks:
        raise HTTPException(status_code=422, detail='Grade exceeds assignment maximum')
    submission.grade = final_grade
    submission.feedback = payload.feedback
    submission.status = 'graded'
    audit(db, user, 'submission.graded', 'submission', submission.id)
    student = db.query(Student).filter(Student.id == submission.student_id).first()
    if student and student.user_id:
        db.add(Notification(user_id=student.user_id, kind='grade', title='Feedback is ready', body=f'Your work for {assignment.title} has been graded.', metadata_json={'submission_id': submission.id}))
    db.commit()
    db.refresh(submission)
    return submission


@app.get(f'{settings.api_v1_prefix}/llm/status')
def llm_status(user: CurrentTeacher) -> dict[str, Any]:
    provider = build_provider(settings)
    if hasattr(provider, 'get_health_status'):
        status = provider.get_health_status()
        return {
            'provider': status.get('provider', provider.provider_name),
            'base_url': status.get('base_url', settings.ollama_base_url),
            'model': status.get('model', settings.llm_model_name),
            'reachable': status.get('reachable', False),
            'model_available': status.get('model_available', False),
            **({'error': status.get('error')} if status.get('error') else {}),
        }
    return {
        'provider': provider.provider_name,
        'base_url': settings.ollama_base_url,
        'model': settings.llm_model_name,
        'reachable': False,
        'model_available': False,
        'error': 'No external LLM provider is configured; development fallback is active.',
    }


@app.get(f'{settings.api_v1_prefix}/interventions')
def list_interventions(user: CurrentTeacher, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    items = db.query(InterventionDraft).order_by(InterventionDraft.created_at.desc()).all()
    return [{'id': item.id, 'student_id': item.student_id, 'title': item.title, 'body': item.body, 'status': item.status, 'provider': item.evidence.get('provider'), 'evidence': item.evidence} for item in items]


@app.post(f'{settings.api_v1_prefix}/interventions/generate', status_code=201)
def generate_intervention(payload: InterventionGenerateRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    student = db.query(Student).filter(Student.id == payload.student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail='Student not found')
    features = student_features(student, db)
    try:
        prediction = predict_with_active_model(features, db)
        explanation = explain_with_active_model(features, db, target_class=prediction['risk_level'])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail='Train the risk model before generating evidence-based drafts.') from exc
    momentum_rows = db.query(AcademicMomentum).filter(AcademicMomentum.student_id == student.id).order_by(AcademicMomentum.observed_at).all()
    momentum_values: dict[str, list[float]] = {}
    for row in momentum_rows:
        momentum_values.setdefault(row.metric, []).append(row.value)
    momentum_status = {name: calculate_momentum(values)['classification'] for name, values in momentum_values.items()}
    retrieval = retrieve(f'{student.department or ""} academic support mentoring attendance assessment')
    resources = [{'title': item['source'], 'text': item['text']} for item in retrieval['sources']]
    shap_contributions = explanation.get('contributions', {})
    shap_values = explanation.get('feature_values', {})
    shap_evidence = [
        {
            'feature': name,
            'value': shap_values.get(name),
            'model_contribution': float(value),
        }
        for name, value in shap_contributions.items()
        if isinstance(value, (int, float))
    ]
    context = InterventionContext(
        student_name=student.full_name,
        risk_level=prediction['risk_level'],
        risk_probability=prediction['risk_probability'],
        class_probabilities=prediction['class_probabilities'],
        model_version=prediction['model_version'],
        shap_factors=explanation['summary'],
        shap_evidence=shap_evidence,
        momentum=momentum_status,
        attendance=student.attendance,
        assessment=student.assessment,
        assignment_engagement=student.assignments_completed,
        institutional_resources=resources,
        subject_academic_context={
            code: {key: value for key, value in record.items() if key not in {'name', 'import_batch_id'} and value is not None}
            for code, record in student_subject_records(student, db)['subjects'].items()
            if any(value is not None for key, value in record.items() if key not in {'name', 'import_batch_id'})
        },
        source_notice=retrieval['message'] or 'Use only the cited institutional resources.',
    )
    provider = build_provider(settings)
    try:
        body = provider.generate_intervention(context)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    validation_errors = validate_intervention_draft(body, context)
    if validation_errors:
        raise HTTPException(
            status_code=422,
            detail={
                'code': 'INTERVENTION_EVIDENCE_VALIDATION_FAILED',
                'message': 'The generated draft did not pass evidence validation. Regenerate or create a manual faculty draft.',
                'issues': validation_errors,
            },
        )
    student.risk_level = prediction['risk_level']
    student.risk_probability = prediction['risk_probability']
    db.add(RiskPrediction(student_id=student.id, risk_level=prediction['risk_level'], probability=prediction['risk_probability'], model_version=prediction['model_version'], prediction_timestamp=prediction['prediction_timestamp'], input_features=features))
    db.add(RiskSnapshot(student_id=student.id, risk_level=prediction['risk_level'], probability=prediction['risk_probability'], recorded_at=prediction['prediction_timestamp'].replace(tzinfo=None)))
    draft = InterventionDraft(student_id=student.id, title='Support check-in', body=body, status='draft', evidence={
        'risk_prediction': {
            **prediction,
            'prediction_timestamp': prediction['prediction_timestamp'].isoformat(),
        },
        'shap': explanation,
        'momentum': momentum_status,
        'rag': retrieval,
        'provider': provider.provider_name,
    })
    db.add(draft)
    audit(db, user, 'intervention.draft_generated', 'intervention_draft')
    db.commit()
    db.refresh(draft)
    return {'id': draft.id, 'student_id': draft.student_id, 'title': draft.title, 'body': draft.body, 'status': draft.status, 'provider': provider.provider_name, 'evidence': draft.evidence}


@app.patch(f'{settings.api_v1_prefix}/interventions/{{draft_id}}')
def edit_intervention(draft_id: int, payload: InterventionEditRequest, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, Any]:
    draft = db.query(InterventionDraft).filter(InterventionDraft.id == draft_id, InterventionDraft.status == 'draft').first()
    if draft is None:
        raise HTTPException(status_code=404, detail='Draft not found')
    draft.title = payload.title
    disclaimer = 'AI-GENERATED DRAFT - FACULTY REVIEW REQUIRED'
    draft.body = payload.body if payload.body.startswith(disclaimer) else f'{disclaimer}\n{payload.body}'
    audit(db, user, 'intervention.draft_edited', 'intervention_draft', draft.id)
    db.commit()
    return {'id': draft.id, 'title': draft.title, 'body': draft.body, 'status': draft.status}


@app.post(f'{settings.api_v1_prefix}/interventions/{{draft_id}}/approve')
def approve_intervention(draft_id: int, user: CurrentTeacher, db: Session = Depends(get_db)) -> dict[str, str]:
    draft = db.query(InterventionDraft).filter(InterventionDraft.id == draft_id, InterventionDraft.status == 'draft').first()
    if draft is None:
        raise HTTPException(status_code=404, detail='Draft not found')
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if teacher is None:
        teacher = Teacher(user_id=user.id)
        db.add(teacher)
        db.flush()
    intervention = Intervention(student_id=draft.student_id, teacher_id=teacher.id if teacher else None, title=draft.title, body=draft.body, status='sent', sent_at=datetime.utcnow())
    db.add(intervention)
    db.add(InterventionReview(draft_id=draft.id, teacher_id=teacher.id, decision='approved'))
    draft.status = 'approved'
    student = db.query(Student).filter(Student.id == draft.student_id).first()
    if student and student.user_id:
        db.add(Notification(user_id=student.user_id, kind='support', title=draft.title, body=draft.body, metadata_json={'intervention_id': intervention.id}))
    audit(db, user, 'intervention.approved', 'intervention', draft.id)
    db.commit()
    return {'status': 'approved'}


@app.get(f'{settings.api_v1_prefix}/knowledge/search')
def knowledge_search(query: str, user: CurrentUser, top_k: int = 4) -> dict[str, Any]:
    return retrieve(query, min(max(top_k, 1), 10))


@app.get(f'{settings.api_v1_prefix}/reports/export.csv')
def export_report(user: CurrentTeacher, db: Session = Depends(get_db), report_type: str = 'risk', academic_period: str | None = None) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    students = db.query(Student).order_by(Student.student_id).all()
    features_by_student: dict[int, dict[str, float]] = {}
    momentum_by_student: dict[int, dict[str, dict[str, Any]]] = {}
    current_predictions: dict[int, dict[str, Any]] = {}
    current_risk_data: dict[int, dict[str, Any]] = {}
    if report_type in {'risk', 'class'}:
        features_by_student, momentum_by_student = student_feature_batch(students, db)
        risk_views = student_risk_views_from_features(students, features_by_student, db)
        current_predictions = {
            student.id: prediction
            for student, (_, prediction) in zip(students, risk_views)
        }
        current_risk_data = {
            student.id: values
            for student, (values, _) in zip(students, risk_views)
        }
    if report_type == 'risk':
        writer.writerow(['student_id', 'name', 'department', 'data_origin', 'risk_status', 'risk_level', 'risk_class_probability', 'attendance', 'assessment', 'current_gpa', 'momentum_signal'])
        for student in students:
            prediction = current_predictions[student.id]
            risk_data = current_risk_data[student.id]
            features = features_by_student[student.id]
            momentum = momentum_by_student[student.id].get('assessment', calculate_momentum([]))
            writer.writerow([student.student_id, student.full_name, student.department, student.data_origin, risk_data['risk_status'], prediction['risk_level'], prediction['risk_probability'], features['attendance'], student.assessment, student.current_gpa, momentum['classification']])
    elif report_type == 'class':
        writer.writerow(['metric', 'count'])
        writer.writerow(['students', len(students)])
        for level in ('HIGH', 'MEDIUM', 'LOW'):
            writer.writerow([f'risk_{level.lower()}', sum(prediction['risk_level'] == level for prediction in current_predictions.values())])
        writer.writerow(['unscored_students', sum(
            values['risk_status'] not in {'available', 'unvalidated-demo-model'}
            for values in current_risk_data.values()
        )])
    elif report_type == 'assignments':
        writer.writerow(['assignment_id', 'title', 'course', 'status', 'assigned', 'submitted', 'pending', 'late', 'graded', 'due_date'])
        for assignment in db.query(Assignment).order_by(Assignment.id).all():
            counts = {'assigned': db.query(AssignmentStudent.id).filter(AssignmentStudent.assignment_id == assignment.id).count()}
            counts['submitted'] = db.query(Submission.id).filter(Submission.assignment_id == assignment.id, Submission.status.in_(['submitted', 'late', 'graded'])).count()
            counts['late'] = db.query(Submission.id).filter(Submission.assignment_id == assignment.id, Submission.status == 'late').count()
            counts['graded'] = db.query(Submission.id).filter(Submission.assignment_id == assignment.id, Submission.status == 'graded').count()
            writer.writerow([assignment.id, assignment.title, assignment.course, assignment.status, counts['assigned'], counts['submitted'], max(0, counts['assigned'] - counts['submitted']), counts['late'], counts['graded'], assignment.due_date.isoformat() if assignment.due_date else ''])
    elif report_type == 'internal_marks':
        try:
            period = selected_academic_period(db, academic_period)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        records = subject_records_by_student(db, [student.id for student in students], period)
        headers = ['Academic Period', 'USN', 'Student Name', 'Department', 'Section']
        for code in SUBJECTS:
            headers.extend([
                f'{code} FA1 raw',
                f'{code} FA1 maximum marks',
                f'{code} FA1 maximum verified',
                f'{code} FA2 /25',
                f'{code} Assignment /25',
                f'{code} Internal /50',
                f'{code} Attendance %',
            ])
        headers.extend(['Total Internal /250', 'Average Internal /50'])
        writer.writerow(headers)
        for student in students:
            row_records = records.get(student.id, {})
            values: list[Any] = [period or '', student.student_id, student.full_name, student.department or '', student.section or '']
            for code in SUBJECTS:
                record = row_records.get(code)
                academic = serialize_record(record)
                values.extend([
                    academic['fa1'] if academic['fa1'] is not None else '',
                    academic['fa1_maximum_marks'] if academic['fa1_maximum_marks'] is not None else '',
                    academic['fa1_maximum_verified'],
                    academic['fa2'] if academic['fa2'] is not None else '',
                    academic['assignment_mark'] if academic['assignment_mark'] is not None else '',
                    academic['internal_mark'] if academic['internal_mark'] is not None else '',
                    academic['attendance_rate'] if academic['attendance_rate'] is not None else '',
                ])
            totals = academic_totals(row_records)
            values.extend([totals['total_internal'] if totals['total_internal'] is not None else '', totals['average_internal'] if totals['average_internal'] is not None else ''])
            writer.writerow(values)
    elif report_type == 'interventions':
        writer.writerow(['student_id', 'student_name', 'title', 'status', 'created_at', 'body'])
        for draft in db.query(InterventionDraft).order_by(InterventionDraft.created_at.desc()).all():
            student = db.query(Student).filter(Student.id == draft.student_id).first()
            writer.writerow([student.student_id if student else '', student.full_name if student else '', draft.title, draft.status, draft.created_at.isoformat(), draft.body])
    else:
        raise HTTPException(status_code=422, detail='Unsupported report type')
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="academic-{report_type}-report.csv"'})


@app.get(f'{settings.api_v1_prefix}/notifications')
def notifications(user: CurrentUser, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    items = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(100).all()
    return [{'id': item.id, 'kind': item.kind, 'title': item.title, 'body': item.body, 'created_at': item.created_at.isoformat(), 'read': item.read_at is not None} for item in items]


@app.patch(f'{settings.api_v1_prefix}/notifications/{{notification_id}}/read')
def mark_notification_read(notification_id: int, user: CurrentUser, db: Session = Depends(get_db)) -> dict[str, str]:
    item = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
    if item is None:
        raise HTTPException(status_code=404, detail='Notification not found')
    item.read_at = datetime.utcnow()
    db.commit()
    return {'status': 'read'}