"""HTTP schemas for the chart preview endpoint."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .bazi.policy import CalculationPolicy


class Gender(str, Enum):
    male = "male"
    female = "female"


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
    destiny_type: Literal["乾造", "坤造"]


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


class FortunePillar(BaseModel):
    gan_zhi: str
    heavenly_stem: Component
    earthly_branch: Component


class BigLuckTransition(BaseModel):
    solar_datetime: datetime
    from_index: int
    from_gan_zhi: str | None
    to_index: int
    to_gan_zhi: str


class MonthlyFortune(BaseModel):
    index: int
    solar_term: str
    start_solar_datetime: datetime
    segment_start_solar_datetime: datetime
    segment_end_solar_datetime: datetime
    pillar: FortunePillar
    big_luck_index_at_start: int
    big_luck_gan_zhi_at_start: str | None
    transition_phase: Literal["before", "after"] | None
    transition: BigLuckTransition | None


class AnnualFortune(BaseModel):
    index: int
    year: int
    nominal_age: int
    segment_start_solar_datetime: datetime
    segment_end_solar_datetime: datetime
    pillar: FortunePillar
    months: list[MonthlyFortune]
    big_luck_index_at_start: int
    big_luck_gan_zhi_at_start: str | None
    transition_phase: Literal["before", "after"] | None
    transition: BigLuckTransition | None


class BigLuckPeriod(BaseModel):
    index: int
    is_before_start: bool
    start_year: int
    end_year: int
    start_nominal_age: int
    end_nominal_age: int
    start_solar_datetime: datetime
    end_solar_datetime: datetime
    pillar: FortunePillar | None
    years: list[AnnualFortune]


class FortuneStartOffset(BaseModel):
    years: int
    months: int
    days: int
    hours: int


class FortuneCycles(BaseModel):
    policy_version: str
    direction: Literal["forward", "backward"]
    start_offset: FortuneStartOffset
    start_solar_datetime: datetime
    big_luck_periods: list[BigLuckPeriod]


class Chart(BaseModel):
    calendar: ChartCalendar
    pillars: Pillars
    day_master: Component
    element_distribution: ElementDistribution
    fortune_cycles: FortuneCycles | None


class EngineInfo(BaseModel):
    name: str
    version: str
    policy_version: str
    shen_sha_policy_version: str
    fortune_policy_version: str
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


ApiProtocol = Literal["responses", "chat_completions"]


class ModelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai"] = "openai"
    api_protocol: ApiProtocol = "responses"
    model: str = Field(min_length=1, max_length=128)
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1, max_length=512)
    api_key: SecretStr = Field(min_length=8, max_length=1024)

    @field_validator("model", "base_url")
    @classmethod
    def strip_setting(cls, value: str) -> str:
        return value.strip().rstrip("/")


class ModelSettingsResponse(BaseModel):
    configured: bool
    provider: str | None = None
    api_protocol: ApiProtocol | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_masked: str | None = None


class ModelConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai"] = "openai"
    api_protocol: ApiProtocol = "responses"
    model: str = Field(min_length=1, max_length=128)
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1, max_length=512)
    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=1024)

    @field_validator("model", "base_url")
    @classmethod
    def strip_connection_setting(cls, value: str) -> str:
        return value.strip().rstrip("/")


class ModelConnectionTestResponse(BaseModel):
    ok: Literal[True]
    provider: str
    api_protocol: ApiProtocol
    model: str
    message: str


class BaziReport(BaseModel):
    """Strict model output; labels are mapped to Chinese in the UI."""

    model_config = ConfigDict(extra="forbid")

    chart_overview: str = Field(min_length=1, max_length=4000)
    temperament: str = Field(min_length=1, max_length=4000)
    career: str = Field(min_length=1, max_length=4000)
    finance: str = Field(min_length=1, max_length=4000)
    relationships: str = Field(min_length=1, max_length=4000)
    current_fortune: str = Field(min_length=1, max_length=4000)
    recommendations: str = Field(min_length=1, max_length=4000)
    limitations: str = Field(min_length=1, max_length=4000)


class ReportMetadata(BaseModel):
    provider: str
    api_protocol: ApiProtocol
    model: str
    prompt_version: str
    schema_version: str
    engine_version: str


class AgentTraceStep(BaseModel):
    id: str
    title: str
    category: Literal["deterministic", "context", "prompt", "model", "tool", "validation"]
    status: Literal["completed", "failed"]
    detail: str
    duration_ms: int | None = None


class AgentRequestDebug(BaseModel):
    method: Literal["POST"]
    endpoint: str
    provider: str
    api_protocol: ApiProtocol
    model: str
    request_count: int = Field(ge=1)
    body: dict[str, Any]


class AgentModelCallDebug(BaseModel):
    sequence: int = Field(ge=1)
    stage: Literal["action_selection", "final_answer"]
    request_body: dict[str, Any]
    response_body: dict[str, Any]
    duration_ms: int
    tool_call_count: int = Field(default=0, ge=0)


class AgentToolExecutionDebug(BaseModel):
    sequence: int = Field(ge=1)
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    duration_ms: int | None = None


class AgentDebugTrace(BaseModel):
    steps: list[AgentTraceStep]
    system_prompt: str
    user_prompt: str
    request: AgentRequestDebug
    raw_response: dict[str, Any]
    model_calls: list[AgentModelCallDebug] = Field(default_factory=list)
    tool_executions: list[AgentToolExecutionDebug] = Field(default_factory=list)
    redacted: list[str]


class ReportGenerationResponse(BaseModel):
    chart: ChartPreviewResponse
    report: BaziReport
    metadata: ReportMetadata
    debug_trace: AgentDebugTrace | None = None


UserRole = Literal["user", "admin"]
UserStatus = Literal["active", "disabled"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@+-]+$")
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr = Field(min_length=8, max_length=128)
    new_password: SecretStr = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def require_different_password(self) -> "ChangePasswordRequest":
        if self.current_password.get_secret_value() == self.new_password.get_secret_value():
            raise ValueError("新密码不能与当前密码相同")
        return self


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str
    role: UserRole
    status: UserStatus
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LoginResponse(BaseModel):
    user: UserResponse


class BootstrapStatusResponse(BaseModel):
    required: bool


class BootstrapAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@+-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("显示名称不能为空")
        return stripped


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@+-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    temporary_password: SecretStr = Field(min_length=8, max_length=128)
    role: UserRole = "user"

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("显示名称不能为空")
        return stripped


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: UserRole | None = None
    status: UserStatus | None = None

    @field_validator("display_name")
    @classmethod
    def strip_optional_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("显示名称不能为空")
        return stripped


class AdminPasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: SecretStr = Field(min_length=8, max_length=128)


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    offset: int
    limit: int
