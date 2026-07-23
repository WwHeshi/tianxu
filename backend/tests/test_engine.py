import pytest

from app.bazi.engine import ChartCalculationError, calculate_chart
from app.schemas import BirthInput


def calculate(local_datetime: str):
    return calculate_chart(
        BirthInput(
            local_datetime=local_datetime,
            timezone="Asia/Shanghai",
            gender="female",
        )
    )


def test_midnight_boundary_changes_day_pillar_and_uses_selected_day_for_hour_stem() -> None:
    before_midnight = calculate("1990-01-01T23:59:59")
    at_midnight = calculate("1990-01-02T00:00:00")

    assert before_midnight.chart.pillars.day.gan_zhi == "丙寅"
    assert before_midnight.chart.pillars.hour.gan_zhi == "戊子"
    assert at_midnight.chart.pillars.day.gan_zhi == "丁卯"
    assert at_midnight.chart.pillars.hour.gan_zhi == "庚子"


def test_leap_day_is_supported() -> None:
    result = calculate("2024-02-29T08:15:00")

    assert result.normalized_input.local_datetime.isoformat() == "2024-02-29T08:15:00+08:00"
    assert len(result.chart.pillars.day.gan_zhi) == 2


def test_offset_input_is_converted_to_requested_timezone() -> None:
    result = calculate_chart(
        BirthInput(
            local_datetime="1990-01-01T04:00:00+00:00",
            timezone="Asia/Shanghai",
            gender="other",
        )
    )

    assert result.normalized_input.local_datetime.isoformat() == "1990-01-01T12:00:00+08:00"
    assert result.chart.pillars.day.gan_zhi == "丙寅"
    assert "已转换为所选 IANA 时区" in result.warnings[0]


def test_longitude_is_recorded_but_not_applied_by_v1() -> None:
    without_longitude = calculate("1990-01-01T12:00:00")
    with_longitude = calculate_chart(
        BirthInput(
            local_datetime="1990-01-01T12:00:00",
            timezone="Asia/Shanghai",
            gender="female",
            longitude=121.4737,
        )
    )

    assert with_longitude.chart == without_longitude.chart
    assert with_longitude.normalized_input.longitude == 121.4737
    assert with_longitude.warnings == ["已记录经度，但规则 v1 未启用真太阳时，因此本次未参与计算"]


def test_nonexistent_dst_wall_time_is_rejected() -> None:
    with pytest.raises(ChartCalculationError, match="不存在"):
        calculate_chart(
            BirthInput(
                local_datetime="2024-03-10T02:30:00",
                timezone="America/New_York",
                gender="female",
            )
        )


def test_ambiguous_dst_wall_time_uses_earlier_occurrence_with_warning() -> None:
    result = calculate_chart(
        BirthInput(
            local_datetime="2024-11-03T01:30:00",
            timezone="America/New_York",
            gender="female",
        )
    )

    assert result.normalized_input.utc_datetime.isoformat() == "2024-11-03T05:30:00+00:00"
    assert result.warnings == ["出生时间在夏令时回拨区间内，已采用较早的一次（fold=0）"]
