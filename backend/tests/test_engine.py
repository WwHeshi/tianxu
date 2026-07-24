from datetime import datetime, timedelta
from math import cos, pi, sin

import pytest

from app.bazi import locations
from app.bazi.engine import ChartCalculationError, calculate_chart
from app.schemas import BirthInput

GUANGZHOU_TIANHE: dict[str, str | None] = {
    "country_code": "CN",
    "province_code": "440000",
    "province_name": "广东省",
    "city_code": "440100",
    "city_name": "广州市",
    "district_code": "440106",
    "district_name": "天河区",
}

URUMQI_TIANSHAN: dict[str, str | None] = {
    "country_code": "CN",
    "province_code": "650000",
    "province_name": "新疆维吾尔自治区",
    "city_code": "650100",
    "city_name": "乌鲁木齐市",
    "district_code": "650102",
    "district_name": "天山区",
}

CHONGQING_LIANGJIANG: dict[str, str | None] = {
    "country_code": "CN",
    "province_code": "500000",
    "province_name": "重庆市",
    "city_code": None,
    "city_name": None,
    "district_code": "500157",
    "district_name": "两江新区",
}


def calculate(
    beijing_datetime: str,
    birthplace: dict[str, str | None] = GUANGZHOU_TIANHE,
):
    return calculate_chart(
        BirthInput(
            beijing_datetime=beijing_datetime,
            birthplace=birthplace,
            gender="female",
        )
    )


def noaa_equation_of_time(value: datetime) -> float:
    """Return NOAA's equation-of-time approximation in minutes."""

    hour = value.hour + value.minute / 60 + value.second / 3600 + value.microsecond / 3_600_000_000
    gamma = 2 * pi / 365 * (value.timetuple().tm_yday - 1 + (hour - 12) / 24)
    return 229.18 * (
        0.000075
        + 0.001868 * cos(gamma)
        - 0.032077 * sin(gamma)
        - 0.014615 * cos(2 * gamma)
        - 0.040849 * sin(2 * gamma)
    )


def test_tianhe_longitude_and_noaa_adjustment_are_applied() -> None:
    beijing = datetime(2024, 1, 15, 12, 34, 56)
    result = calculate(beijing.isoformat())
    adjustment = result.solar_time_adjustment

    assert adjustment.longitude_degrees == pytest.approx(113.361597)
    assert adjustment.reference_meridian_degrees == pytest.approx(120)
    expected_longitude_correction = 4 * (113.361597 - 120)
    assert adjustment.longitude_correction_minutes == pytest.approx(expected_longitude_correction)
    assert adjustment.equation_of_time_minutes == pytest.approx(
        noaa_equation_of_time(beijing), abs=1e-6
    )
    expected_total = expected_longitude_correction + noaa_equation_of_time(beijing)
    assert adjustment.total_correction_minutes == pytest.approx(expected_total, abs=1e-6)
    expected_true_solar = beijing + timedelta(minutes=expected_total)
    assert (
        result.normalized_input.true_solar_datetime - expected_true_solar
    ).total_seconds() == pytest.approx(0, abs=0.5)
    assert adjustment.location_precision == "district_center"
    assert adjustment.coordinate_source


def test_official_mca_coordinate_is_used_without_fallback() -> None:
    result = calculate("1990-01-01T12:00:00", CHONGQING_LIANGJIANG)
    adjustment = result.solar_time_adjustment

    assert adjustment.longitude_degrees == pytest.approx(106.562177)
    assert adjustment.latitude_degrees == pytest.approx(29.64553)
    assert adjustment.coordinate_match == "official_mca_api"
    assert adjustment.coordinate_source.startswith("中华人民共和国民政部国家地名信息库@")
    assert not [warning for warning in result.warnings if "回退" in warning]


def test_true_solar_midnight_boundary_changes_day_and_hour_pillars() -> None:
    before_midnight = calculate("2024-01-02T00:29:00")
    after_midnight = calculate("2024-01-02T00:30:00")

    assert before_midnight.normalized_input.true_solar_datetime.date().isoformat() == "2024-01-01"
    assert after_midnight.normalized_input.true_solar_datetime.date().isoformat() == "2024-01-02"
    assert before_midnight.chart.pillars.day.gan_zhi != after_midnight.chart.pillars.day.gan_zhi
    assert before_midnight.chart.pillars.hour.gan_zhi != after_midnight.chart.pillars.hour.gan_zhi


def test_xinjiang_true_solar_time_crosses_date_and_changes_hour_pillar() -> None:
    beijing_datetime = "2024-01-02T01:00:00"
    guangzhou = calculate(beijing_datetime, GUANGZHOU_TIANHE)
    xinjiang = calculate(beijing_datetime, URUMQI_TIANSHAN)

    assert xinjiang.normalized_input.true_solar_datetime.date().isoformat() == "2024-01-01"
    assert guangzhou.normalized_input.true_solar_datetime.date().isoformat() == "2024-01-02"
    assert xinjiang.solar_time_adjustment.longitude_degrees == pytest.approx(87.631986)
    assert xinjiang.solar_time_adjustment.total_correction_minutes < -120
    assert xinjiang.chart.pillars.day.gan_zhi != guangzhou.chart.pillars.day.gan_zhi
    assert xinjiang.chart.pillars.hour.gan_zhi != guangzhou.chart.pillars.hour.gan_zhi


def test_leap_day_is_supported() -> None:
    result = calculate("2024-02-29T08:15:00")

    assert result.normalized_input.beijing_datetime.isoformat() == "2024-02-29T08:15:00"
    assert result.normalized_input.true_solar_datetime.date().isoformat() == "2024-02-29"
    assert len(result.chart.pillars.day.gan_zhi) == 2


def test_legacy_statistical_development_zone_is_rejected() -> None:
    with pytest.raises(ChartCalculationError, match=r"暂不支持.*130171"):
        calculate(
            "1990-01-01T12:00:00",
            {
                "country_code": "CN",
                "province_code": "130000",
                "province_name": "河北省",
                "city_code": "130100",
                "city_name": "石家庄市",
                "district_code": "130171",
                "district_name": "石家庄高新技术产业开发区",
            },
        )


def test_fallback_coordinate_is_rejected_instead_of_used_for_chart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_record = {
        "province_code": "440000",
        "province_name": "广东省",
        "city_code": "440100",
        "city_name": "广州市",
        "district_code": "440106",
        "district_name": "天河区",
        "longitude": 113.264434,
        "latitude": 23.129162,
        "precision": "city_center",
        "coordinate_match": "regional_center_fallback",
        "fallback": True,
        "source_name": "广州市",
    }
    monkeypatch.setattr(
        locations,
        "_load_coordinate_data",
        lambda: {
            "standard_meridian_longitude": 120,
            "coordinate_source": "test-coordinate-source",
            "records": {"440106": fallback_record},
        },
    )

    with pytest.raises(ChartCalculationError, match=r"回退|缺少独立.*坐标"):
        calculate("1990-01-01T12:00:00")


def test_repeated_calculations_are_deterministic() -> None:
    first = calculate("1990-01-01T12:00:00")
    second = calculate("1990-01-01T12:00:00")

    assert first == second
