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
    assert schema["properties"]["gender"]["enum"] == ["male", "female"]
    assert schema["properties"]["true_solar_datetime"]["description"] == (
        "已完成校正的真太阳时，格式为 YYYY-MM-DDTHH:mm:ss，工具不再换算。"
    )


def test_chart_tool_rejects_timezone_and_unknown_inputs() -> None:
    with pytest.raises(ValidationError, match="gender"):
        BaziChartToolInput(
            gender="other",
            true_solar_datetime="1974-04-28T15:45:32",
        )

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


def test_chart_tool_returns_natal_chart_without_fortune_cycles() -> None:
    payload = BaziChartToolInput(
        gender="male",
        true_solar_datetime="1974-04-28T16:40:00",
    )

    tool_result = run_bazi_chart_tool(payload)
    legacy_result = calculate_chart(
        BirthInput(beijing_datetime="1974-04-28T16:40:00", gender="male")
    )

    assert set(tool_result.model_dump()) == {"pillars"}
    year = tool_result.pillars.year.model_dump()
    assert set(year) == {
        "gan_zhi",
        "heavenly_stem",
        "earthly_branch",
        "day_master_growth_stage",
        "pillar_stem_growth_stage",
        "xun_kong_branches",
        "na_yin",
        "shen_sha",
    }
    assert year["earthly_branch"]["primary_element"] == (
        legacy_result.chart.pillars.year.earthly_branch.element
    )
    assert "element" not in year["earthly_branch"]
    assert year["day_master_growth_stage"] == legacy_result.chart.pillars.year.growth_stage
    assert year["pillar_stem_growth_stage"] == (
        legacy_result.chart.pillars.year.self_growth_stage
    )
    assert year["xun_kong_branches"] == list(legacy_result.chart.pillars.year.xun_kong)
    assert "name" not in year
    assert "growth_stage" not in year
    assert "self_growth_stage" not in year
    assert "xun_kong" not in year
    assert legacy_result.chart.fortune_cycles is not None
    assert tuple(
        pillar.gan_zhi
        for pillar in (
            tool_result.pillars.year,
            tool_result.pillars.month,
            tool_result.pillars.day,
            tool_result.pillars.hour,
        )
    ) == ("甲寅", "戊辰", "己亥", "壬申")
