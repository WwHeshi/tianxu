"""API schemas for administrator-triggered MingLi evaluations."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...agent_trace import (
    AgentModelCallTrace as EvaluationModelCallTrace,
)
from ...agent_trace import (
    AgentToolExecutionTrace as EvaluationToolExecutionTrace,
)


class EvaluationStartRequest(BaseModel):
    scope: Literal["quick", "year", "all"]
    benchmark_year: Literal[2022, 2023, 2024, 2025] | None = None
    mode: Literal["tianxu_fortune"] = "tianxu_fortune"
    max_concurrency: int = Field(default=2, ge=1, le=4)
    confirmed_request_count: int = Field(ge=1, le=160)

    @model_validator(mode="after")
    def validate_year_scope(self) -> "EvaluationStartRequest":
        if self.scope == "year" and self.benchmark_year is None:
            raise ValueError("单年评测必须选择年份")
        if self.scope != "year" and self.benchmark_year is not None:
            raise ValueError("只有单年评测可以指定年份")
        return self


class EvaluationBreakdown(BaseModel):
    key: str
    total: int
    completed: int
    correct: int
    errors: int
    accuracy: float | None


class EvaluationRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scope: str
    benchmark_year: int | None
    mode: str
    max_concurrency: int
    dataset_name: str
    dataset_sha256: str
    dataset_question_count: int
    provider: str
    api_protocol: str
    model: str
    prompt_version: str
    engine_version: str
    calculation_policy_version: str
    status: str
    total_questions: int
    completed_questions: int
    correct_answers: int
    error_count: int
    input_tokens: int
    output_tokens: int
    progress: float
    accuracy: float | None
    started_at: datetime | None
    finished_at: datetime | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime


class EvaluationRunDetail(EvaluationRunSummary):
    by_year: list[EvaluationBreakdown]
    by_category: list[EvaluationBreakdown]


class EvaluationRunList(BaseModel):
    items: list[EvaluationRunSummary]
    total: int


class EvaluationOptionResponse(BaseModel):
    letter: str
    text: str


class EvaluationItemResponse(BaseModel):
    id: int
    question_id: str
    case_id: str
    benchmark_year: int
    category: str
    question: str
    options: list[EvaluationOptionResponse]
    correct_answer: str
    predicted_answer: str | None
    is_correct: bool
    status: str
    confidence: int | None
    reasoning_summary: str | None
    error_message: str | None
    latency_ms: int | None
    input_tokens: int
    output_tokens: int
    prompt_sha256: str | None


class EvaluationItemList(BaseModel):
    items: list[EvaluationItemResponse]
    total: int


class EvaluationItemTraceResponse(BaseModel):
    question_id: str
    status: str
    api_protocol: str
    model: str
    endpoint: str
    system_prompt: str | None = None
    user_prompt: str | None = None
    model_calls: list[EvaluationModelCallTrace] = Field(default_factory=list)
    tool_executions: list[EvaluationToolExecutionTrace] = Field(default_factory=list)
    prompt_sha256: str | None
    redacted: list[str]


class EvaluationDatasetOverview(BaseModel):
    available: bool
    error: str | None = None
    dataset_name: str
    sha256: str | None = None
    question_count: int = 0
    case_count: int = 0
    years: dict[str, int] = Field(default_factory=dict)
    scopes: dict[str, int] = Field(default_factory=dict)


class EvaluationOverview(BaseModel):
    dataset: EvaluationDatasetOverview
    model_configured: bool
    model: str | None = None
    api_protocol: str | None = None
    active_run: EvaluationRunSummary | None = None
