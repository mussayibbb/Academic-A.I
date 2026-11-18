from pydantic import BaseModel, ConfigDict, Field, model_validator


class SubjectFocusPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    expected_version: int = Field(ge=1)
    threshold_score: float = Field(ge=0, le=25)
    threshold_maximum: float = Field(ge=25, le=25)
    change_reason: str = Field(min_length=5, max_length=1000)

    @model_validator(mode='after')
    def validate_threshold(self):
        if not self.change_reason.strip():
            raise ValueError('change_reason must not be blank.')
        return self
