"""Stable tool boundary for deterministic BaZi chart calculation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..schemas import (
    BirthInput,
    Component,
    Gender,
    HiddenStem,
    Pillar,
)
from .engine import calculate_chart

BAZI_CHART_TOOL_NAME = "calculate_bazi_chart"
BAZI_CHART_TOOL_DESCRIPTION = (
    "根据已经换算完成的真太阳出生时间和性别生成确定性的八字命盘。"
    "工具不会再次换算地点、时区或真太阳时。"
)


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


class BaziChartToolEarthlyBranch(BaseModel):
    """Earthly-branch facts with an explicit primary-element label."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    primary_element: str
    polarity: Literal["yang", "yin"]
    hidden_stems: list[HiddenStem]


class BaziChartToolPillar(BaseModel):
    """Unambiguous pillar shape exposed to Agent models."""

    model_config = ConfigDict(extra="forbid")

    gan_zhi: str
    heavenly_stem: Component
    earthly_branch: BaziChartToolEarthlyBranch
    day_master_growth_stage: str
    pillar_stem_growth_stage: str
    xun_kong_branches: list[str]
    na_yin: str
    shen_sha: list[str]


class BaziChartToolPillars(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: BaziChartToolPillar
    month: BaziChartToolPillar
    day: BaziChartToolPillar
    hour: BaziChartToolPillar


class BaziChartToolResult(BaseModel):
    """Final Agent observation containing only natal-chart facts."""

    model_config = ConfigDict(extra="forbid")

    pillars: BaziChartToolPillars


def _tool_pillar(pillar: Pillar) -> BaziChartToolPillar:
    branch = pillar.earthly_branch
    return BaziChartToolPillar(
        gan_zhi=pillar.gan_zhi,
        heavenly_stem=pillar.heavenly_stem,
        earthly_branch=BaziChartToolEarthlyBranch(
            symbol=branch.symbol,
            primary_element=branch.element,
            polarity=branch.polarity,
            hidden_stems=branch.hidden_stems,
        ),
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
                    "format": "date-time",
                    "description": (
                        "已完成校正的真太阳时，格式为 YYYY-MM-DDTHH:mm:ss，"
                        "工具不再换算。"
                    ),
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
        pillars=BaziChartToolPillars(
            year=_tool_pillar(result.chart.pillars.year),
            month=_tool_pillar(result.chart.pillars.month),
            day=_tool_pillar(result.chart.pillars.day),
            hour=_tool_pillar(result.chart.pillars.hour),
        ),
    )
