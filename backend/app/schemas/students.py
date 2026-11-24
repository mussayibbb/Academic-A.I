
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)


StudentIdentifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
]

StudentName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]


class StudentCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    student_id: StudentIdentifier
    usn: str | None = Field(default=None, max_length=40)
    full_name: StudentName
    email: EmailStr | None = None

    program: str | None = Field(default=None, max_length=160)
    year: str | None = Field(default=None, max_length=30)
    semester: str | None = Field(default=None, max_length=40)
    section: str | None = Field(default=None, max_length=40)
    department: str | None = Field(default=None, max_length=160)
    branch: str | None = Field(default=None, max_length=80)

    course_id: int | None = Field(default=None, gt=0)

    # Existing GPA fields: 4-point scale
    previous_gpa: float | None = Field(default=None, ge=0, le=4)
    current_gpa: float | None = Field(default=None, ge=0, le=4)

    # New CGPA fields: 10-point scale
    previous_cgpa: float | None = Field(default=None, ge=0, le=10)
    current_cgpa: float | None = Field(default=None, ge=0, le=10)

    attendance: float = Field(default=0, ge=0, le=100)
    assessment: float = Field(default=0, ge=0, le=100)
    assignments_completed: float = Field(default=0, ge=0)

    @field_validator('email', mode='before')
    @classmethod
    def normalize_email(cls, value: object) -> str | None:
        normalized = str(value).strip().lower() if value is not None else ''
        return normalized or None

    @field_validator(
        'program',
        'year',
        'semester',
        'section',
        'department',
        'usn',
        'branch',
        mode='before',
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        normalized = str(value).strip() if value is not None else ''
        return normalized or None


class StudentUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    student_id: StudentIdentifier | None = None
    usn: str | None = Field(default=None, max_length=40)
    full_name: StudentName | None = None
    email: EmailStr | None = None

    program: str | None = Field(default=None, max_length=160)
    year: str | None = Field(default=None, max_length=30)
    semester: str | None = Field(default=None, max_length=40)
    section: str | None = Field(default=None, max_length=40)
    department: str | None = Field(default=None, max_length=160)
    branch: str | None = Field(default=None, max_length=80)

    course_id: int | None = Field(default=None, gt=0)

    # Existing GPA fields: 4-point scale
    previous_gpa: float | None = Field(default=None, ge=0, le=4)
    current_gpa: float | None = Field(default=None, ge=0, le=4)
    # New CGPA fields: 10-point scale
    previous_cgpa: float | None = Field(default=None, ge=0, le=10)
    current_cgpa: float | None = Field(default=None, ge=0, le=10)

    attendance: float | None = Field(default=None, ge=0, le=100)
    assessment: float | None = Field(default=None, ge=0, le=100)
    assignments_completed: float | None = Field(default=None, ge=0)

    @field_validator('email', mode='before')
    @classmethod
    def normalize_email(cls, value: object) -> str | None:
        normalized = str(value).strip().lower() if value is not None else ''
        return normalized or None

    @field_validator(
        'program',
        'year',
        'semester',
        'section',
        'department',
        'usn',
        'branch',
        mode='before',
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        normalized = str(value).strip() if value is not None else ''
        return normalized or None


class StudentRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: str
    usn: str | None = None
    full_name: str
    email: str | None = None

    program: str | None = None
    year: str | None = None
    semester: str | None = None
    section: str | None = None
    department: str | None = None
    branch: str | None = None

    course_id: int | None = None
    data_origin: str

    # Existing 4-point GPA
    previous_gpa: float | None = None
    current_gpa: float | None = None

    # New 10-point CGPA
    previous_cgpa: float | None = None
    current_cgpa: float | None = None

    attendance: float
    assessment: float
    assignments_completed: float


class StudentCsvImportResult(BaseModel):
    imported: int
    rejected: int
    duplicates: int
    batch_id: int | None
    duplicate_import: bool = False
    errors: list[dict[str, object]] = Field(default_factory=list)
    source_provenance: dict[str, str | bool] = Field(default_factory=dict)


class StudentStatsOut(BaseModel):
    total: int
    high_risk: int | None
    medium_risk: int | None
    low_risk: int | None
    average_attendance: float | None
    average_gpa: float | None
    attention: int | None
    recently_added: int
    by_origin: dict[str, int]
    prediction_status: str
    prediction_message: str | None = None
