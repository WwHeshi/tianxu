"""HTTP schemas for the chart preview endpoint."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .bazi.policy import CalculationPolicy


class Gender(str, Enum):
    male = "male"
    female = "female"
    other = "other"


class Birthplace(BaseModel):
    """A province/city/district selection using Chinese administrative codes."""

    model_config = ConfigDict(extra="forbid")

    country_code: Literal["CN"] = "CN"
    province_code: str = Field(pattern=r"^\d{6}$")
    province_name: str = Field(min_length=1, max_length=32)
    city_code: str | None = Field(default=None, pattern=r"^\d{6}$")
    city_name: str | None = Field(default=None, min_length=1, max_length=64)
    district_code: str = Field(pattern=r"^\d{6}$")
    district_name: str = Field(min_length=1, max_length=64)

    @field_validator("province_name", "district_name")
    @classmethod
    def strip_names(cls, value: str) -> str:
        return value.strip()

    @field_validator("city_name")
    @classmethod
    def strip_optional_city_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_code_hierarchy(self) -> "Birthplace":
        province_prefix = self.province_code[:2]
        if (self.city_code is None) != (self.city_name is None):
            raise ValueError("城市代码和城市名称必须同时提供或同时省略")
        if self.city_code is not None and not self.city_code.startswith(province_prefix):
            raise ValueError("城市代码与省级行政区不匹配")
        if not self.district_code.startswith(province_prefix):
            raise ValueError("区县代码与省级行政区不匹配")
        return self


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
    birthplace: Birthplace
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
