import pytest
from pydantic import ValidationError

from app.bazi.engine import calculate_chart
from app.bazi.tool import (
    BAZI_CHART_TOOL_NAME,
    BaziChartToolInput,
    bazi_chart_tool_definition,
    run_bazi_chart_tool,
)
from app.schemas import BirthInput


def test_chart_tool_has_only_normalized_time_and_gender_inputs() -> None:
    definition = bazi_chart_tool_definition()
    schema = definition["input_schema"]

    assert definition["name"] == BAZI_CHART_TOOL_NAME
    assert set(schema["properties"]) == {"gender", "true_solar_datetime"}
    assert set(schema["required"]) == {"gender", "true_solar_datetime"}
    assert schema["additionalProperties"] is False


def test_chart_tool_rejects_timezone_and_unknown_inputs() -> None:
    with pytest.raises(ValidationError, match="真太阳时间不得附带时区"):
        BaziChartToolInput(
            gender="male",
            true_solar_datetime="1974-04-28T15:45:32+08:00",
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BaziChartToolInput.model_validate(
            {
                "gender": "male",
                "true_solar_datetime": "1974-04-28T15:45:32",
                "birth_location": "USA",
            }
        )


def test_chart_tool_preserves_existing_engine_result() -> None:
    payload = BaziChartToolInput(
        gender="male",
        true_solar_datetime="1974-04-28T16:40:00",
    )

    tool_result = run_bazi_chart_tool(payload)
    legacy_result = calculate_chart(
        BirthInput(beijing_datetime="1974-04-28T16:40:00", gender="male")
    )

    assert tool_result.chart == legacy_result.chart
    assert tool_result.engine == legacy_result.engine
    assert tuple(
        pillar.gan_zhi
        for pillar in (
            tool_result.chart.pillars.year,
            tool_result.chart.pillars.month,
            tool_result.chart.pillars.day,
            tool_result.chart.pillars.hour,
        )
    ) == ("甲寅", "戊辰", "己亥", "壬申")
