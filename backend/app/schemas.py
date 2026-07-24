"""HTTP schemas for the chart preview endpoint."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .bazi.policy import CalculationPolicy


class Gender(str, Enum):
    male = "male"
    female = "female"
    other = "other"


class Birthplace(BaseModel):
    """A stable identifier selected from the bundled location snapshot."""

    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(min_length=1, max_length=128)

    @field_validator("location_id")
    @classmethod
    def strip_location_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("出生地点标识不能为空")
        return stripped


class DivisionPathItem(BaseModel):
    code: str
    name: str
    type: str


class CanonicalBirthplace(BaseModel):
    location_id: str
    region_code: str
    timezone: str
    division_path: list[DivisionPathItem]


class BirthInput(BaseModel):
    """Birth information entered using the Beijing standard-time clock."""

    model_config = ConfigDict(extra="forbid")

    beijing_datetime: datetime
    birthplace: Birthplace
    gender: Gender
    calculation_policy: CalculationPolicy = Field(default_factory=CalculationPolicy)

    @field_validator("beijing_datetime")
    @classmethod
    def require_naive_beijing_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError("北京时间请直接填写钟表时间，不要附带时区或 UTC 偏移")
        return value


class NormalizedBirthInput(BaseModel):
    beijing_datetime: datetime
    true_solar_datetime: datetime
    birthplace: CanonicalBirthplace
    gender: Gender


class SolarTimeAdjustment(BaseModel):
    longitude_degrees: float
    latitude_degrees: float | None = None
    reference_meridian_degrees: float
    longitude_correction_minutes: float
    equation_of_time_minutes: float
    total_correction_minutes: float
    location_precision: str
    coordinate_match: str
    coordinate_source: str


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
    solar_time_note: str


class ChartPreviewResponse(BaseModel):
    normalized_input: NormalizedBirthInput
    chart: Chart
    calculation_policy: CalculationPolicy
    solar_time_adjustment: SolarTimeAdjustment
    engine: EngineInfo
    warnings: list[str]
    limitations: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    engine_version: str
