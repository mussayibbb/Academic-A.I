from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LearningSchema(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SubjectModuleCreate(LearningSchema):
    module_number: int = Field(ge=1, le=5)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator('title', mode='before')
    @classmethod
    def normalize_title(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class SubjectModuleUpdate(LearningSchema):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator('title', mode='before')
    @classmethod
    def normalize_optional_title(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator('title')
    @classmethod
    def validate_nonempty_title(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError('Module title cannot be blank.')
        return value

    @field_validator('description')
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator('description')
    @classmethod
    def validate_update_has_content(cls, value: str | None, info):
        if value is None and info.data.get('title') is None:
            raise ValueError('Provide a module title or description to update.')
        return value


class SubjectModuleResourceOut(LearningSchema):
    id: int
    module_id: int
    subject_id: int
    teacher_id: int | None
    title: str
    original_filename: str
    file_type: str
    file_size: int
    content_hash: str
    processing_status: str
    error_message: str | None = None
    extracted_text: str | None = None
    extracted_topics: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    uploaded_at: datetime | None = None


class TopicApprovalRequest(LearningSchema):
    resource_id: int
    candidate_id: str | None = Field(default=None, min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=10, max_length=5000)
    learning_objectives: list[str] = Field(default_factory=list, max_length=10)
    concepts: list[str] = Field(default_factory=list, max_length=30)
    source_order: int | None = Field(default=None, ge=1, le=30)
    order_uncertain: bool = False
    approval_status: Literal['DRAFT', 'APPROVED', 'REJECTED', 'DEFERRED'] = 'APPROVED'


class TopicAssignmentCreate(LearningSchema):
    student_id: int | None = Field(default=None)
    approved_topic_id: int
    title: str | None = Field(default=None, max_length=200)
    learning_objective: str | None = Field(default=None, max_length=2000)
    lesson_content: str | None = Field(default=None, max_length=10000)
    expected_duration_minutes: int = Field(default=30, ge=5, le=240)
    scheduled_at: datetime | None = None
    available_from: datetime | None = None
    mastery_criteria: str | None = Field(default=None, max_length=2000)


class TopicAssignmentOut(LearningSchema):
    id: int
    student_id: int
    subject_id: int
    module_id: int
    approved_topic_id: int
    teacher_id: int | None
    title: str
    learning_objective: str
    lesson_content: str
    expected_duration_minutes: int
    scheduled_at: datetime | None = None
    available_from: datetime | None = None
    status: str
    mastery_criteria: str | None = None
    source_provenance: dict[str, Any] = Field(default_factory=dict)


class LearningAssessmentReview(LearningSchema):
    reason: str | None = Field(default=None, max_length=2000)


class LearningAnswerReview(LearningSchema):
    decision: Literal['accept', 'override']
    marks_awarded: float | None = Field(default=None, ge=0, le=5)
    feedback: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator('reason', 'feedback', mode='before')
    @classmethod
    def normalize_optional_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator('reason')
    @classmethod
    def require_override_reason(cls, value: str | None, info):
        if info.data.get('decision') == 'override' and not value:
            raise ValueError('A reason is required to override an assessment grade.')
        return value


class AssessmentAttemptSubmit(LearningSchema):
    answers: dict[str, str] = Field(min_length=20, max_length=20)

    @field_validator('answers')
    @classmethod
    def validate_answers(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.isdigit() or len(answer) > 10000 for key, answer in value.items()):
            raise ValueError('Answers must map numeric question IDs to text responses of at most 10000 characters.')
        return value
