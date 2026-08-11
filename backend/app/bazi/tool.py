"""Stable tool boundary for deterministic BaZi chart calculation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..schemas import BirthInput, ChartPreviewResponse, Gender
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
                    "enum": ["male", "female", "other"],
                },
                "true_solar_datetime": {
                    "type": "string",
                    "format": "date-time",
                    "description": "不带时区或 UTC 偏移的真太阳当地时间。",
                },
            },
            "required": ["gender", "true_solar_datetime"],
            "additionalProperties": False,
        },
    }


def run_bazi_chart_tool(payload: BaziChartToolInput) -> ChartPreviewResponse:
    """Run the existing engine without performing another time correction.

    ``calculate_chart`` already treats a birthplace-free clock value as the
    exact wall-clock components used by lunar-python. Passing the normalized
    true-solar value through that path preserves the existing pillar, luck
    cycle, sect and boundary behavior while giving callers a smaller contract.
    """

    return calculate_chart(
        BirthInput(
            beijing_datetime=payload.true_solar_datetime,
            gender=payload.gender,
        )
    )
