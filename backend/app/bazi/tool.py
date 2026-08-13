"""Stable tool boundary for deterministic BaZi chart calculation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent_tools import AgentTool, AgentToolAuthorizationError
from ..schemas import (
    BirthInput,
    Gender,
    Pillar,
)
from .engine import calculate_chart

BAZI_CHART_TOOL_NAME = "calculate_bazi_chart"
BAZI_CHART_TOOL_DESCRIPTION = "按已校正的真太阳时和性别计算八字四柱原局。"


class BaziChartToolInput(BaseModel):
    """Minimal, normalized input accepted by the chart tool."""

    model_config = ConfigDict(extra="forbid")

    gender: Gender
    true_solar_datetime: datetime

    @field_validator("true_solar_datetime")
    @classmethod
    def require_naive_true_solar_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError("真太阳时间不得附带时区或 UTC 偏移")
        return value


class BaziChartToolStem(BaseModel):
    """Chinese-keyed heavenly stem exposed to Agent models."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    symbol: str = Field(serialization_alias="字")
    element: str = Field(serialization_alias="五行")
    polarity: Literal["阳", "阴"] = Field(serialization_alias="阴阳")


class BaziChartToolHiddenStem(BaziChartToolStem):
    """One hidden stem paired with its corresponding secondary star."""

    secondary_star: str | None = Field(default=None, serialization_alias="副星")


class BaziChartToolEarthlyBranch(BaseModel):
    """Earthly-branch facts with an explicit primary-element label."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    symbol: str = Field(serialization_alias="字")
    primary_element: str = Field(serialization_alias="本气五行")
    polarity: Literal["阳", "阴"] = Field(serialization_alias="阴阳")


class BaziChartToolPillar(BaseModel):
    """Unambiguous pillar shape exposed to Agent models."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    main_star: str | None = Field(default=None, serialization_alias="主星")
    heavenly_stem: BaziChartToolStem = Field(serialization_alias="天干")
    earthly_branch: BaziChartToolEarthlyBranch = Field(serialization_alias="地支")
    hidden_stems: list[BaziChartToolHiddenStem] = Field(serialization_alias="藏干")
    day_master_growth_stage: str = Field(serialization_alias="星运")
    pillar_stem_growth_stage: str = Field(serialization_alias="自坐")
    xun_kong_branches: list[str] = Field(serialization_alias="空亡")
    na_yin: str = Field(serialization_alias="纳音")
    shen_sha: list[str] = Field(serialization_alias="神煞")


class BaziChartToolResult(BaseModel):
    """Final Agent observation containing only natal-chart facts."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    year: BaziChartToolPillar = Field(serialization_alias="年柱")
    month: BaziChartToolPillar = Field(serialization_alias="月柱")
    day: BaziChartToolPillar = Field(serialization_alias="日柱")
    hour: BaziChartToolPillar = Field(serialization_alias="时柱")


def _tool_stem(component: Any) -> BaziChartToolStem:
    return BaziChartToolStem(
        symbol=component.symbol,
        element=component.element,
        polarity="阳" if component.polarity == "yang" else "阴",
    )


def _tool_hidden_stem(component: Any) -> BaziChartToolHiddenStem:
    return BaziChartToolHiddenStem(
        symbol=component.symbol,
        element=component.element,
        polarity="阳" if component.polarity == "yang" else "阴",
        secondary_star=component.ten_god,
    )


def _tool_pillar(pillar: Pillar) -> BaziChartToolPillar:
    branch = pillar.earthly_branch
    return BaziChartToolPillar(
        main_star=pillar.heavenly_stem.ten_god,
        heavenly_stem=_tool_stem(pillar.heavenly_stem),
        earthly_branch=BaziChartToolEarthlyBranch(
            symbol=branch.symbol,
            primary_element=branch.element,
            polarity="阳" if branch.polarity == "yang" else "阴",
        ),
        hidden_stems=[_tool_hidden_stem(stem) for stem in branch.hidden_stems],
        day_master_growth_stage=pillar.growth_stage,
        pillar_stem_growth_stage=pillar.self_growth_stage,
        xun_kong_branches=list(pillar.xun_kong),
        na_yin=pillar.na_yin,
        shen_sha=pillar.shen_sha,
    )


def bazi_chart_tool_definition() -> dict[str, Any]:
    """Return a provider-neutral function-tool definition."""

    return {
        "name": BAZI_CHART_TOOL_NAME,
        "description": BAZI_CHART_TOOL_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "gender": {
                    "type": "string",
                    "enum": ["male", "female"],
                },
                "true_solar_datetime": {
                    "type": "string",
                    "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",
                    "description": "已校正的真太阳出生时间，不得包含时区或 UTC 偏移。",
                },
            },
            "required": ["gender", "true_solar_datetime"],
            "additionalProperties": False,
        },
    }


def run_bazi_chart_tool(payload: BaziChartToolInput) -> BaziChartToolResult:
    """Calculate the natal chart without time correction or fortune cycles.

    ``calculate_chart`` already treats a birthplace-free clock value as the
    exact wall-clock components used by lunar-python. Passing the normalized
    true-solar value through that path preserves the existing pillar, sect and
    boundary behavior while giving Agent callers a smaller contract.
    """

    result = calculate_chart(
        BirthInput(
            beijing_datetime=payload.true_solar_datetime,
            gender=payload.gender,
        ),
        include_fortune_cycles=False,
    )
    return BaziChartToolResult(
        year=_tool_pillar(result.chart.pillars.year),
        month=_tool_pillar(result.chart.pillars.month),
        day=_tool_pillar(result.chart.pillars.day),
        hour=_tool_pillar(result.chart.pillars.hour),
    )


def bazi_chart_agent_tool(
    expected_input: BaziChartToolInput,
    *,
    execute_tool: Callable[[BaziChartToolInput], BaziChartToolResult] = run_bazi_chart_tool,
) -> AgentTool:
    """Bind the authoritative birth input for one Agent invocation."""

    definition = bazi_chart_tool_definition()

    def authorize(payload: BaseModel) -> None:
        if payload != expected_input:
            raise AgentToolAuthorizationError("模型擅自修改了排盘工具参数，已拒绝执行。")

    def execute(payload: BaseModel) -> BaseModel:
        if not isinstance(payload, BaziChartToolInput):  # pragma: no cover - registry invariant
            raise TypeError("calculate_bazi_chart received an unexpected input model")
        return execute_tool(payload)

    return AgentTool(
        name=definition["name"],
        description=definition["description"],
        input_schema=definition["input_schema"],
        input_model=BaziChartToolInput,
        execute=execute,
        authorize=authorize,
    )
