import hashlib
import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.app.models import (
    Assessment,
    Attendance,
    Course,
    Mark,
    RecoveryAdaptationDecision,
    RecoveryTask,
    RecoveryTaskEvent,
    RiskPrediction,
    Student,
    StudentSubjectAcademicRecord,
    Subject,
    SupportPlanVersion,
    User,
)
from backend.app.schemas.recovery import (
    RecoveryBlueprintAIResponse,
    RecoveryTaskDraft,
    RecoveryTutorModelResponse,
)

PLAN_DRAFT = 'DRAFT'
PLAN_APPROVED = 'APPROVED'
PLAN_PUBLISHED = 'PUBLISHED'
PLAN_REJECTED = 'REJECTED'
TASK_AVAILABLE = 'AVAILABLE'
TASK_DRAFT = 'DRAFT'
TASK_IN_PROGRESS = 'IN_PROGRESS'
TASK_SUBMITTED = 'SUBMITTED'
TASK_NEEDS_REVISION = 'NEEDS_REVISION'
TASK_COMPLETED = 'COMPLETED'
TASK_SUPERSEDED = 'SUPERSEDED'
_TUTOR_NUMBER = re.compile(r'(?<![\w.])\d+(?:\.\d+)?%?')


def _normalized_tutor_numbers(value: str) -> set[str]:
    return {
        format(Decimal(token.rstrip('%')).normalize(), 'f')
        for token in _TUTOR_NUMBER.findall(value)
    }


def _verified_tutor_numbers(evidence_records: list[dict[str, Any]]) -> set[str]:
    values: list[str] = []
    for record in evidence_records:
        for field in ('score', 'maximum_marks', 'attendance_rate'):
            value = record.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(str(value))
        for measurement in record.get('values', {}).values():
            if isinstance(measurement, dict):
                value = measurement.get('value')
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(str(value))
    return set().union(*(_normalized_tutor_numbers(value) for value in values)) if values else set()


def validate_recovery_tutor_response(
    response: RecoveryTutorModelResponse,
    *,
    evidence_records: list[dict[str, Any]],
) -> None:
    if not response.supported:
        if response.answer or response.practice_suggestions or response.evidence_refs:
            raise ValueError('Unsupported tutor responses must not include an answer or claims.')
        return
    allowed_refs = {record.get('ref') for record in evidence_records}
    if any(ref not in allowed_refs for ref in response.evidence_refs):
        raise ValueError('The tutor response cited academic evidence outside the authorized evidence set.')
    response_text = ' '.join([response.answer, *response.practice_suggestions])
    if response.answer_scope == 'GENERAL_EXPLANATION':
        if not response.answer or response.evidence_refs:
            raise ValueError('General explanations require an answer and cannot cite student evidence.')
        if re.search(
            r'\b(?:your|you)\s+(?:record|mark|grade|score|attendance|progress|risk)\b'
            r'|\bthe student(?:\'s)?\b',
            response_text,
            re.IGNORECASE,
        ):
            raise ValueError('General explanations must not make personalized student claims.')
    else:
        if not response.answer or not response.evidence_refs:
            raise ValueError('A student-evidence response must include an answer and cited academic evidence.')
        allowed_numbers = _verified_tutor_numbers(evidence_records)
        unsupported_numbers = _normalized_tutor_numbers(response_text) - allowed_numbers
        if unsupported_numbers:
            raise ValueError('The tutor response included numeric claims not present in verified academic evidence.')
        if re.search(
            r'\b(?:has|have|is|are|fully|already)\s+mastered\b'
            r'|\b(?:demonstrates?|confirms?|shows?|indicates?)\s+(?:topic[- ]level\s+)?mastery\b'
            r'|\bmastery\s+(?:is|was)\s+(?:confirmed|demonstrated|shown)\b',
            response_text,
            re.IGNORECASE,
        ):
            raise ValueError('Topic mastery cannot be inferred from the available aggregate academic evidence.')


def validate_recovery_blueprint_response(raw: str, topic_ids: set[int]) -> list[dict[str, Any]]:
    response = RecoveryBlueprintAIResponse.model_validate_json(raw)
    response_ids = [item.topic_id for item in response.topics]
    if len(response_ids) != len(set(response_ids)) or set(response_ids) != topic_ids:
        raise ValueError('Blueprint recommendations must cover each supplied approved topic exactly once.')
    return [item.model_dump(mode='json') for item in response.topics]


