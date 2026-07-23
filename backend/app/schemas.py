"""HTTP schemas for the chart preview endpoint."""

from datetime import datetime
from enum import Enum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .bazi.policy import CalculationPolicy


class Gender(str, Enum):
    male = "male"
    female = "female"
    other = "other"


class BirthInput(BaseModel):
    """User-supplied civil birth information.

    ``local_datetime`` may be naive (interpreted in ``timezone``) or include
    an offset (converted into ``timezone`` before calculation).
    """

    model_config = ConfigDict(extra="forbid")

    local_datetime: datetime
    timezone: str = Field(min_length=1, max_length=64)
    gender: Gender
    longitude: float | None = Field(default=None, ge=-180, le=180)
    calculation_policy: CalculationPolicy = Field(default_factory=CalculationPolicy)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("时区必须是有效的 IANA 时区，例如 Asia/Shanghai")
        return value


class NormalizedBirthInput(BaseModel):
    local_datetime: datetime
    utc_datetime: datetime
    timezone: str
    gender: Gender
    longitude: float | None = None


class Component(BaseModel):
    symbol: str
    element: str
    polarity: Literal["yang", "yin"]
    ten_god: str | None = None


class HiddenStem(Component):
    pass


class BranchComponent(BaseModel):
    symbol: str
    element: str
    polarity: Literal["yang", "yin"]
    hidden_stems: list[HiddenStem]


class Pillar(BaseModel):
    name: Literal["year", "month", "day", "hour"]
    gan_zhi: str
    heavenly_stem: Component
    earthly_branch: BranchComponent
    na_yin: str


class ElementDistribution(BaseModel):
    """Counts are intentionally split because schools weight hidden stems differently."""

    visible: dict[str, int]
    hidden_stems: dict[str, int]
    total: dict[str, int]


class Pillars(BaseModel):
    year: Pillar
    month: Pillar
    day: Pillar
    hour: Pillar


class Chart(BaseModel):
    pillars: Pillars
    day_master: Component
    element_distribution: ElementDistribution


class EngineInfo(BaseModel):
    name: str
    version: str
    policy_version: str
    timezone_note: str


class ChartPreviewResponse(BaseModel):
    normalized_input: NormalizedBirthInput
    chart: Chart
    calculation_policy: CalculationPolicy
    engine: EngineInfo
    warnings: list[str]
    limitations: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    engine_version: str
