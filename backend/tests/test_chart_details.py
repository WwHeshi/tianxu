from datetime import datetime

from lunar_python.util import LunarUtil

from app.bazi.engine import calculate_chart
from app.bazi.shen_sha import SHEN_SHA_DISPLAY_ORDER, calculate_shen_sha
from app.schemas import BirthInput


def reference_chart():
    return calculate_chart(
        BirthInput(
            beijing_datetime="2003-04-13T18:57:00",
            gender="male",
        )
    )


def test_reference_chart_matches_complete_pillar_table() -> None:
    result = reference_chart()
    pillars = result.chart.pillars
    ordered = (pillars.year, pillars.month, pillars.day, pillars.hour)

    assert [pillar.gan_zhi for pillar in ordered] == ["癸未", "丙辰", "丙辰", "丁酉"]
    assert [pillar.heavenly_stem.ten_god for pillar in ordered] == [
        "正官",
        "比肩",
        "日主",
        "劫财",
    ]
    assert [[item.symbol for item in pillar.earthly_branch.hidden_stems] for pillar in ordered] == [
        ["己", "丁", "乙"],
        ["戊", "乙", "癸"],
        ["戊", "乙", "癸"],
        ["辛"],
    ]
    assert [
        [item.ten_god for item in pillar.earthly_branch.hidden_stems] for pillar in ordered
    ] == [
        ["伤官", "劫财", "正印"],
        ["食神", "正印", "正官"],
        ["食神", "正印", "正官"],
        ["正财"],
    ]
    assert [pillar.growth_stage for pillar in ordered] == ["衰", "冠带", "冠带", "死"]
    assert [pillar.self_growth_stage for pillar in ordered] == [
        "墓",
        "冠带",
        "冠带",
        "长生",
    ]
    assert [pillar.xun_kong for pillar in ordered] == ["申酉", "子丑", "子丑", "辰巳"]
    assert [pillar.na_yin for pillar in ordered] == [
        "杨柳木",
        "沙中土",
        "沙中土",
        "山下火",
    ]


def test_reference_chart_calendar_summary_is_complete() -> None:
    calendar = reference_chart().chart.calendar

    assert calendar.solar_datetime == datetime(2003, 4, 13, 18, 57)
    assert calendar.lunar_year == 2003
    assert calendar.lunar_month == 3
    assert calendar.lunar_day == 12
    assert calendar.is_leap_month is False
    assert calendar.lunar_text == "2003年三月十二"
    assert calendar.time_branch == "酉"
    assert calendar.zodiac == "羊"
    assert calendar.destiny_type == "乾造"


def test_reference_chart_shen_sha_matches_v2_acceptance_fixture() -> None:
    pillars = {
        "year": "癸未",
        "month": "丙辰",
        "day": "丙辰",
        "hour": "丁酉",
    }

    assert calculate_shen_sha(pillars, gender="male") == {
        "year": ["德秀贵人", "金舆", "流霞"],
        "month": ["德秀贵人", "华盖", "披麻", "寡宿"],
        "day": ["德秀贵人", "华盖", "十灵日", "披麻", "寡宿"],
        "hour": [
            "天乙贵人",
            "太极贵人",
            "天德合",
            "月德合",
            "桃花",
            "丧门",
            "灾煞",
            "空亡",
        ],
    }


def test_every_pillar_detail_is_derived_from_the_final_gan_zhi() -> None:
    result = calculate_chart(
        BirthInput(
            beijing_datetime="2024-01-01T23:30:00",
            gender="female",
        )
    )

    for pillar in (
        result.chart.pillars.year,
        result.chart.pillars.month,
        result.chart.pillars.day,
        result.chart.pillars.hour,
    ):
        assert pillar.xun_kong == LunarUtil.getXunKong(pillar.gan_zhi)
        assert len(pillar.growth_stage) >= 1
        assert len(pillar.self_growth_stage) >= 1
        assert len(pillar.shen_sha) == len(set(pillar.shen_sha))
        assert pillar.shen_sha == sorted(
            pillar.shen_sha,
            key=SHEN_SHA_DISPLAY_ORDER.index,
        )


def test_calendar_summary_uses_the_actual_true_solar_chart_datetime() -> None:
    result = calculate_chart(
        BirthInput(
            beijing_datetime="2024-01-02T00:20:00",
            birthplace={"location_id": "CN:440106"},
            gender="female",
        )
    )

    assert result.chart.calendar.solar_datetime == result.normalized_input.true_solar_datetime
    assert result.chart.calendar.solar_datetime.date().isoformat() == "2024-01-01"
    assert result.chart.calendar.lunar_text == "2023年冬月二十"
