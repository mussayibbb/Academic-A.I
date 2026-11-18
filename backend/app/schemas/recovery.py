from datetime import date, datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecoverySchema(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RecoveryPlanGenerateRequest(RecoverySchema):
    generation_key: str | None = Field(default=None, min_length=8, max_length=100)

    @field_validator('generation_key', mode='before')
    @classmethod
    def normalize_generation_key(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryBlueprintGenerateRequest(RecoverySchema):
    academic_period: str | None = Field(default=None, min_length=7, max_length=30)
    exam_date: date
    subject_ids: list[int] | None = Field(default=None, description='Deprecated selection; all eligible subjects are included automatically.')
    syllabus_context: str | None = Field(default=None, max_length=8000)
    syllabus_source: str | None = Field(default=None, max_length=500)
    acknowledge_no_syllabus: bool = False
    available_minutes_per_day: int | None = Field(default=None, ge=5, le=600)

    @field_validator('academic_period')
    @classmethod
    def validate_academic_period(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not re.fullmatch(r'\d{4}-S[12]', normalized):
            raise ValueError('academic_period must use the YYYY-S1 or YYYY-S2 format.')
        return normalized

    @field_validator('subject_ids')
    @classmethod
    def validate_unique_subjects(cls, values: list[int] | None) -> list[int] | None:
        if values is None:
            return None
        if any(value <= 0 for value in values) or len(set(values)) != len(values):
            raise ValueError('subject_ids must contain unique positive subject IDs.')
        return values

    @field_validator('syllabus_context', 'syllabus_source', mode='before')
    @classmethod
    def normalize_optional_context(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode='after')
    def require_context_or_acknowledgement(self):
        if self.syllabus_context and not self.syllabus_source:
            raise ValueError('Provide the source label for teacher-supplied syllabus context.')
        if not self.syllabus_context and not self.acknowledge_no_syllabus:
            raise ValueError('Provide syllabus context or confirm that no official syllabus mapping is available.')
        return self


class RecoveryBlueprintAIRecommendation(RecoverySchema):
    topic_id: int = Field(gt=0)
    priority: Literal['HIGH', 'MEDIUM', 'LOW']
    suggested_minutes: int = Field(ge=5, le=240)


class RecoveryBlueprintAIResponse(RecoverySchema):
    topics: list[RecoveryBlueprintAIRecommendation] = Field(min_length=1, max_length=300)


class RecoveryBlueprintTopicDecision(RecoverySchema):
    topic_id: int = Field(gt=0)
    decision: Literal['INCLUDE', 'EXCLUDE']


class RecoveryBlueprintSyllabusMapping(RecoverySchema):
    topic_id: int = Field(gt=0)
    mapping: str = Field(min_length=3, max_length=1000)

    @field_validator('mapping')
    @classmethod
    def normalize_mapping(cls, value: str) -> str:
        return value.strip()


class RecoveryBlueprintTopicResolution(RecoverySchema):
    topic_id: int = Field(gt=0)
    resolution: str = Field(min_length=5, max_length=1000)

    @field_validator('resolution')
    @classmethod
    def normalize_resolution(cls, value: str) -> str:
        return value.strip()


class RecoveryBlueprintScheduleEntry(RecoverySchema):
    scheduled_date: date
    activity: Literal['TOPIC_STUDY', 'REVISION', 'MOCK_PRACTICE', 'RETENTION_CHECK']
    stage: Literal['MODULE_3', 'MODULE_4', 'REVISION_ASSESSMENT'] | None = None
    estimated_minutes: int = Field(ge=1, le=600)
    topic_id: int | None = Field(default=None, gt=0)
    subject_id: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def require_topic_for_topic_study(self):
        if self.activity == 'TOPIC_STUDY' and self.topic_id is None:
            raise ValueError('TOPIC_STUDY schedule entries require a topic_id.')
        return self


class RecoveryBlueprintEditRequest(RecoverySchema):
    topic_decisions: list[RecoveryBlueprintTopicDecision] = Field(default_factory=list, max_length=300)
    syllabus_mappings: list[RecoveryBlueprintSyllabusMapping] = Field(default_factory=list, max_length=300)
    conflict_resolutions: list[RecoveryBlueprintTopicResolution] = Field(default_factory=list, max_length=300)
    schedule: list[RecoveryBlueprintScheduleEntry] | None = None
    syllabus_reviewed: bool | None = None


class RecoveryTutorTurn(RecoverySchema):
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=2000)

    @field_validator('content', mode='before')
    @classmethod
    def normalize_content(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryTutorRequest(RecoverySchema):
    question: str = Field(min_length=1, max_length=2000)
    conversation_history: list[RecoveryTutorTurn] = Field(default_factory=list, max_length=6)

    @field_validator('question', mode='before')
    @classmethod
    def normalize_question(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryTutorModelResponse(RecoverySchema):
    supported: bool
    answer_scope: Literal['GENERAL_EXPLANATION', 'STUDENT_EVIDENCE'] = 'STUDENT_EVIDENCE'
    answer: str = Field(default='', max_length=4000)
    practice_suggestions: list[str] = Field(default_factory=list, max_length=4)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('answer')
    @classmethod
    def normalize_answer(cls, value):
        return value.strip()

    @field_validator('practice_suggestions')
    @classmethod
    def normalize_suggestions(cls, values):
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 500 for value in normalized):
            raise ValueError('Practice suggestions must contain 1 to 500 characters.')
        return normalized

    @field_validator('evidence_refs')
    @classmethod
    def normalize_evidence_refs(cls, values):
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 100 for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError('Evidence references must be non-empty, unique, and at most 100 characters.')
        return normalized


class RecoveryTutorOut(BaseModel):
    status: Literal['answered', 'unsupported', 'no_evidence', 'unavailable']
    answer_source: Literal['provider', 'built_in', 'deterministic_fallback']
    answer_scope: Literal['GENERAL_EXPLANATION', 'STUDENT_EVIDENCE'] | None = None
    answer: str
    practice_suggestions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    provider: str | None = None
    document_retrieval: Literal['disabled_unscoped_index'] = 'disabled_unscoped_index'


class RecoveryResource(RecoverySchema):
    title: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=2048)


class RecoveryActivity(RecoverySchema):
    reason: str = Field(min_length=5, max_length=2000)
    lesson: str = Field(min_length=5, max_length=5000)
    steps: list[str] = Field(min_length=1, max_length=8)
    exercise: str = Field(min_length=5, max_length=5000)
    submission_format: str = Field(min_length=5, max_length=2000)

    @field_validator('steps')
    @classmethod
    def validate_steps(cls, values):
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 1000 for value in normalized):
            raise ValueError('Each activity step must contain 1 to 1000 characters.')
        return normalized


class RecoveryTaskDraft(RecoverySchema):
    title: str = Field(min_length=2, max_length=200)
    learning_objective: str = Field(min_length=5, max_length=2000)
    instructions: str = Field(min_length=5, max_length=10000)
    activity_type: str = Field(min_length=2, max_length=40)
    priority: Literal['LOW', 'MEDIUM', 'HIGH'] = 'MEDIUM'
    subject_id: int | None = Field(default=None, gt=0)
    subject: str | None = Field(default=None, max_length=160)
    topic: str | None = Field(default=None, max_length=160)
    scheduled_at: datetime | None = None
    deadline: datetime | None = None
    estimated_minutes: int = Field(ge=1, le=240)
    resources: list[RecoveryResource] = Field(default_factory=list, max_length=20)
    completion_criteria: str = Field(min_length=5, max_length=2000)
    mastery_criteria: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    activity: RecoveryActivity | None = None

    @model_validator(mode='after')
    def validate_schedule(self):
        if self.scheduled_at and self.deadline and self.deadline < self.scheduled_at:
            raise ValueError('deadline cannot be earlier than scheduled_at')
        return self


class RecoveryPlanEditRequest(RecoverySchema):
    summary: str | None = Field(default=None, min_length=3, max_length=2000)
    tasks: list[RecoveryTaskDraft] | None = Field(default=None, max_length=20)

    @model_validator(mode='after')
    def require_an_edit(self):
        if self.summary is None and self.tasks is None:
            raise ValueError('Provide a summary or task list to edit the recovery plan.')
        return self

    @field_validator('summary', mode='before')
    @classmethod
    def normalize_summary(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryPlanDecisionRequest(RecoverySchema):
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator('reason', mode='before')
    @classmethod
    def normalize_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: int
    subject_id: int | None = None
    subject_scope_review_required: bool = True
    blueprint_group_id: str | None = None
    is_blueprint_group: bool = False
    source_blueprint_plan_id: int | None = None
    teacher_id: int | None
    version: int
    status: str
    summary: str | None
    plan: dict[str, Any]
    evidence: dict[str, Any]
    generation_key: str | None
    approved_by_user_id: int | None
    approved_at: datetime | None
    published_at: datetime | None
    created_at: datetime


class RecoveryTaskProgressRequest(RecoverySchema):
    action: Literal['start', 'progress']
    idempotency_key: str = Field(min_length=8, max_length=100)
    progress_percent: int | None = Field(default=None, ge=0, le=99)

    @model_validator(mode='after')
    def validate_progress_action(self):
        if self.action == 'progress' and self.progress_percent is None:
            raise ValueError('progress_percent is required for a progress update')
        if self.action == 'start' and self.progress_percent is not None:
            raise ValueError('progress_percent is not accepted when starting a task')
        return self

    @field_validator('idempotency_key', mode='before')
    @classmethod
    def normalize_idempotency_key(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryTaskActivityApprovalRequest(RecoverySchema):
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator('idempotency_key', mode='before')
    @classmethod
    def normalize_idempotency_key(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryTaskSubmitRequest(RecoverySchema):
    idempotency_key: str = Field(min_length=8, max_length=100)
    response: str = Field(min_length=1, max_length=20000)

    @field_validator('idempotency_key', mode='before')
    @classmethod
    def normalize_idempotency_key(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('response', mode='before')
    @classmethod
    def normalize_response(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryTaskDraftSaveRequest(RecoverySchema):
    idempotency_key: str = Field(min_length=8, max_length=100)
    response: str = Field(max_length=20000)

    @field_validator('idempotency_key', mode='before')
    @classmethod
    def normalize_idempotency_key(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryTaskActivityEditRequest(RecoverySchema):
    idempotency_key: str = Field(min_length=8, max_length=100)
    title: str = Field(min_length=2, max_length=200)
    learning_objective: str = Field(min_length=5, max_length=2000)
    instructions: str = Field(min_length=5, max_length=10000)
    completion_criteria: str = Field(min_length=5, max_length=2000)
    resources: list[RecoveryResource] = Field(default_factory=list, max_length=20)
    activity: RecoveryActivity

    @field_validator('idempotency_key', mode='before')
    @classmethod
    def normalize_idempotency_key(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryTaskAssessmentRequest(RecoverySchema):
    idempotency_key: str = Field(min_length=8, max_length=100)
    decision: Literal['complete', 'needs_revision']
    feedback: str = Field(min_length=1, max_length=5000)
    score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    @field_validator('idempotency_key', mode='before')
    @classmethod
    def normalize_idempotency_key(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('feedback', mode='before')
    @classmethod
    def normalize_feedback(cls, value):
        return value.strip() if isinstance(value, str) else value


class RecoveryTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    roadmap_version_id: int
    student_id: int
    subject_id: int | None = None
    subject_scope_review_required: bool = True
    scheduled_date: date | None = None
    task_key: str
    subject: str | None
    topic: str | None
    stage: str | None = None
    learning_objective: str
    title: str
    instructions: str
    activity_type: str
    priority: str
    source: str
    scheduled_at: datetime | None
    deadline: datetime | None
    estimated_minutes: int
    resources: list[dict[str, Any]]
    completion_criteria: str
    mastery_criteria: str | None
    status: str
    progress_percent: int
    student_response: str | None
    submitted_at: datetime | None
    assessed_score: float | None
    teacher_feedback: str | None
    revision_count: int
    completed_at: datetime | None
    evidence_refs: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    field_provenance: dict[str, str] = Field(default_factory=dict)
    activity: RecoveryActivity | None = None


class RecoveryTaskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    student_id: int
    subject_id: int | None = None
    actor_user_id: int | None
    event_type: str
    detail: dict[str, Any]
    idempotency_key: str
    created_at: datetime


class RecoveryAdaptationDecisionOut(BaseModel):
    id: int
    student_id: int
    roadmap_version_id: int
    task_id: int
    trigger_event_id: int
    decision_type: Literal[
        'TEACHER_REVIEW_REQUIRED',
        'REPEATED_DIFFICULTY_REVIEW',
        'INSUFFICIENT_EVIDENCE',
    ]
    status: Literal['PENDING_TEACHER_REVIEW', 'NO_CHANGE']
    recommended_action: str
    rationale: str
    evidence: dict[str, Any]
    created_at: datetime
