from datetime import datetime, timedelta
from math import cos, pi, sin
from zoneinfo import ZoneInfo

import pytest

from app.bazi import locations
from app.bazi.engine import ChartCalculationError, calculate_chart
from app.schemas import BirthInput

GUANGZHOU_TIANHE = "CN:440106"
URUMQI_TIANSHAN = "CN:650102"
CHONGQING_LIANGJIANG = "CN:500157"


def calculate(
    beijing_datetime: str,
    birthplace: str = GUANGZHOU_TIANHE,
):
    return calculate_chart(
        BirthInput(
            beijing_datetime=beijing_datetime,
            birthplace={"location_id": birthplace},
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


@pytest.mark.parametrize(
    ("location_id", "region_code", "timezone", "path_names", "longitude"),
    [
        (
            "CN-HK:DCD:A",
            "CN-HK",
            "Asia/Hong_Kong",
            ["香港特别行政区", "中西区"],
            114.15491485,
        ),
        (
            "CN-MO:AREA:01",
            "CN-MO",
            "Asia/Macau",
            ["澳门特别行政区", "花地玛堂区"],
            113.54537,
        ),
        (
            "CN-TW:TOWN:10014020",
            "CN-TW",
            "Asia/Taipei",
            ["台湾地区", "台东县", "成功镇"],
            121.35410384,
        ),
    ],
)
def test_special_region_locations_use_beijing_time_and_canonical_metadata(
    location_id: str,
    region_code: str,
    timezone: str,
    path_names: list[str],
    longitude: float,
) -> None:
    result = calculate("1990-01-01T12:00:00", location_id)
    normalized_location = result.normalized_input.birthplace

    assert result.normalized_input.beijing_datetime == datetime(1990, 1, 1, 12)
    assert normalized_location.location_id == location_id
    assert normalized_location.region_code == region_code
    assert normalized_location.timezone == timezone
    assert [item.name for item in normalized_location.division_path] == path_names
    assert result.solar_time_adjustment.longitude_degrees == pytest.approx(longitude)
    assert result.solar_time_adjustment.reference_meridian_degrees == 120
    assert "仅作为元数据，不参与换算" in result.engine.solar_time_note
    assert not [warning for warning in result.warnings if "回退" in warning]


@pytest.mark.parametrize(
    ("location_id", "timezone"),
    [
        ("CN-HK:DCD:A", "Asia/Hong_Kong"),
        ("CN-MO:AREA:01", "Asia/Macau"),
        ("CN-TW:TOWN:10014020", "Asia/Taipei"),
    ],
)
def test_historical_local_daylight_saving_time_is_metadata_only(
    location_id: str,
    timezone: str,
) -> None:
    beijing_clock = datetime(1975, 7, 1, 12)
    assert beijing_clock.replace(tzinfo=ZoneInfo(timezone)).utcoffset() == timedelta(hours=9)

    result = calculate(beijing_clock.isoformat(), location_id)

    assert result.normalized_input.beijing_datetime == beijing_clock
    expected_total = 4 * (result.solar_time_adjustment.longitude_degrees - 120) + (
        noaa_equation_of_time(beijing_clock)
    )
    assert result.solar_time_adjustment.total_correction_minutes == pytest.approx(
        expected_total,
        abs=1e-6,
    )
    expected_true_solar = beijing_clock + timedelta(minutes=expected_total)
    assert (
        result.normalized_input.true_solar_datetime - expected_true_solar
    ).total_seconds() == pytest.approx(0, abs=0.5)


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
        calculate("1990-01-01T12:00:00", "CN:130171")


def test_fallback_coordinate_is_a_server_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_record = locations.LocationRecord(
        location_id="CN:440106",
        region_code="CN",
        timezone="Asia/Shanghai",
        division_path=(
            locations.DivisionPathItem("440000", "广东省", "province"),
            locations.DivisionPathItem("440100", "广州市", "city"),
            locations.DivisionPathItem("440106", "天河区", "district"),
        ),
        longitude=113.264434,
        latitude=23.129162,
        precision="city_center",
        coordinate_match="regional_center_fallback",
        fallback=True,
        coordinate_source="test-coordinate-source",
    )
    monkeypatch.setattr(
        locations,
        "_load_location_data",
        lambda: {"CN:440106": fallback_record},
    )

    with pytest.raises(locations.LocationDataError, match=r"回退|缺少独立.*坐标"):
        calculate("1990-01-01T12:00:00")


def test_repeated_calculations_are_deterministic() -> None:
    first = calculate("1990-01-01T12:00:00")
    second = calculate("1990-01-01T12:00:00")

    assert first == second