def build_recovery_blueprint_schedule(
    recommendations: list[dict[str, Any]],
    topic_subjects: dict[int, int],
    *,
    start_date: date,
    exam_date: date,
    minutes_per_day: int,
    topic_module_numbers: dict[int, int] | None = None,
    eligible_subject_ids: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    schedule: list[dict[str, Any]] = []
    blockers: list[str] = []
    study_end = exam_date - timedelta(days=4) if (exam_date - start_date).days >= 4 else exam_date
    current_date = start_date
    used_minutes = 0
    priority_rank = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    stage_rank = {'MODULE_3': 0, 'MODULE_4': 1}

    def topic_stage(topic_id: int) -> str:
        module_number = (topic_module_numbers or {}).get(topic_id, 3)
        if module_number not in {3, 4}:
            raise ValueError(f'Topic {topic_id} is outside the supported Module 3/4 recovery stages.')
        return f'MODULE_{module_number}'

    ordered = [
        item
        for _, item in sorted(
            enumerate(recommendations),
            key=lambda pair: (
                stage_rank[topic_stage(pair[1]['topic_id'])],
                priority_rank[pair[1]['priority']],
                pair[0],
            ),
        )
    ]

    for stage in ('MODULE_3', 'MODULE_4'):
        stage_last_date = None
        for recommendation in ordered:
            topic_id = recommendation['topic_id']
            if topic_stage(topic_id) != stage:
                continue
            remaining = recommendation['suggested_minutes']
            while remaining > 0 and current_date <= study_end:
                if used_minutes >= minutes_per_day:
                    current_date += timedelta(days=1)
                    used_minutes = 0
                    continue
                duration = min(remaining, minutes_per_day - used_minutes)
                schedule.append({
                    'scheduled_date': current_date.isoformat(),
                    'activity': 'TOPIC_STUDY',
                    'stage': stage,
                    'topic_id': topic_id,
                    'subject_id': topic_subjects[topic_id],
                    'estimated_minutes': duration,
                    'provenance': 'AI_RECOMMENDATION_TEACHER_REVIEW_REQUIRED',
                })
                stage_last_date = current_date
                remaining -= duration
                used_minutes += duration
            if remaining > 0:
                blockers.append(
                    f'Topic {topic_id} needs {remaining} more recommended study minutes that do not fit before the exam date.'
                )
        if stage == 'MODULE_3' and stage_last_date is not None:
            current_date = stage_last_date + timedelta(days=1)
            used_minutes = 0

    if (exam_date - start_date).days >= 4:
        subject_ids = sorted(set(topic_subjects.values()) | (eligible_subject_ids or set()))
        minutes_per_checkpoint = max(
            1,
            min(30, minutes_per_day // max(1, len(subject_ids) * 3)),
        )
        checkpoint_start = max(current_date, exam_date - timedelta(days=3))
        checkpoint_date = checkpoint_start
        checkpoint_used_minutes = 0
        for activity in ('REVISION', 'MOCK_PRACTICE', 'RETENTION_CHECK'):
            for subject_id in subject_ids:
                if checkpoint_used_minutes + minutes_per_checkpoint > minutes_per_day:
                    checkpoint_date += timedelta(days=1)
                    checkpoint_used_minutes = 0
                if checkpoint_date >= exam_date:
                    blockers.append(
                        f'{activity.replace("_", " ").title()} for subject {subject_id} does not fit before the FA-2 exam date.'
                    )
                    continue
                schedule.append({
                    'scheduled_date': checkpoint_date.isoformat(),
                    'activity': activity,
                    'stage': 'REVISION_ASSESSMENT',
                    'topic_id': None,
                    'subject_id': subject_id,
                    'estimated_minutes': minutes_per_checkpoint,
                    'provenance': 'TEACHER_REVIEW_REQUIRED',
                })
                checkpoint_used_minutes += minutes_per_checkpoint
    elif topic_subjects:
        blockers.append(
            'The exam date is too close to fit separate revision, mock-practice, and retention-check days.'
        )
    return schedule, blockers


def build_recovery_evidence(db: Session, student: Student) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    by_subject: dict[str, list[str]] = {}
    subject_names: dict[str, str] = {}
    subject_ids: dict[str, int | None] = {}

    subject_rows = (
        db.query(StudentSubjectAcademicRecord, Subject)
        .join(Subject, Subject.id == StudentSubjectAcademicRecord.subject_id)
        .filter(StudentSubjectAcademicRecord.student_id == student.id)
        .order_by(StudentSubjectAcademicRecord.academic_period, Subject.name)
        .all()
    )
    for record, subject in subject_rows:
        values: dict[str, Any] = {}
        observed_fields = {
            'fa1': 'fa1_observed_at',
            'fa2': 'fa2_observed_at',
            'assignment_mark': 'assignment_observed_at',
            'attendance_rate': 'attendance_observed_at',
        }
        for field in ('fa1', 'fa2', 'assignment_mark', 'attendance_rate'):
            value = getattr(record, field)
            observed_at = getattr(record, observed_fields[field])
            if value is not None:
                values[field] = {
                    'value': float(value),
                    'observed_at': observed_at.isoformat() if observed_at else None,
                }
        if not values:
            continue
        ref = f'academic_record:{record.id}'
        group_key = f'subject:{subject.id}'
        subject_names[group_key] = subject.name
        subject_ids[group_key] = subject.id
        records.append({
            'ref': ref,
            'kind': 'student_subject_academic_record',
            'subject': subject.name,
            'subject_id': subject.id,
            'academic_period': record.academic_period,
            'values': values,
        })
        by_subject.setdefault(group_key, []).append(ref)

    mark_rows = (
        db.query(Mark, Assessment, Course, Subject)
        .join(Assessment, Assessment.id == Mark.assessment_id)
        .join(Course, Course.id == Assessment.course_id)
        .outerjoin(Subject, Subject.id == Course.subject_id)
        .filter(Mark.student_id == student.id)
        .order_by(Mark.recorded_at, Mark.id)
        .all()
    )
    for mark, assessment, course, subject in mark_rows:
        subject_name = subject.name if subject else course.name
        group_key = f'subject:{subject.id}' if subject else f'course:{course.id}'
        subject_names[group_key] = subject_name
        subject_ids[group_key] = subject.id if subject else None
        ref = f'mark:{mark.id}'
        records.append({
            'ref': ref,
            'kind': 'assessment_mark',
            'subject': subject_name,
            'subject_id': subject.id if subject else None,
            'assessment': assessment.title,
            'score': float(mark.score),
            'maximum_marks': float(assessment.maximum_marks),
            'recorded_at': mark.recorded_at.isoformat(),
        })
        by_subject.setdefault(group_key, []).append(ref)

    attendance_rows = (
        db.query(Attendance, Course, Subject)
        .outerjoin(Course, Course.id == Attendance.course_id)
        .outerjoin(Subject, Subject.id == Course.subject_id)
        .filter(Attendance.student_id == student.id)
        .order_by(Attendance.observed_at, Attendance.id)
        .all()
    )
    for attendance, course, subject in attendance_rows:
        subject_name = subject.name if subject else (course.name if course else 'Unspecified course')
        group_key = (
            f'subject:{subject.id}' if subject else
            f'course:{course.id}' if course else
            'unscoped:attendance'
        )
        subject_names[group_key] = subject_name
        subject_ids[group_key] = subject.id if subject else None
        ref = f'attendance:{attendance.id}'
        records.append({
            'ref': ref,
            'kind': 'attendance_record',
            'subject': subject_name,
            'subject_id': subject.id if subject else None,
            'attended': bool(attendance.attended),
            'attendance_rate': float(attendance.attendance_rate) if attendance.attendance_rate is not None else None,
            'observed_at': attendance.observed_at.isoformat(),
        })
        by_subject.setdefault(group_key, []).append(ref)

    prediction = (
        db.query(RiskPrediction)
        .filter_by(student_id=student.id)
        .order_by(RiskPrediction.prediction_timestamp.desc())
        .first()
    )
    model_output = None
    if prediction is not None:
        model_output = {
            'source': 'risk_predictions',
            'record_id': prediction.id,
            'risk_level': prediction.risk_level,
            'probability': float(prediction.probability),
            'model_version': prediction.model_version,
            'prediction_timestamp': prediction.prediction_timestamp.isoformat(),
        }

    missing_information = [
        'Topic-level assessment evidence is not available; aggregate records do not establish topic mastery.',
        'No student schedule or deadline was provided.',
        'No verified learning resources or learning preferences were provided.',
    ]
    if not records:
        missing_information.insert(0, 'No verified academic assessment, subject-record, or attendance evidence is available.')
    return {
        'academic_records': records,
        'model_outputs': {'risk_prediction': model_output},
        'missing_information': missing_information,
        'provenance': {
            'academic_records': 'Read-only records from the authorized student academic database.',
            'model_outputs': 'Persisted model output, when available; not treated as topic-level evidence.',
        },
        '_by_subject': by_subject,
        '_subject_names': subject_names,
        '_subject_ids': subject_ids,
    }


def _evidence_tasks(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    grouped = evidence.get('_by_subject', {})
    subject_names = evidence.get('_subject_names', {})
    subject_ids = evidence.get('_subject_ids', {})
    records_by_ref = {
        record['ref']: record
        for record in evidence.get('academic_records', [])
        if isinstance(record, dict) and record.get('ref')
    }
    tasks: list[dict[str, Any]] = []
    for group_key, refs in sorted(grouped.items()):
        if not refs:
            continue
        subject = subject_names.get(group_key, group_key.removeprefix('subject:'))
        subject_id = subject_ids.get(group_key)
        evidence_records = [records_by_ref[ref] for ref in refs if ref in records_by_ref]
        task_key = hashlib.sha256('|'.join(sorted(refs)).encode('utf-8')).hexdigest()[:32]
        evidence_labels = [
            f"{record.get('assessment')} ({record.get('score')} / {record.get('maximum_marks')})"
            for record in evidence_records
            if record.get('kind') == 'assessment_mark'
        ]
        record_context = (
            f" The linked assessment records include {', '.join(evidence_labels)}."
            if evidence_labels else
            ' The linked records are academic-record or attendance observations, not evidence of a specific topic gap.'
        )
        activity = {
            'reason': (
                f'This teacher-guided activity reviews verified records associated with {subject}.'
                f'{record_context} These records do not identify a specific missed topic or establish mastery.'
            ),
            'lesson': (
                f'This is a general evidence-reading skill for studying {subject}, not a verified lesson from the course. '
                'For any academic record, separate three things: Observation (what the record directly says), Limit '
                '(what it cannot establish by itself), and Question (what additional feedback or original work would '
                'help explain it). A recorded result or attendance observation alone cannot identify which topic needs practice.'
            ),
            'steps': [
                'Observation: open a linked record and write exactly what it reports, including its label and recorded value.',
                'Limit: write one thing this record cannot tell you without the original work or course context.',
                'Question: turn that limit into a specific question for your teacher.',
                'If original work is available, ask your teacher to select one item for follow-up; otherwise submit your three-part evidence note.',
            ],
            'exercise': (
                f'Complete a three-part evidence note for the linked {subject} record: (1) Observation — quote or '
                'describe one fact shown, (2) Limit — name one conclusion the record cannot support, and (3) Question — '
                'ask what additional feedback would clarify. If your teacher provides original work, add one teacher-selected '
                'question and explain your reasoning. This practices careful interpretation; it does not diagnose a subject weakness.'
            ),
            'submission_format': (
                'Submit the evidence reference and three labeled lines — Observation, Limit, and Question — plus any '
                'teacher-selected follow-up and the next step agreed with your teacher. Do not claim topic mastery or '
                'weakness based only on an aggregate result.'
            ),
        }
        tasks.append({
            'task_key': task_key,
            'subject': subject,
            'subject_id': subject_id,
            'subject_scope_review_required': subject_id is None,
            'topic': None,
            'learning_objective': (
                f'Interpret the verified {subject} record with a teacher and identify a question-level follow-up '
                'only if the original work or course context supports one.'
            ),
            'title': f'Teacher-guided evidence activity: {subject}',
            'instructions': (
                'Use the lesson, steps, and exercise below. This activity is a structured evidence review, not a claim '
                'that a subject topic is weak. Your teacher must select any topic-specific follow-up from original '
                'work or verified course material.'
            ),
            'activity': activity,
            'activity_type': 'TEACHER_REVIEW',
            'priority': 'MEDIUM',
            'source': 'EVIDENCE_REVIEW',
            'scheduled_at': None,
            'deadline': None,
            'estimated_minutes': 20,
            'resources': [],
            'completion_criteria': 'Record the discussion and any evidence-based next step with the teacher.',
            'mastery_criteria': None,
            'status': TASK_AVAILABLE,
            'progress_percent': 0,
            'evidence_refs': sorted(refs),
            'field_provenance': {
                'subject': 'VERIFIED_ACADEMIC_RECORD',
                'evidence_refs': 'VERIFIED_ACADEMIC_RECORD',
                'topic': 'UNKNOWN_NOT_INFERRED',
                'activity_type': 'DETERMINISTIC_REVIEW_RULE',
                'priority': 'PROPOSED_DEFAULT_REQUIRES_TEACHER_REVIEW',
                'scheduled_at': 'UNKNOWN_UNSCHEDULED',
                'deadline': 'UNKNOWN_UNSCHEDULED',
                'estimated_minutes': 'PROPOSED_DEFAULT_REQUIRES_TEACHER_REVIEW',
                'resources': 'NO_VERIFIED_RESOURCES',
                'completion_criteria': 'PROPOSED_REQUIRES_TEACHER_REVIEW',
                'mastery_criteria': 'NOT_INFERRED_FROM_AGGREGATE_MARKS',
                'activity': 'DETERMINISTIC_EVIDENCE_REVIEW',
            },
        })
    return tasks


def legacy_recovery_activity(task: RecoveryTask) -> dict[str, Any]:
    subject = task.subject or 'this course'
    return {
        'reason': (
            'This task was published before structured lesson and exercise details were available. '
            'Its original evidence does not identify a topic-specific learning gap; the activity needs teacher review.'
        ),
        'lesson': (
            f'No verified topic-level lesson content was attached to this {subject} task. '
            'Do not infer a weakness or mastery from an aggregate mark. Ask your teacher to review the linked evidence '
            'and provide an appropriate course-specific explanation.'
        ),
        'steps': [
            'Review the linked evidence displayed with this task.',
            'Ask your teacher to identify a specific question or course objective from verified work or course material.',
            'Record the evidence-based discussion and any teacher-approved next step.',
        ],
        'exercise': (
            'Write what the linked record directly shows and one question for your teacher. '
            'Complete a topic-specific exercise only after your teacher supplies or approves it.'
        ),
        'submission_format': (
            'Submit a short written evidence-review reflection: identify the linked record, state what it directly shows, '
            'and record your question and the teacher-agreed next step.'
        ),
    }


def make_draft_plan(
    evidence: dict[str, Any],
    *,
    evidence_selector: Callable[[dict[str, Any]], list[str]] | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    clean_evidence = {key: value for key, value in evidence.items() if not key.startswith('_')}
    selected_evidence = evidence
    llm_status = 'not_used_no_structured_evidence_constrained_recovery_provider'
    fallback_reason = 'No structured recovery evidence selector is configured.'
    if evidence_selector is not None:
        known_refs = {
            record['ref']
            for record in evidence.get('academic_records', [])
        }
        try:
            selected_refs = set(evidence_selector(clean_evidence))
            if not selected_refs.issubset(known_refs):
                raise ValueError('Recovery evidence selector returned an unknown evidence reference.')
            selected_evidence = {
                **evidence,
                'academic_records': [
                    record for record in evidence.get('academic_records', [])
                    if record['ref'] in selected_refs
                ],
                '_by_subject': {
                    subject: [ref for ref in refs if ref in selected_refs]
                    for subject, refs in evidence.get('_by_subject', {}).items()
                },
            }
            llm_status = 'used_only_to_select_verified_evidence_references'
            fallback_reason = ''
        except (RuntimeError, TimeoutError, OSError, ValueError):
            llm_status = 'evidence_selector_failed_deterministic_fallback'
            fallback_reason = 'Recovery evidence selection failed or returned unsupported references.'
    tasks = _evidence_tasks(selected_evidence)
    if tasks:
        summary = f'{len(tasks)} evidence-linked review task(s) are ready for teacher review. No topic mastery is inferred.'
    else:
        summary = 'No task can be generated from the available verified academic evidence. A teacher can add an evidence-backed task before approval.'
    provenance = {
        'generator': 'deterministic_evidence_rules',
        'llm_status': llm_status,
        'rule_version': 'recovery-evidence-v1',
        'fallback': fallback_reason or 'Task wording and details are deterministic and evidence-grounded.',
    }
    return summary, tasks, {**clean_evidence, 'generation': provenance}


def stable_generation_key(student_id: int, evidence: dict[str, Any], requested_key: str | None) -> str:
    if requested_key:
        return requested_key
    serialized = json.dumps(evidence, sort_keys=True, separators=(',', ':'), default=str)
    digest = hashlib.sha256(f'{student_id}:{serialized}'.encode('utf-8')).hexdigest()
    return f'evidence-{digest}'


def create_draft(
    db: Session,
    *,
    student_id: int,
    teacher_id: int,
    evidence: dict[str, Any],
    generation_key: str,
) -> tuple[SupportPlanVersion, bool]:
    existing = db.query(SupportPlanVersion).filter_by(student_id=student_id, generation_key=generation_key).first()
    if existing is not None:
        return existing, False
    current = (
        db.query(SupportPlanVersion)
        .filter_by(student_id=student_id)
        .order_by(SupportPlanVersion.version.desc())
        .first()
    )
    summary, tasks, evidence_json = make_draft_plan(evidence)
    is_subject_group = bool(tasks) and any(task.get('subject_id') for task in tasks)
    plan = SupportPlanVersion(
        student_id=student_id,
        teacher_id=teacher_id,
        version=(current.version if current else 0) + 1,
        summary=summary,
        plan_json={'tasks': tasks},
        status=PLAN_DRAFT,
        evidence_json=evidence_json,
        generation_key=generation_key,
        subject_scope_review_required=not is_subject_group,
        blueprint_group_id=str(uuid4()) if is_subject_group else None,
        is_blueprint_group=is_subject_group,
    )
    db.add(plan)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.query(SupportPlanVersion).filter_by(student_id=student_id, generation_key=generation_key).first()
        if existing is None:
            raise
        return existing, False
    return plan, True


def edit_draft(plan: SupportPlanVersion, *, summary: str | None, tasks: list[RecoveryTaskDraft] | None) -> None:
    if plan.status != PLAN_DRAFT:
        raise ValueError('Only draft recovery plans can be edited.')
    if summary is not None:
        plan.summary = summary
    if tasks is not None:
        current = plan.plan_json.get('tasks', [])
        refs = sorted({
            ref
            for task in current
            for ref in task.get('evidence_refs', [])
        })
        supplied_refs = {
            ref
            for task in tasks
            for ref in task.evidence_refs
        }
        if not supplied_refs.issubset(set(refs)):
            raise ValueError('Task evidence references must come from this recovery plan.')
        plan.plan_json = {
            **plan.plan_json,
            'tasks': [
                {
                    **task.model_dump(mode='json'),
                    'task_key': hashlib.sha256(
                        f'{plan.id}:{index}:{task.title}'.encode('utf-8')
                    ).hexdigest()[:32],
                    'status': TASK_AVAILABLE,
                    'source': 'TEACHER_AUTHORED',
                    'field_provenance': {
                        'subject_id': 'TEACHER_CONFIRMED_SUBJECT' if task.subject_id else 'NEEDS_SUBJECT_SCOPE_REVIEW',
                        'content': 'TEACHER_AUTHORED',
                        'topic': 'TEACHER_PROVIDED' if task.topic else 'UNKNOWN',
                        'priority': 'TEACHER_PROVIDED',
                        'activity_type': 'TEACHER_PROVIDED',
                        'schedule': 'TEACHER_PROVIDED' if task.scheduled_at else 'UNKNOWN_UNSCHEDULED',
                        'deadline': 'TEACHER_PROVIDED' if task.deadline else 'UNKNOWN_UNSCHEDULED',
                        'resources': 'TEACHER_PROVIDED' if task.resources else 'NO_VERIFIED_RESOURCES',
                        'estimated_minutes': 'TEACHER_PROVIDED',
                        'completion_criteria': 'TEACHER_PROVIDED',
                        'mastery_criteria': 'TEACHER_PROVIDED_REQUIRES_REVIEW' if task.mastery_criteria else 'UNKNOWN',
                        'evidence_refs': 'VERIFIED_ACADEMIC_RECORD' if task.evidence_refs else 'TEACHER_AUTHORED_NO_EVIDENCE_LINK',
                    },
                    'evidence_refs': task.evidence_refs,
                }
                for index, task in enumerate(tasks)
            ]
        }


def add_task_event(
    db: Session,
    *,
    task: RecoveryTask,
    actor_user_id: int,
    event_type: str,
    detail: dict[str, Any],
    idempotency_key: str,
) -> tuple[RecoveryTaskEvent, bool]:
    existing = db.query(RecoveryTaskEvent).filter_by(idempotency_key=idempotency_key).first()
    if existing is not None:
        same_event = (
            existing.task_id == task.id
            and existing.subject_id == task.subject_id
            and existing.actor_user_id == actor_user_id
            and existing.event_type == event_type
            and existing.detail == detail
        )
        if not same_event:
            raise ValueError('The idempotency key was already used for a different event.')
        return existing, False
    event = RecoveryTaskEvent(
        task_id=task.id,
        student_id=task.student_id,
        subject_id=task.subject_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        detail=detail,
        idempotency_key=idempotency_key,
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:
        existing = db.query(RecoveryTaskEvent).filter_by(idempotency_key=idempotency_key).first()
        if existing is None:
            raise
        same_event = (
            existing.task_id == task.id
            and existing.subject_id == task.subject_id
            and existing.actor_user_id == actor_user_id
            and existing.event_type == event_type
            and existing.detail == detail
        )
        if not same_event:
            raise ValueError('The idempotency key was already used for a different event.')
        return existing, False
    return event, True


def _blueprint_schedule_task_definitions(plan: SupportPlanVersion) -> list[dict[str, Any]]:
    blueprint = (plan.plan_json or {}).get('blueprint')
    if not isinstance(blueprint, dict):
        raise ValueError('A subject-group roadmap requires a reviewable blueprint.')
    subjects = {
        int(item['subject_id']): item
        for item in blueprint.get('subjects', [])
        if item.get('subject_id') is not None
    }
    topics = {
        int(topic['topic_id']): (subject_id, topic)
        for subject_id, subject in subjects.items()
        for topic in subject.get('topics', [])
        if topic.get('topic_id') is not None
    }
    definitions: list[dict[str, Any]] = []
    for index, entry in enumerate(blueprint.get('schedule', [])):
        subject_id = entry.get('subject_id')
        if subject_id is None or int(subject_id) not in subjects:
            raise ValueError('Every published schedule entry must identify a blueprint subject.')
        subject_id = int(subject_id)
        activity_type = entry.get('activity')
        topic = None
        if activity_type == 'TOPIC_STUDY':
            topic_record = topics.get(int(entry.get('topic_id') or 0))
            if topic_record is None or topic_record[0] != subject_id:
                raise ValueError('A topic study entry must reference a topic in the same subject.')
            topic = topic_record[1]
            if topic.get('decision') != 'INCLUDE' or not topic.get('source_ready'):
                raise ValueError('A topic study entry must use an included, source-backed topic.')
            scoped_topics = [topic]
        else:
            scoped_topics = [
                item for item in subjects[subject_id].get('topics', [])
                if item.get('decision') == 'INCLUDE' and item.get('source_ready')
            ]
        subject_name = subjects[subject_id].get('subject_name') or f'Subject {subject_id}'
        topic_title = topic.get('title') if topic else None
        activity_name = str(activity_type or 'STUDY').replace('_', ' ').lower()
        objective = (
            (topic.get('learning_objectives') or [topic.get('summary') or topic_title])[0]
            if topic else
            f'Review the teacher-approved {subject_name} scope using the linked topics and assessments.'
        )
        source_resources: list[dict[str, str]] = []
        evidence_refs: list[str] = []
        for scoped_topic in scoped_topics:
            source = scoped_topic.get('source_material') or {}
            source_ref = source.get('ref')
            if source_ref:
                evidence_refs.append(source_ref)
            resource_id = scoped_topic.get('source_resource_id')
            module_id = scoped_topic.get('module_id')
            if resource_id and module_id and source.get('filename'):
                source_resources.append({
                    'title': str(source['filename'])[:200],
                    'url': (
                        f"/api/v1/subjects/{subject_id}/modules/{module_id}"
                        f"/resources/{resource_id}/file"
                    ),
                })
            assignment_id = scoped_topic.get('assignment_id')
            assessment_id = (scoped_topic.get('assessment') or {}).get('assessment_id')
            if assignment_id and assessment_id:
                source_resources.append({
                    'title': f"{scoped_topic.get('title', 'Topic')} assessment",
                    'url': f'/api/v1/topic-learning-assignments/{assignment_id}/assessments',
                })
        schedule_signature = json.dumps(entry, sort_keys=True, separators=(',', ':'))
        schedule_key = hashlib.sha256(f'{index}:{schedule_signature}'.encode('utf-8')).hexdigest()
        scheduled_date = date.fromisoformat(entry['scheduled_date'])
        scheduled_at = datetime.combine(scheduled_date, datetime.min.time())
        deadline = scheduled_at + timedelta(days=1) - timedelta(seconds=1)
        title_activity = str(activity_type or 'STUDY').replace('_', ' ').title()
        title = f'{subject_name}: {topic_title}' if topic_title else f'{subject_name}: {title_activity}'
        reason = (
            f'This {activity_name} activity follows the teacher-reviewed subject roadmap schedule.'
        )
        activity = {
            'reason': reason,
            'lesson': (
                f'Use only the approved {subject_name} topics and linked source material for this scheduled activity. '
                'A completed activity is not, by itself, evidence of topic mastery.'
            ),
            'steps': [
                'Open the linked approved module material or assessment.',
                'Complete the scheduled subject activity using the approved scope.',
                'Submit a brief response describing the work completed and any questions for the teacher.',
            ],
            'exercise': (
                f'Complete the {activity_name} activity for {topic_title or subject_name} using the source materials '
                'and approved assessment links attached to this task.'
            ),
            'submission_format': (
                'Submit a short written summary of the activity, identify the linked source used, and note any '
                'unresolved question for teacher follow-up.'
            ),
        }
        definitions.append({
            'task_key': f'blueprint-{index}-{schedule_key[:24]}',
            'subject_id': subject_id,
            'subject': subject_name,
            'topic': topic_title,
            'learning_objective': str(objective)[:2000],
            'title': title[:200],
            'instructions': (
                f'Complete the {activity_name} activity on {scheduled_date.isoformat()} using only the teacher-approved '
                f'{subject_name} sources linked here. Submit your work for teacher review.'
            ),
            'activity_type': str(activity_type or 'STUDY'),
            'stage': entry.get('stage'),
            'priority': topic.get('priority', 'MEDIUM') if topic else 'MEDIUM',
            'source': 'APPROVED_BLUEPRINT',
            'scheduled_at': scheduled_at.isoformat(),
            'scheduled_date': scheduled_date.isoformat(),
            'deadline': deadline.isoformat(),
            'estimated_minutes': int(entry['estimated_minutes']),
            'resources': source_resources,
            'completion_criteria': 'Submit the scheduled activity response for teacher review.',
            'mastery_criteria': None,
            'evidence_refs': sorted(set(evidence_refs)),
            'field_provenance': {
                'subject_id': 'TEACHER_CONFIRMED_BLUEPRINT_SUBJECT',
                'schedule': entry.get('provenance', 'TEACHER_REVIEWED_BLUEPRINT'),
                'stage': 'TEACHER_REVIEWED_GLOBAL_RECOVERY_STAGE',
                'topic': 'APPROVED_MODULE_TOPIC' if topic else 'SUBJECT_LEVEL_CHECKPOINT',
                'resources': 'APPROVED_SOURCE_MATERIAL',
                'activity': 'AI_RECOMMENDATION_TEACHER_REVIEWED',
                'mastery_criteria': 'NOT_INFERRED_FROM_TASK_COMPLETION',
            },
            'activity': activity,
            'source_schedule_entry_key': schedule_key,
            'source_blueprint_plan_id': plan.id,
        })
    return definitions


def _subject_group_evidence(plan: SupportPlanVersion, subject_id: int) -> dict[str, Any]:
    evidence = json.loads(json.dumps(plan.evidence_json or {}))
    evidence['academic_records'] = [
        item for item in evidence.get('academic_records', [])
        if item.get('subject_id') == subject_id
    ]
    provenance = evidence.get('provenance')
    if isinstance(provenance, dict):
        evidence['provenance'] = {
            **provenance,
            'subject_scope': 'FILTERED_TO_PERSISTED_SUBJECT_ID',
        }
    return evidence


def _publish_subject_group(
    db: Session,
    *,
    plan: SupportPlanVersion,
    definitions: list[dict[str, Any]],
    actor_user_id: int,
    now: datetime,
) -> list[RecoveryTask]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for definition in definitions:
        subject_id = definition.get('subject_id')
        if not isinstance(subject_id, int) or subject_id <= 0:
            raise ValueError('Every published task must identify a subject in the approved roadmap.')
        if plan.subject_id is not None and plan.subject_id != subject_id:
            raise ValueError('Recovery task subject does not match its parent roadmap subject.')
        grouped.setdefault(subject_id, []).append(definition)
    if not grouped:
        raise ValueError('A subject-group roadmap must contain at least one subject-scoped task.')

    group_id = plan.blueprint_group_id or str(plan.id)
    plan.blueprint_group_id = group_id
    created: list[RecoveryTask] = []
    blueprint = (plan.plan_json or {}).get('blueprint')
    for subject_id, subject_definitions in sorted(grouped.items()):
        subject = db.get(Subject, subject_id)
        if subject is None:
            raise ValueError(f'Recovery roadmap subject {subject_id} no longer exists.')
        child = db.query(SupportPlanVersion).filter_by(
            source_blueprint_plan_id=plan.id,
            subject_id=subject_id,
        ).first()
        if child is not None:
            if child.status != PLAN_PUBLISHED:
                raise ValueError('A subject roadmap from this blueprint is not fully published.')
            created.extend(child.tasks)
            continue
        previous_tasks = (
            db.query(RecoveryTask)
            .join(SupportPlanVersion, SupportPlanVersion.id == RecoveryTask.roadmap_version_id)
            .filter(
                RecoveryTask.student_id == plan.student_id,
                RecoveryTask.subject_id == subject_id,
                RecoveryTask.status == TASK_AVAILABLE,
                SupportPlanVersion.blueprint_group_id == group_id,
                SupportPlanVersion.is_blueprint_group.is_(False),
                SupportPlanVersion.status == PLAN_PUBLISHED,
            )
            .order_by(RecoveryTask.id)
            .all()
        )
        for previous_task in previous_tasks:
            previous_task.status = TASK_SUPERSEDED
            add_task_event(
                db,
                task=previous_task,
                actor_user_id=actor_user_id,
                event_type=TASK_SUPERSEDED,
                detail={
                    'status': TASK_SUPERSEDED,
                    'replacement_blueprint_plan_id': plan.id,
                },
                idempotency_key=f'recovery-blueprint-{plan.id}-supersedes-task-{previous_task.id}',
            )
        child_blueprint = None
        if isinstance(blueprint, dict):
            child_blueprint = {
                **blueprint,
                'status': 'PUBLISHED',
                'subjects': [
                    item for item in blueprint.get('subjects', [])
                    if item.get('subject_id') == subject_id
                ],
                'schedule': [
                    item for item in blueprint.get('schedule', [])
                    if item.get('subject_id') == subject_id
                ],
            }
        child_plan_json: dict[str, Any] = {'tasks': subject_definitions}
        if child_blueprint is not None:
            child_plan_json['blueprint'] = child_blueprint
        child = SupportPlanVersion(
            student_id=plan.student_id,
            subject_id=subject_id,
            subject_scope_review_required=False,
            blueprint_group_id=group_id,
            is_blueprint_group=False,
            source_blueprint_plan_id=plan.id,
            teacher_id=plan.teacher_id,
            version=plan.version,
            summary=f'Subject-specific recovery roadmap for {subject.name}.',
            plan_json=child_plan_json,
            status=PLAN_APPROVED,
            evidence_json=_subject_group_evidence(plan, subject_id),
            generation_key=f'subject-roadmap-{plan.id}-{subject_id}',
            approved_by_user_id=plan.approved_by_user_id or actor_user_id,
            approved_at=plan.approved_at or now,
        )
        db.add(child)
        db.flush()
        for definition in subject_definitions:
            definition['source_blueprint_plan_id'] = plan.id
        created.extend(publish_plan(db, child, actor_user_id, now))
    plan.status = PLAN_PUBLISHED
    plan.published_at = now
    return created


def publish_plan(db: Session, plan: SupportPlanVersion, actor_user_id: int, now: datetime) -> list[RecoveryTask]:
    if plan.status == PLAN_PUBLISHED:
        if plan.is_blueprint_group:
            return (
                db.query(RecoveryTask)
                .filter(RecoveryTask.source_blueprint_plan_id == plan.id)
                .order_by(RecoveryTask.id)
                .all()
            )
        return list(plan.tasks)
    if plan.status != PLAN_APPROVED:
        raise ValueError('Only an approved recovery plan can be published.')
    definitions = plan.plan_json.get('tasks', [])
    if plan.is_blueprint_group and isinstance((plan.plan_json or {}).get('blueprint'), dict):
        definitions = _blueprint_schedule_task_definitions(plan)
    if not definitions:
        raise ValueError('A recovery plan must contain at least one task before publication.')
    if plan.is_blueprint_group:
        return _publish_subject_group(
            db,
            plan=plan,
            definitions=definitions,
            actor_user_id=actor_user_id,
            now=now,
        )
    if plan.subject_id is None or plan.subject_scope_review_required:
        raise ValueError('A subject-specific roadmap is required before tasks can be published.')
    created: list[RecoveryTask] = []
    for definition in definitions:
        task_subject_id = definition.get('subject_id', plan.subject_id)
        if task_subject_id != plan.subject_id:
            raise ValueError('Recovery task subject does not match its parent roadmap subject.')
        task = RecoveryTask(
            roadmap_version_id=plan.id,
            student_id=plan.student_id,
            subject_id=task_subject_id,
            subject_scope_review_required=False,
            scheduled_date=(
                date.fromisoformat(definition['scheduled_date'])
                if definition.get('scheduled_date') else None
            ),
            source_blueprint_plan_id=(
                definition.get('source_blueprint_plan_id') or plan.source_blueprint_plan_id
            ),
            source_schedule_entry_key=definition.get('source_schedule_entry_key'),
            task_key=definition['task_key'],
            subject=definition.get('subject'),
            topic=definition.get('topic'),
            learning_objective=definition['learning_objective'],
            title=definition['title'],
            instructions=definition['instructions'],
            activity_type=definition['activity_type'],
            priority=definition.get('priority', 'MEDIUM'),
            source=definition.get('source', 'AI_PRACTICE'),
            scheduled_at=datetime.fromisoformat(definition['scheduled_at']) if definition.get('scheduled_at') else None,
            deadline=datetime.fromisoformat(definition['deadline']) if definition.get('deadline') else None,
            estimated_minutes=definition['estimated_minutes'],
            resources=[resource if isinstance(resource, dict) else resource.model_dump() for resource in definition.get('resources', [])],
            completion_criteria=definition['completion_criteria'],
            mastery_criteria=definition.get('mastery_criteria'),
            status=TASK_AVAILABLE,
            progress_percent=0,
        )
        db.add(task)
        db.flush()
        detail = {
            'status': TASK_AVAILABLE,
            'source': task.source,
            'evidence_refs': definition.get('evidence_refs', []),
            'field_provenance': definition.get('field_provenance', {}),
        }
        add_task_event(
            db,
            task=task,
            actor_user_id=actor_user_id,
            event_type='PUBLISHED',
            detail=detail,
            idempotency_key=f'recovery-plan-{plan.id}-task-{task.task_key}-published',
        )
        created.append(task)
    plan.status = PLAN_PUBLISHED
    plan.published_at = now
    return created


def record_adaptation_decision(
    db: Session,
    *,
    task: RecoveryTask,
    trigger_event: RecoveryTaskEvent,
) -> tuple[RecoveryAdaptationDecision, bool]:
    if (
        trigger_event.task_id != task.id
        or trigger_event.student_id != task.student_id
        or trigger_event.event_type != 'ASSESSED'
    ):
        raise ValueError('Adaptation decisions require an assessment event for the same recovery task.')

    existing = db.query(RecoveryAdaptationDecision).filter_by(trigger_event_id=trigger_event.id).first()
    if existing is not None:
        return existing, False

    decision = trigger_event.detail.get('decision')
    if decision not in {'complete', 'needs_revision'}:
        raise ValueError('The assessment event does not contain a supported teacher decision.')
    actor = db.query(User).filter_by(id=trigger_event.actor_user_id).first()
    if actor is None or actor.role.upper() != 'TEACHER':
        raise ValueError('Adaptation decisions require an assessment event recorded by a teacher.')

    plan = task.roadmap
    definition = next(
        (item for item in plan.plan_json.get('tasks', []) if item.get('task_key') == task.task_key),
        {},
    )
    evidence_refs = definition.get('evidence_refs', [])
    assessment_events = (
        db.query(RecoveryTaskEvent)
        .filter_by(task_id=task.id, event_type='ASSESSED')
        .order_by(RecoveryTaskEvent.created_at, RecoveryTaskEvent.id)
        .all()
    )
    revision_requests = sum(
        event.detail.get('decision') == 'needs_revision'
        for event in assessment_events
    )

    if decision == 'needs_revision':
        repeated_difficulty = revision_requests >= 2
        decision_type = 'REPEATED_DIFFICULTY_REVIEW' if repeated_difficulty else 'TEACHER_REVIEW_REQUIRED'
        status = 'PENDING_TEACHER_REVIEW'
        recommended_action = (
            'Review the student work and task fit with the student before changing future recovery activities.'
        )
        rationale = (
            f'The teacher requested revision {revision_requests} time(s) for this recovery task. '
            'This is a signal for teacher review, not an automatic diagnosis or proof of a topic-level gap.'
        )
    else:
        decision_type = 'INSUFFICIENT_EVIDENCE'
        status = 'NO_CHANGE'
        recommended_action = 'Do not change the roadmap or mark topic mastery from this task completion.'
        rationale = (
            'The teacher marked the recovery task complete, but task completion and its recovery-task score '
            'do not establish topic mastery or sustained academic improvement. No roadmap change was made.'
        )

    adaptation = RecoveryAdaptationDecision(
        student_id=task.student_id,
        roadmap_version_id=task.roadmap_version_id,
        task_id=task.id,
        trigger_event_id=trigger_event.id,
        decision_type=decision_type,
        status=status,
        recommended_action=recommended_action,
        rationale=rationale,
        evidence_json={
            'source': 'teacher_assessed_recovery_task_event',
            'trigger_event_id': trigger_event.id,
            'trigger_event_type': trigger_event.event_type,
            'task_id': task.id,
            'roadmap_version_id': task.roadmap_version_id,
            'teacher_decision': decision,
            'recovery_task_score': trigger_event.detail.get('score'),
            'score_is_official_grade': False,
            'score_is_mastery_evidence': False,
            'revision_requests_for_task': revision_requests,
            'academic_evidence_refs': evidence_refs,
            'topic_level_mastery': 'NOT_ESTABLISHED',
        },
    )
    try:
        with db.begin_nested():
            db.add(adaptation)
            db.flush()
    except IntegrityError:
        existing = db.query(RecoveryAdaptationDecision).filter_by(trigger_event_id=trigger_event.id).first()
        if existing is None:
            raise
        return existing, False
    return adaptation, True
