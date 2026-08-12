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
    assert definition["description"] == "按已校正的真太阳时和性别计算八字四柱原局。"
    assert set(schema["properties"]) == {"gender", "true_solar_datetime"}
    assert set(schema["required"]) == {"gender", "true_solar_datetime"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["gender"]["enum"] == ["male", "female"]
    assert "description" not in schema["properties"]["gender"]
    assert "format" not in schema["properties"]["true_solar_datetime"]
    assert schema["properties"]["true_solar_datetime"]["pattern"] == (
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    )
    assert schema["properties"]["true_solar_datetime"]["description"] == (
        "已校正的真太阳出生时间，不得包含时区或 UTC 偏移。"
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

    observation = tool_result.model_dump(mode="json")
    assert set(observation) == {"年柱", "月柱", "日柱", "时柱"}
    year = observation["年柱"]
    assert set(year) == {
        "主星",
        "天干",
        "地支",
        "藏干",
        "星运",
        "自坐",
        "空亡",
        "纳音",
        "神煞",
    }
    assert year["主星"] == legacy_result.chart.pillars.year.heavenly_stem.ten_god
    assert year["地支"]["本气五行"] == (
        legacy_result.chart.pillars.year.earthly_branch.element
    )
    assert year["天干"]["阴阳"] == "阳"
    assert year["地支"]["阴阳"] == "阳"
    assert "十神" not in year["天干"]
    assert "藏干" not in year["地支"]
    assert set(year["藏干"][0]) == {"字", "五行", "阴阳", "副星"}
    assert year["藏干"][0]["副星"] == (
        legacy_result.chart.pillars.year.earthly_branch.hidden_stems[0].ten_god
    )
    assert year["星运"] == legacy_result.chart.pillars.year.growth_stage
    assert year["自坐"] == (
        legacy_result.chart.pillars.year.self_growth_stage
    )
    assert year["空亡"] == list(legacy_result.chart.pillars.year.xun_kong)
    assert "name" not in year
    assert "growth_stage" not in year
    assert "self_growth_stage" not in year
    assert "xun_kong" not in year
    assert legacy_result.chart.fortune_cycles is not None
    assert tuple(
        pillar.heavenly_stem.symbol + pillar.earthly_branch.symbol
        for pillar in (
            tool_result.year,
            tool_result.month,
            tool_result.day,
            tool_result.hour,
        )
    ) == ("甲寅", "戊辰", "己亥", "壬申")
