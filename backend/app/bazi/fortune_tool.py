"""Agent tool returning only the fortune segments active at a requested instant."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent_tools import AgentTool
from ..schemas import BirthInput, FortunePillar, Gender
from .engine import calculate_chart
from .fortune import FortuneAtSelection, select_fortune_at

FORTUNE_AT_TOOL_NAME = "calculate_fortune_at"
FORTUNE_AT_TOOL_DESCRIPTION = (
    "根据性别和已校正的真太阳出生时间，查询指定北京时间点对应的大运、流年和流月。"
)


class FortuneAtToolInput(BaseModel):
    """Self-contained birth facts and query instant supplied by the caller."""

    model_config = ConfigDict(extra="forbid")

    gender: Gender
    true_solar_datetime: datetime
    as_of_datetime: datetime

    @field_validator("true_solar_datetime")
    @classmethod
    def require_naive_true_solar_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError("真太阳出生时间不得附带时区或 UTC 偏移")
        return value

    @field_validator("as_of_datetime")
    @classmethod
    def require_naive_beijing_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError("查询时点须使用北京时间钟表值，不得附带时区或 UTC 偏移")
        return value


class FortuneAtToolBigLuck(BaseModel):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    status: Literal["起运前", "行运中"] = Field(serialization_alias="状态")
    gan_zhi: str | None = Field(serialization_alias="干支")
    heavenly_stem_ten_god: str | None = Field(serialization_alias="天干十神")
    earthly_branch_main_qi_ten_god: str | None = Field(
        serialization_alias="地支本气十神"
    )


class FortuneAtToolAnnual(BaseModel):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    year: int = Field(serialization_alias="年份")
    nominal_age_sui: int = Field(serialization_alias="虚岁")
    gan_zhi: str = Field(serialization_alias="干支")
    heavenly_stem_ten_god: str | None = Field(serialization_alias="天干十神")
    earthly_branch_main_qi_ten_god: str | None = Field(
        serialization_alias="地支本气十神"
    )


class FortuneAtToolMonthly(BaseModel):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    boundary_solar_term: str = Field(serialization_alias="交界节气")
    gan_zhi: str = Field(serialization_alias="干支")
    heavenly_stem_ten_god: str | None = Field(serialization_alias="天干十神")
    earthly_branch_main_qi_ten_god: str | None = Field(
        serialization_alias="地支本气十神"
    )


class FortuneAtToolResult(BaseModel):
    """A deliberately narrow observation with no neighboring timeline entries."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    big_luck: FortuneAtToolBigLuck = Field(serialization_alias="大运")
    annual: FortuneAtToolAnnual = Field(serialization_alias="流年")
    monthly: FortuneAtToolMonthly = Field(serialization_alias="流月")


def _pillar_values(value: FortunePillar | None) -> tuple[str | None, str | None, str | None]:
    if value is None:
        return None, None, None
    return (
        value.gan_zhi,
        value.heavenly_stem.ten_god,
        value.earthly_branch.ten_god,
    )


def _tool_result(selection: FortuneAtSelection) -> FortuneAtToolResult:
    period = selection.big_luck
    annual = selection.annual
    monthly = selection.monthly
    big_luck_gan_zhi, big_luck_stem_ten_god, big_luck_branch_ten_god = _pillar_values(
        period.pillar
    )
    return FortuneAtToolResult(
        big_luck=FortuneAtToolBigLuck(
            status="起运前" if period.is_before_start else "行运中",
            gan_zhi=big_luck_gan_zhi,
            heavenly_stem_ten_god=big_luck_stem_ten_god,
            earthly_branch_main_qi_ten_god=big_luck_branch_ten_god,
        ),
        annual=FortuneAtToolAnnual(
            year=annual.year,
            nominal_age_sui=annual.nominal_age,
            gan_zhi=annual.pillar.gan_zhi,
            heavenly_stem_ten_god=annual.pillar.heavenly_stem.ten_god,
            earthly_branch_main_qi_ten_god=annual.pillar.earthly_branch.ten_god,
        ),
        monthly=FortuneAtToolMonthly(
            boundary_solar_term=monthly.solar_term,
            gan_zhi=monthly.pillar.gan_zhi,
            heavenly_stem_ten_god=monthly.pillar.heavenly_stem.ten_god,
            earthly_branch_main_qi_ten_god=monthly.pillar.earthly_branch.ten_god,
        ),
    )


def fortune_at_tool_definition() -> dict[str, Any]:
    return {
        "name": FORTUNE_AT_TOOL_NAME,
        "description": FORTUNE_AT_TOOL_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "gender": {
                    "type": "string",
                    "enum": ["male", "female"],
                    "description": "命主性别，用于确定大运顺逆。",
                },
                "true_solar_datetime": {
                    "type": "string",
                    "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",
                    "description": "已校正的真太阳出生时间，不得包含时区或 UTC 偏移。",
                },
                "as_of_datetime": {
                    "type": "string",
                    "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",
                    "description": (
                        "查询时点，使用北京时间（Asia/Shanghai）的无时区钟表时间。"
                    ),
                }
            },
            "required": ["gender", "true_solar_datetime", "as_of_datetime"],
            "additionalProperties": False,
        },
    }


def run_fortune_at_tool(
    payload: FortuneAtToolInput,
) -> FortuneAtToolResult:
    """Reuse the complete deterministic calculation, then strictly select one instant."""

    chart = calculate_chart(
        BirthInput(
            beijing_datetime=payload.true_solar_datetime,
            gender=payload.gender,
        )
    )
    cycles = chart.chart.fortune_cycles
    if cycles is None:  # pragma: no cover - calculate_chart default invariant
        raise ValueError("运势时间线未生成")
    return _tool_result(select_fortune_at(cycles, payload.as_of_datetime))


def fortune_at_agent_tool() -> AgentTool:
    """Register the self-contained point-in-time fortune tool."""

    definition = fortune_at_tool_definition()

    def execute(payload: BaseModel) -> BaseModel:
        if not isinstance(payload, FortuneAtToolInput):  # pragma: no cover - registry invariant
            raise TypeError("calculate_fortune_at received an unexpected input model")
        return run_fortune_at_tool(payload)

    return AgentTool(
        name=definition["name"],
        description=definition["description"],
        input_schema=definition["input_schema"],
        input_model=FortuneAtToolInput,
        execute=execute,
    )
