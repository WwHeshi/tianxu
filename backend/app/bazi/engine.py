"""Deterministic BaZi calculations backed by lunar-python.

This module is the only place that knows the third-party library's object
model.  The API receives a stable, JSON-friendly representation instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from math import cos, pi, sin

from lunar_python import Solar
from lunar_python.util import LunarUtil

from ..schemas import (
    BirthInput,
    BranchComponent,
    CanonicalBirthplace,
    Chart,
    ChartPreviewResponse,
    Component,
    DivisionPathItem,
    ElementDistribution,
    EngineInfo,
    HiddenStem,
    NormalizedBirthInput,
    Pillar,
    Pillars,
    SolarTimeAdjustment,
)
from .locations import BirthplaceCoordinateError, LocationDataError, get_location


class ChartCalculationError(RuntimeError):
    """Raised when the calendar library cannot calculate a valid chart."""


try:
    ENGINE_VERSION = version("lunar-python")
except PackageNotFoundError:  # pragma: no cover - only possible in a broken install
    ENGINE_VERSION = "unknown"

ENGINE_NAME = "lunar-python"
ELEMENTS = ("木", "火", "土", "金", "水")
GAN = tuple(LunarUtil.GAN[1:])
ZHI = tuple(LunarUtil.ZHI[1:])
GAN_ELEMENT = dict(LunarUtil.WU_XING_GAN)
ZHI_ELEMENT = dict(LunarUtil.WU_XING_ZHI)
HIDDEN_GAN = dict(LunarUtil.ZHI_HIDE_GAN)
NAYIN = dict(LunarUtil.NAYIN)
SHI_SHEN = dict(LunarUtil.SHI_SHEN)

# Heavenly stems alternate yang/yin, beginning with 甲; earthly branches do the
# same, beginning with 子.  Keeping this local avoids exposing LunarUtil internals.
GAN_POLARITY = {gan: ("yang" if index % 2 == 0 else "yin") for index, gan in enumerate(GAN)}
ZHI_POLARITY = {zhi: ("yang" if index % 2 == 0 else "yin") for index, zhi in enumerate(ZHI)}

SOLAR_TIME_NOTE = (
    "所有地区统一输入北京时间；系统按出生地静态代表点经度相对东经 120°进行修正，"
    "并使用 NOAA 近似公式计算均时差后得到真太阳时。地点时区仅作为元数据，不参与换算。"
)
LIMITATIONS = [
    "经度取静态区级代表点；同一区域内的实际出生地点仍可能带来少量时间误差。",
    "规则 v1 使用真太阳时并在 00:00 换日；接近换日、时辰或节气边界时应结合具体地址复核。",
    "均时差使用 NOAA 近似公式，节气表由排盘库提供，边界样例仍需独立历书交叉校验。",
    "性别当前仅用于记录，v1 尚未计算大运、起运和流年。",
    "五行分布是不加权计数，不代表旺衰或强弱评分。",
]


def _element_counts() -> dict[str, int]:
    return {element: 0 for element in ELEMENTS}


def _equation_of_time_minutes(value: datetime) -> float:
    """Return NOAA's fractional-year approximation of the equation of time."""

    day_of_year = value.timetuple().tm_yday
    fractional_hour = value.hour + value.minute / 60 + value.second / 3600
    gamma = 2 * pi / 365 * (day_of_year - 1 + (fractional_hour - 12) / 24)
    return 229.18 * (
        0.000075
        + 0.001868 * cos(gamma)
        - 0.032077 * sin(gamma)
        - 0.014615 * cos(2 * gamma)
        - 0.040849 * sin(2 * gamma)
    )


def _round_to_second(value: datetime) -> datetime:
    if value.microsecond >= 500_000:
        value += timedelta(seconds=1)
    return value.replace(microsecond=0)


def _minutes_from_pillar_boundary(value: datetime) -> float:
    minute_of_day = value.hour * 60 + value.minute + value.second / 60
    # Day changes at 00:00; two-hour branches change at each odd-numbered hour.
    boundaries = (0, *(hour * 60 for hour in range(1, 24, 2)), 24 * 60)
    return min(abs(minute_of_day - boundary) for boundary in boundaries)


def _normalize_birth(
    birth: BirthInput,
) -> tuple[NormalizedBirthInput, SolarTimeAdjustment, list[str]]:
    try:
        location = get_location(birth.birthplace.location_id)
    except BirthplaceCoordinateError as exc:
        raise ChartCalculationError(str(exc)) from exc
    if location.fallback or location.coordinate_match.endswith("_fallback"):
        raise LocationDataError(
            f"该出生地区缺少独立坐标，系统禁止使用回退坐标："
            f"{location.display_name}（{location.location_id}）"
        )

    longitude_correction = 4 * (location.longitude - 120)
    equation_of_time = _equation_of_time_minutes(birth.beijing_datetime)
    total_correction = longitude_correction + equation_of_time
    true_solar_datetime = _round_to_second(
        birth.beijing_datetime + timedelta(minutes=total_correction)
    )

    warnings: list[str] = []
    if _minutes_from_pillar_boundary(true_solar_datetime) <= 10:
        warnings.append("真太阳时接近换日或时辰边界，建议使用更具体的出生地址复核")

    normalized = NormalizedBirthInput(
        beijing_datetime=birth.beijing_datetime,
        true_solar_datetime=true_solar_datetime,
        birthplace=CanonicalBirthplace(
            location_id=location.location_id,
            region_code=location.region_code,
            timezone=location.timezone,
            division_path=[
                DivisionPathItem(code=item.code, name=item.name, type=item.type)
                for item in location.division_path
            ],
        ),
        gender=birth.gender,
    )
    adjustment = SolarTimeAdjustment(
        longitude_degrees=round(location.longitude, 6),
        latitude_degrees=(
            round(location.latitude, 6) if location.latitude is not None else None
        ),
        reference_meridian_degrees=120,
        longitude_correction_minutes=round(longitude_correction, 6),
        equation_of_time_minutes=round(equation_of_time, 6),
        total_correction_minutes=round(total_correction, 6),
        location_precision=location.precision,
        coordinate_match=location.coordinate_match,
        coordinate_source=location.coordinate_source,
    )
    return normalized, adjustment, warnings


