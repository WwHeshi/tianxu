from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.agent_tools import AgentToolRegistry
from app.bazi.engine import calculate_chart
from app.bazi.fortune import FortuneAtRangeError, select_fortune_at
from app.bazi.fortune_tool import (
    FORTUNE_AT_TOOL_NAME,
    FortuneAtToolInput,
    fortune_at_agent_tool,
    fortune_at_tool_definition,
    run_fortune_at_tool,
)
from app.schemas import BirthInput

BIRTH = datetime(2003, 4, 13, 18, 57)


def reference_cycles():
    cycles = calculate_chart(
        BirthInput(beijing_datetime=BIRTH, gender="male")
    ).chart.fortune_cycles
    assert cycles is not None
    return cycles


def test_fortune_tool_exposes_self_contained_birth_and_query_inputs() -> None:
    definition = fortune_at_tool_definition()
    schema = definition["input_schema"]

    assert definition["name"] == FORTUNE_AT_TOOL_NAME
    assert definition["description"] == (
        "根据性别和已校正的真太阳出生时间，查询指定北京时间点对应的大运、流年和流月。"
    )
    assert set(schema["properties"]) == {
        "gender",
        "true_solar_datetime",
        "as_of_datetime",
    }
    assert schema["required"] == [
        "gender",
        "true_solar_datetime",
        "as_of_datetime",
    ]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["gender"]["enum"] == ["male", "female"]
    assert "真太阳" in schema["properties"]["true_solar_datetime"]["description"]
    assert "北京时间" in schema["properties"]["as_of_datetime"]["description"]


def test_fortune_tool_input_rejects_timezone_and_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="不得附带时区"):
        FortuneAtToolInput(
            gender="male",
            true_solar_datetime=BIRTH,
            as_of_datetime="2026-08-13T12:00:00+08:00",
        )
    with pytest.raises(ValidationError, match="真太阳出生时间不得附带时区"):
        FortuneAtToolInput(
            gender="male",
            true_solar_datetime="2003-04-13T18:57:00+08:00",
            as_of_datetime="2026-08-13T12:00:00",
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FortuneAtToolInput.model_validate(
            {
                "gender": "female",
                "true_solar_datetime": "2003-04-13T18:57:00",
                "as_of_datetime": "2026-08-13T12:00:00",
                "birthplace": "Guangzhou",
            }
        )


def test_fortune_tool_returns_only_one_big_luck_year_and_month() -> None:
    result = run_fortune_at_tool(
        FortuneAtToolInput(
            gender="male",
            true_solar_datetime=BIRTH,
            as_of_datetime="2026-08-13T12:00:00",
        )
    )
    observation = result.model_dump(mode="json")

    assert observation == {
        "大运": {
            "状态": "行运中",
            "干支": "癸丑",
            "天干十神": "正官",
            "地支本气十神": "伤官",
        },
        "流年": {
            "年份": 2026,
            "干支": "丙午",
            "天干十神": "比肩",
            "地支本气十神": "劫财",
        },
        "流月": {
            "交界节气": "立秋",
            "干支": "丙申",
            "天干十神": "比肩",
            "地支本气十神": "偏财",
        },
    }
    assert "big_luck_periods" not in str(observation)
    assert "years" not in str(observation)
    assert "months" not in str(observation)


def test_fortune_tool_uses_supplied_gender_for_big_luck_direction() -> None:
    male = run_fortune_at_tool(
        FortuneAtToolInput(
            gender="male",
            true_solar_datetime=BIRTH,
            as_of_datetime="2026-08-13T12:00:00",
        )
    )
    female = run_fortune_at_tool(
        FortuneAtToolInput(
            gender="female",
            true_solar_datetime=BIRTH,
            as_of_datetime="2026-08-13T12:00:00",
        )
    )

    assert male.big_luck.gan_zhi == "癸丑"
    assert female.big_luck.gan_zhi == "戊午"


def test_fortune_tool_represents_before_start_without_a_big_luck_pillar() -> None:
    result = run_fortune_at_tool(
        FortuneAtToolInput(
            gender="male",
            true_solar_datetime=BIRTH,
            as_of_datetime=BIRTH,
        )
    )

    assert result.model_dump(mode="json")["大运"] == {
        "状态": "起运前",
        "干支": None,
        "天干十神": None,
        "地支本气十神": None,
    }


def test_selection_uses_half_open_boundaries_at_exact_big_luck_transition() -> None:
    cycles = reference_cycles()
    transition = datetime(2026, 1, 14, 4, 57)

    before = select_fortune_at(cycles, transition - timedelta(seconds=1))
    after = select_fortune_at(cycles, transition)

    assert before.big_luck.index == 2
    assert before.big_luck.pillar is not None
    assert before.big_luck.pillar.gan_zhi == "甲寅"
    assert after.big_luck.index == 3
    assert after.big_luck.pillar is not None
    assert after.big_luck.pillar.gan_zhi == "癸丑"
    assert before.annual.year == after.annual.year == 2025
    assert before.monthly.solar_term == after.monthly.solar_term == "小寒"


def test_selection_uses_new_flow_year_and_month_at_exact_solar_term() -> None:
    cycles = reference_cycles()
    period = cycles.big_luck_periods[3]
    annual_2026 = next(item for item in period.years if item.year == 2026)
    li_chun = annual_2026.segment_start_solar_datetime
    jing_zhe = annual_2026.months[1].segment_start_solar_datetime

    before_li_chun = select_fortune_at(cycles, li_chun - timedelta(seconds=1))
    at_li_chun = select_fortune_at(cycles, li_chun)
    before_jing_zhe = select_fortune_at(cycles, jing_zhe - timedelta(seconds=1))
    at_jing_zhe = select_fortune_at(cycles, jing_zhe)

    assert before_li_chun.annual.year == 2025
    assert at_li_chun.annual.year == 2026
    assert before_li_chun.monthly.solar_term == "小寒"
    assert at_li_chun.monthly.solar_term == "立春"
    assert before_jing_zhe.monthly.solar_term == "立春"
    assert at_jing_zhe.monthly.solar_term == "惊蛰"


def test_selection_rejects_dates_outside_the_generated_timeline() -> None:
    cycles = reference_cycles()

    with pytest.raises(FortuneAtRangeError, match="不在可计算范围"):
        select_fortune_at(cycles, BIRTH - timedelta(seconds=1))
    with pytest.raises(FortuneAtRangeError, match="不在可计算范围"):
        select_fortune_at(cycles, cycles.big_luck_periods[-1].end_solar_datetime)


def test_fortune_agent_tool_dispatches_self_contained_arguments() -> None:
    registry = AgentToolRegistry([fortune_at_agent_tool()])

    definition = registry.definitions("responses")[0]
    dispatched = registry.dispatch(
        FORTUNE_AT_TOOL_NAME,
        (
            '{"gender":"male","true_solar_datetime":"2003-04-13T18:57:00",'
            '"as_of_datetime":"2026-08-13T12:00:00"}'
        ),
    )

    assert set(definition["parameters"]["properties"]) == {
        "gender",
        "true_solar_datetime",
        "as_of_datetime",
    }
    assert dispatched.input == {
        "gender": "male",
        "true_solar_datetime": "2003-04-13T18:57:00",
        "as_of_datetime": "2026-08-13T12:00:00",
    }
    assert set(dispatched.output) == {"大运", "流年", "流月"}
