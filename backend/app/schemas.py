"""HTTP schemas for the chart preview endpoint."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class LunarDateInput(BaseModel):
    """A Chinese lunar calendar date; the clock remains in beijing_datetime."""

    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=1, le=9999)
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=30)
    is_leap_month: bool = False


class BirthInput(BaseModel):
    """Birth information entered using the Beijing standard-time clock."""

    model_config = ConfigDict(extra="forbid")

    beijing_datetime: datetime
    calendar_type: Literal["solar", "lunar"] = "solar"
    lunar_date: LunarDateInput | None = None
    birthplace: Birthplace | None = None
    gender: Gender
    calculation_policy: CalculationPolicy = Field(default_factory=CalculationPolicy)

    @model_validator(mode="before")
    @classmethod
    def default_time_mode_from_birthplace(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        values = dict(data)
        raw_policy = values.get("calculation_policy", {})
        if isinstance(raw_policy, dict):
            policy = dict(raw_policy)
            policy.setdefault("true_solar_time", values.get("birthplace") is not None)
            values["calculation_policy"] = policy
        return values

    @model_validator(mode="after")
    def require_consistent_time_mode(self) -> "BirthInput":
        if self.birthplace is None and self.calculation_policy.true_solar_time:
            raise ValueError("未选择出生地点时，true_solar_time 必须为 false")
        if self.birthplace is not None and not self.calculation_policy.true_solar_time:
            raise ValueError("已选择出生地点时，true_solar_time 必须为 true")
        return self

    @model_validator(mode="after")
    def require_consistent_calendar_input(self) -> "BirthInput":
        if self.calendar_type == "solar" and self.lunar_date is not None:
            raise ValueError("公历输入时禁止提供 lunar_date")
        if self.calendar_type == "lunar" and self.lunar_date is None:
            raise ValueError("农历输入时必须提供 lunar_date")
        return self

    @field_validator("beijing_datetime")
    @classmethod
    def require_naive_beijing_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError("北京时间请直接填写钟表时间，不要附带时区或 UTC 偏移")
        return value


class NormalizedBirthInput(BaseModel):
    beijing_datetime: datetime
    true_solar_datetime: datetime
    calendar_type: Literal["solar", "lunar"]
    lunar_date: LunarDateInput | None
    birthplace: CanonicalBirthplace | None
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


class ChartCalendar(BaseModel):
    """Calendar labels derived from the exact datetime used for the pillars."""

    solar_datetime: datetime
    lunar_year: int
    lunar_month: int
    lunar_day: int
    is_leap_month: bool
    lunar_text: str
    time_branch: str
    zodiac: str
    destiny_type: Literal["乾造", "坤造", "命造"]


class Pillar(BaseModel):
    name: Literal["year", "month", "day", "hour"]
    gan_zhi: str
    heavenly_stem: Component
    earthly_branch: BranchComponent
    growth_stage: str
    self_growth_stage: str
    xun_kong: str
    na_yin: str
    shen_sha: list[str]


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
    calendar: ChartCalendar
    pillars: Pillars
    day_master: Component
    element_distribution: ElementDistribution


class EngineInfo(BaseModel):
    name: str
    version: str
    policy_version: str
    shen_sha_policy_version: str
    solar_time_note: str


class ChartPreviewResponse(BaseModel):
    normalized_input: NormalizedBirthInput
    chart: Chart
    calculation_policy: CalculationPolicy
    solar_time_adjustment: SolarTimeAdjustment | None
    engine: EngineInfo
    warnings: list[str]
    limitations: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    engine_version: str
