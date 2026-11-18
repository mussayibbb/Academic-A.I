from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RiskOverviewSchema(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SubjectEvidenceOut(RiskOverviewSchema):
    record_id: int
    id: int
    code: str
    name: str
    academic_period: str
    fa1: float | None
    fa1_observed_at: datetime | None
    temporal_evidence_verified: bool
    import_batch_id: int | None
    source_filename: str | None
    fa2: float | None
    fa2_observed_at: datetime | None
    assignment_mark: float | None
    assignment_observed_at: datetime | None
    attendance_rate: float | None
    attendance_observed_at: datetime | None


class RecordedRiskOut(RiskOverviewSchema):
    risk_level: Literal['HIGH', 'MEDIUM', 'LOW']
    observed_at: datetime
    outcome_source: str
    academic_period: str


class RiskOverviewStudentOut(RiskOverviewSchema):
    id: int
    student_id: str
    full_name: str
    section: str | None
    course_name: str | None
    risk_category: Literal['HIGH', 'MEDIUM', 'LOW', 'INSUFFICIENT_EVIDENCE']
    risk_outcome: RecordedRiskOut | None
    subjects: list[SubjectEvidenceOut]
    indicators: list[str]


class RiskOverviewOut(RiskOverviewSchema):
    academic_period: str | None
    academic_periods: list[str]
    classes: list[str]
    counts: dict[str, int]
    items: list[RiskOverviewStudentOut]
    total: int
