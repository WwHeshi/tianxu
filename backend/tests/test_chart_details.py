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


def test_reference_chart_fortune_cycles_match_the_selected_policy() -> None:
    cycles = reference_chart().chart.fortune_cycles

    assert cycles is not None
    assert cycles.policy_version == "v1"
    assert cycles.direction == "backward"
    assert cycles.start_offset.model_dump() == {
        "years": 2,
        "months": 9,
        "days": 0,
        "hours": 10,
    }
    assert cycles.start_solar_datetime == datetime(2006, 1, 14, 4, 57)
    assert len(cycles.big_luck_periods) == 10

    before_start, first, second, current = cycles.big_luck_periods[:4]
    assert (
        before_start.is_before_start,
        before_start.start_year,
        before_start.end_year,
        before_start.start_nominal_age,
        before_start.end_nominal_age,
        before_start.pillar,
    ) == (True, 2003, 2005, 1, 3, None)
    assert [
        (
            period.start_year,
            period.end_year,
            period.start_nominal_age,
            period.end_nominal_age,
            period.pillar.gan_zhi,
        )
        for period in (first, second, current)
        if period.pillar is not None
    ] == [
        (2005, 2015, 3, 13, "乙卯"),
        (2015, 2025, 13, 23, "甲寅"),
        (2025, 2035, 23, 33, "癸丑"),
    ]
    assert before_start.start_solar_datetime == datetime(2003, 4, 13, 18, 57)
    assert before_start.end_solar_datetime == datetime(2006, 1, 14, 4, 57)
    assert first.start_solar_datetime == datetime(2006, 1, 14, 4, 57)
    assert first.end_solar_datetime == datetime(2016, 1, 14, 4, 57)
    assert second.start_solar_datetime == datetime(2016, 1, 14, 4, 57)
    assert current.start_solar_datetime == datetime(2026, 1, 14, 4, 57)
    assert before_start.years[0].transition_phase is None
    assert before_start.years[-1].transition_phase == "before"
    assert first.years[0].transition_phase == "after"

    assert len(first.years) == len(second.years) == len(current.years) == 11

    before_transition = second.years[-1]
    after_transition = current.years[0]
    assert before_transition.year == after_transition.year == 2025
    assert (
        after_transition.nominal_age,
        after_transition.pillar.gan_zhi,
        after_transition.pillar.heavenly_stem.ten_god,
        after_transition.pillar.earthly_branch.ten_god,
    ) == (23, "乙巳", "正印", "比肩")

    expected_transition = {
        "solar_datetime": datetime(2026, 1, 14, 4, 57),
        "from_index": 2,
        "from_gan_zhi": "甲寅",
        "to_index": 3,
        "to_gan_zhi": "癸丑",
    }
    assert before_transition.transition_phase == "before"
    assert after_transition.transition_phase == "after"
    assert before_transition.transition is not None
    assert after_transition.transition is not None
    assert before_transition.transition.model_dump() == expected_transition
    assert after_transition.transition.model_dump() == expected_transition
    assert before_transition.big_luck_index_at_start == 2
    assert before_transition.big_luck_gan_zhi_at_start == "甲寅"
    assert after_transition.big_luck_index_at_start == 3
    assert after_transition.big_luck_gan_zhi_at_start == "癸丑"
    assert before_transition.segment_start_solar_datetime == datetime(
        2025, 2, 3, 22, 10, 28
    )
    assert before_transition.segment_end_solar_datetime == datetime(2026, 1, 14, 4, 57)
    assert after_transition.segment_start_solar_datetime == datetime(2026, 1, 14, 4, 57)
    assert after_transition.segment_end_solar_datetime == datetime(2026, 2, 4, 4, 2, 8)

    assert len(before_transition.months) == 12
    assert (
        before_transition.months[0].solar_term,
        before_transition.months[0].start_solar_datetime,
        before_transition.months[0].pillar.gan_zhi,
    ) == ("立春", datetime(2025, 2, 3, 22, 10, 28), "戊寅")
    assert all(
        month.big_luck_gan_zhi_at_start == "甲寅"
        for month in before_transition.months
    )
    assert all(month.transition is None for month in before_transition.months[:-1])

    before_month = before_transition.months[-1]
    after_month = after_transition.months[0]
    assert before_month.solar_term == after_month.solar_term == "小寒"
    assert before_month.index == after_month.index == 12
    assert before_month.transition_phase == "before"
    assert after_month.transition_phase == "after"
    assert before_month.transition == before_transition.transition
    assert after_month.transition == after_transition.transition
    assert before_month.segment_start_solar_datetime == datetime(2026, 1, 5, 16, 23, 10)
    assert before_month.segment_end_solar_datetime == datetime(2026, 1, 14, 4, 57)
    assert after_month.segment_start_solar_datetime == datetime(2026, 1, 14, 4, 57)
    assert after_month.segment_end_solar_datetime == datetime(2026, 2, 4, 4, 2, 8)
    assert after_month.big_luck_gan_zhi_at_start == "癸丑"

    following_annual = current.years[1]
    assert following_annual.year == 2026
    assert following_annual.big_luck_index_at_start == 3
    assert following_annual.big_luck_gan_zhi_at_start == "癸丑"
    assert following_annual.transition is None


def test_nominal_age_uses_the_birth_flow_year_before_li_chun() -> None:
    cycles = calculate_chart(
        BirthInput(beijing_datetime="1990-01-01T12:00:00", gender="male")
    ).chart.fortune_cycles

    assert cycles is not None
    before_start, first = cycles.big_luck_periods[:2]
    assert [
        (annual.year, annual.nominal_age)
        for annual in before_start.years[:3]
    ] == [(1989, 1), (1990, 2), (1991, 3)]
    assert (
        before_start.start_nominal_age,
        before_start.end_nominal_age,
        first.start_nominal_age,
    ) == (1, 10, 10)


def test_fortune_direction_uses_gender_and_year_stem_polarity() -> None:
    male = reference_chart().chart.fortune_cycles
    female = calculate_chart(
        BirthInput(beijing_datetime="2003-04-13T18:57:00", gender="female")
    ).chart.fortune_cycles

    assert male is not None and female is not None
    assert male.direction == "backward"
    assert female.direction == "forward"
    assert male.big_luck_periods[1].pillar is not None
    assert female.big_luck_periods[1].pillar is not None
    assert male.big_luck_periods[1].pillar.gan_zhi == "乙卯"
    assert female.big_luck_periods[1].pillar.gan_zhi == "丁巳"


def test_other_gender_does_not_guess_a_big_luck_direction() -> None:
    result = calculate_chart(
        BirthInput(beijing_datetime="2003-04-13T18:57:00", gender="other")
    )

    assert result.chart.fortune_cycles is None


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