def _component(symbol: str, *, ten_god: str | None = None) -> Component:
    try:
        element = GAN_ELEMENT[symbol]
        polarity = GAN_POLARITY[symbol]
    except KeyError as exc:  # pragma: no cover - protects against upstream changes
        raise ChartCalculationError(
            f"unknown heavenly stem returned by lunar-python: {symbol!r}"
        ) from exc
    return Component(symbol=symbol, element=element, polarity=polarity, ten_god=ten_god)


def _branch(symbol: str, day_master: str) -> BranchComponent:
    try:
        hidden = HIDDEN_GAN[symbol]
        element = ZHI_ELEMENT[symbol]
        polarity = ZHI_POLARITY[symbol]
    except KeyError as exc:  # pragma: no cover - protects against upstream changes
        raise ChartCalculationError(
            f"unknown earthly branch returned by lunar-python: {symbol!r}"
        ) from exc
    hidden_stems = [
        HiddenStem(
            symbol=gan,
            element=GAN_ELEMENT[gan],
            polarity=GAN_POLARITY[gan],
            ten_god=SHI_SHEN.get(day_master + gan),
        )
        for gan in hidden
    ]
    return BranchComponent(
        symbol=symbol,
        element=element,
        polarity=polarity,
        hidden_stems=hidden_stems,
    )


def _pillar(name: str, gan_zhi: str, day_master: str) -> Pillar:
    if len(gan_zhi) != 2:
        raise ChartCalculationError(f"invalid pillar returned by lunar-python: {gan_zhi!r}")
    gan, zhi = gan_zhi
    return Pillar(
        name=name,  # type: ignore[arg-type]
        gan_zhi=gan_zhi,
        heavenly_stem=_component(
            gan,
            ten_god="日主" if name == "day" else SHI_SHEN.get(day_master + gan),
        ),
        earthly_branch=_branch(zhi, day_master),
        na_yin=NAYIN.get(gan_zhi, "未知"),
    )


def calculate_chart(birth: BirthInput) -> ChartPreviewResponse:
    """Calculate a chart with a deterministic, versioned policy."""

    normalized, solar_time_adjustment, warnings = _normalize_birth(birth)
    true_solar = normalized.true_solar_datetime
    try:
        # The selected policy applies the corrected apparent-solar wall clock to
        # all four pillar boundaries. lunar-python accepts calendar components.
        solar = Solar.fromYmdHms(
            true_solar.year,
            true_solar.month,
            true_solar.day,
            true_solar.hour,
            true_solar.minute,
            true_solar.second,
        )
        eight_char = solar.getLunar().getEightChar()
        # Sect 2 is the library's midnight rollover convention (our v1 policy).
        eight_char.setSect(2)
        raw_pillars = {
            "year": eight_char.getYear(),
            "month": eight_char.getMonth(),
            "day": eight_char.getDay(),
        }
        # lunar-python derives the 23:00 hour stem from the next day's stem even
        # in sect 2.  v1 explicitly uses midnight rollover, so derive the hour
        # stem from the selected day pillar while retaining the library's branch.
        time_zhi = eight_char.getTimeZhi()
        day_gan_index = GAN.index(raw_pillars["day"][0])
        time_zhi_index = ZHI.index(time_zhi)
        time_gan = GAN[(day_gan_index % 5 * 2 + time_zhi_index) % len(GAN)]
        raw_pillars["hour"] = time_gan + time_zhi
    except Exception as exc:  # upstream exceptions are not part of our API contract
        raise ChartCalculationError("lunar-python could not calculate this date") from exc

    day_master = raw_pillars["day"][0]
    pillar_map = {name: _pillar(name, value, day_master) for name, value in raw_pillars.items()}
    pillars = Pillars(**pillar_map)  # type: ignore[arg-type]

    visible = _element_counts()
    hidden_counts = _element_counts()
    for pillar in (pillars.year, pillars.month, pillars.day, pillars.hour):
        visible[pillar.heavenly_stem.element] += 1
        visible[pillar.earthly_branch.element] += 1
        for hidden_stem in pillar.earthly_branch.hidden_stems:
            hidden_counts[hidden_stem.element] += 1
    total = {element: visible[element] + hidden_counts[element] for element in ELEMENTS}

    return ChartPreviewResponse(
        normalized_input=normalized,
        chart=Chart(
            pillars=pillars,
            day_master=_component(day_master, ten_god="日主"),
            element_distribution=ElementDistribution(
                visible=visible,
                hidden_stems=hidden_counts,
                total=total,
            ),
        ),
        calculation_policy=birth.calculation_policy,
        solar_time_adjustment=solar_time_adjustment,
        engine=EngineInfo(
            name=ENGINE_NAME,
            version=ENGINE_VERSION,
            policy_version=birth.calculation_policy.version,
            solar_time_note=SOLAR_TIME_NOTE,
        ),
        warnings=warnings,
        limitations=LIMITATIONS,
    )
