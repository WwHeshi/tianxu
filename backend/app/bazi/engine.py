"""Deterministic BaZi calculations backed by lunar-python.

This module is the only place that knows the third-party library's object
model.  The API receives a stable, JSON-friendly representation instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from math import cos, pi, sin
from typing import Any

from lunar_python import Lunar, Solar
from lunar_python.util import LunarUtil

from ..schemas import (
    AnnualFortune,
    BigLuckPeriod,
    BigLuckTransition,
    BirthInput,
    BranchComponent,
    CanonicalBirthplace,
    Chart,
    ChartCalendar,
    ChartPreviewResponse,
    Component,
    DivisionPathItem,
    ElementDistribution,
    EngineInfo,
    FortuneCycles,
    FortunePillar,
    FortuneStartOffset,
    HiddenStem,
    MonthlyFortune,
    NormalizedBirthInput,
    Pillar,
    Pillars,
    SolarTimeAdjustment,
)
from .locations import BirthplaceCoordinateError, LocationDataError, get_location
from .shen_sha import SHEN_SHA_POLICY_VERSION, calculate_shen_sha


class ChartCalculationError(RuntimeError):
    """Raised when the calendar library cannot calculate a valid chart."""


try:
    ENGINE_VERSION = version("lunar-python")
except PackageNotFoundError:  # pragma: no cover - only possible in a broken install
    ENGINE_VERSION = "unknown"

ENGINE_NAME = "lunar-python"
FORTUNE_POLICY_VERSION = "v1"
FORTUNE_SECT = 2
FORTUNE_BIG_LUCK_PERIODS = 10
ELEMENTS = ("木", "火", "土", "金", "水")
GAN = tuple(LunarUtil.GAN[1:])
ZHI = tuple(LunarUtil.ZHI[1:])
GAN_ELEMENT = dict(LunarUtil.WU_XING_GAN)
ZHI_ELEMENT = dict(LunarUtil.WU_XING_ZHI)
HIDDEN_GAN = dict(LunarUtil.ZHI_HIDE_GAN)
NAYIN = dict(LunarUtil.NAYIN)
SHI_SHEN = dict(LunarUtil.SHI_SHEN)
GROWTH_STAGES = ("长生", "沐浴", "冠带", "临官", "帝旺", "衰", "病", "死", "墓", "绝", "胎", "养")
GROWTH_STAGE_OFFSETS = {
    "甲": 1,
    "丙": 10,
    "戊": 10,
    "庚": 7,
    "壬": 4,
    "乙": 6,
    "丁": 9,
    "己": 9,
    "辛": 0,
    "癸": 3,
}
FLOW_MONTH_TERM_KEYS = (
    ("立春", "立春"),
    ("惊蛰", "惊蛰"),
    ("清明", "清明"),
    ("立夏", "立夏"),
    ("芒种", "芒种"),
    ("小暑", "小暑"),
    ("立秋", "立秋"),
    ("白露", "白露"),
    ("寒露", "寒露"),
    ("立冬", "立冬"),
    ("大雪", "大雪"),
    ("小寒", "XIAO_HAN"),
)


@dataclass(frozen=True)
class _BigLuckSpec:
    index: int
    is_before_start: bool
    start_year: int
    end_year: int
    start_nominal_age: int
    end_nominal_age: int
    start_solar_datetime: datetime
    end_solar_datetime: datetime
    pillar: FortunePillar | None

# Heavenly stems alternate yang/yin, beginning with 甲; earthly branches do the
# same, beginning with 子.  Keeping this local avoids exposing LunarUtil internals.
GAN_POLARITY = {gan: ("yang" if index % 2 == 0 else "yin") for index, gan in enumerate(GAN)}
ZHI_POLARITY = {zhi: ("yang" if index % 2 == 0 else "yin") for index, zhi in enumerate(ZHI)}

SOLAR_TIME_NOTE = (
    "所有地区统一输入北京时间；系统按出生地静态代表点经度相对东经 120°进行修正，"
    "并使用 NOAA 近似公式计算均时差后得到真太阳时。地点时区仅作为元数据，不参与换算。"
)
BEIJING_TIME_NOTE = (
    "未选择出生地点，系统未进行经度修正或均时差计算，已按输入的北京时间直接排盘。"
)
LIMITATIONS = [
    "经度取静态区级代表点；同一区域内的实际出生地点仍可能带来少量时间误差。",
    (
        "提供出生地点时使用真太阳时；未提供地点时使用北京时间。"
        "规则 v2 在所选时间基准的 23:00（子初）换日。"
    ),
    "接近换日、时辰或节气边界时，应复核出生时间；真太阳时模式还应结合具体地址复核。",
    "均时差使用 NOAA 近似公式，节气表由排盘库提供，边界样例仍需独立历书交叉校验。",
    "性别用于乾造、坤造标签、元辰规则和大运顺逆。",
    (
        "起运按相邻节的精确分钟数折算，4320 分钟折 1 年；"
        "大运按精确交运时刻切换，流年立春换年，流月按十二个节换月。"
    ),
    "五行分布是不加权计数，不代表旺衰或强弱评分。",
    "神煞采用天序固定 51 项规则集 v2；不同流派的神煞取法和名称可能存在差异。",
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
    # Two-hour branches change at each odd-numbered hour. The day and Zi-hour
    # boundaries both occur at 23:00 under the selected sect-1 policy.
    boundaries = tuple(hour * 60 for hour in range(1, 24, 2))
    return min(abs(minute_of_day - boundary) for boundary in boundaries)


def _validate_calendar_input(birth: BirthInput) -> None:
    if birth.calendar_type == "solar":
        return

    lunar_date = birth.lunar_date
    if lunar_date is None:  # pragma: no cover - protected by BirthInput validation
        raise ChartCalculationError("农历输入缺少 lunar_date")
    lunar_month = -lunar_date.month if lunar_date.is_leap_month else lunar_date.month
    try:
        lunar = Lunar.fromYmd(lunar_date.year, lunar_month, lunar_date.day)
        converted_solar = lunar.getSolar()
        converted_date = datetime(
            converted_solar.getYear(),
            converted_solar.getMonth(),
            converted_solar.getDay(),
        ).date()
    except Exception as exc:  # lunar-python does not expose stable exception types
        description = (
            f"{lunar_date.year}年"
            f"{'闰' if lunar_date.is_leap_month else ''}{lunar_date.month}月"
            f"{lunar_date.day}日"
        )
        raise ChartCalculationError(
            f"无效的农历日期或该年不存在所选闰月：{description}"
        ) from exc

    beijing_date = birth.beijing_datetime.date()
    if converted_date != beijing_date:
        raise ChartCalculationError(
            f"农历日期换算为公历 {converted_date.isoformat()}，"
            f"与 beijing_datetime 日期 {beijing_date.isoformat()} 不一致"
        )


def _normalize_birth(
    birth: BirthInput,
) -> tuple[NormalizedBirthInput, SolarTimeAdjustment | None, list[str]]:
    if birth.birthplace is None:
        return (
            NormalizedBirthInput(
                beijing_datetime=birth.beijing_datetime,
                true_solar_datetime=birth.beijing_datetime,
                calendar_type=birth.calendar_type,
                lunar_date=birth.lunar_date,
                birthplace=None,
                gender=birth.gender,
            ),
            None,
            [],
        )

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
        calendar_type=birth.calendar_type,
        lunar_date=birth.lunar_date,
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


def _growth_stage(stem: str, branch: str) -> str:
    try:
        stem_index = GAN.index(stem)
        branch_index = ZHI.index(branch)
        offset = GROWTH_STAGE_OFFSETS[stem]
    except (KeyError, ValueError) as exc:  # pragma: no cover - upstream invariant
        raise ChartCalculationError(
            f"cannot calculate growth stage for stem {stem!r} and branch {branch!r}"
        ) from exc
    direction = 1 if stem_index % 2 == 0 else -1
    return GROWTH_STAGES[(offset + direction * branch_index) % len(GROWTH_STAGES)]


def _pillar(name: str, gan_zhi: str, day_master: str, shen_sha: list[str]) -> Pillar:
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
        growth_stage=_growth_stage(day_master, zhi),
        self_growth_stage=_growth_stage(gan, zhi),
        xun_kong=LunarUtil.getXunKong(gan_zhi),
        na_yin=NAYIN.get(gan_zhi, "未知"),
        shen_sha=shen_sha,
    )


def _fortune_pillar(gan_zhi: str, day_master: str) -> FortunePillar:
    if len(gan_zhi) != 2:
        raise ChartCalculationError(
            f"invalid fortune pillar returned by lunar-python: {gan_zhi!r}"
        )
    gan, zhi = gan_zhi
    try:
        branch_main_stem = HIDDEN_GAN[zhi][0]
        branch = Component(
            symbol=zhi,
            element=ZHI_ELEMENT[zhi],
            polarity=ZHI_POLARITY[zhi],
            ten_god=SHI_SHEN.get(day_master + branch_main_stem),
        )
    except (KeyError, IndexError) as exc:  # pragma: no cover - upstream invariant
        raise ChartCalculationError(
            f"unknown fortune branch returned by lunar-python: {zhi!r}"
        ) from exc
    return FortunePillar(
        gan_zhi=gan_zhi,
        heavenly_stem=_component(gan, ten_god=SHI_SHEN.get(day_master + gan)),
        earthly_branch=branch,
    )


def _solar_datetime(value: Any) -> datetime:
    return datetime.strptime(value.toYmdHms(), "%Y-%m-%d %H:%M:%S")


@lru_cache(maxsize=512)
def _flow_month_starts(year: int) -> tuple[tuple[str, datetime], ...]:
    table = Solar.fromYmdHms(year, 7, 1, 12, 0, 0).getLunar().getJieQiTable()
    try:
        return tuple(
            (label, _solar_datetime(table[key])) for label, key in FLOW_MONTH_TERM_KEYS
        )
    except KeyError as exc:  # pragma: no cover - protects against upstream changes
        raise ChartCalculationError(
            f"lunar-python could not provide flow-month terms for {year}"
        ) from exc


@lru_cache(maxsize=512)
def _flow_year_gan_zhi(year: int) -> str:
    return Solar.fromYmdHms(year, 7, 1, 12, 0, 0).getLunar().getYearInGanZhiExact()


def _flow_year_for_datetime(value: datetime) -> int:
    li_chun = _flow_month_starts(value.year)[0][1]
    return value.year if value >= li_chun else value.year - 1


def _transition_in_interval(
    specs: tuple[_BigLuckSpec, ...],
    start: datetime,
    end: datetime,
) -> BigLuckTransition | None:
    for position, spec in enumerate(specs[1:], start=1):
        if start <= spec.start_solar_datetime < end:
            previous = specs[position - 1]
            if spec.pillar is None:  # pragma: no cover - construction invariant
                raise ChartCalculationError("a formal big-luck period requires a pillar")
            return BigLuckTransition(
                solar_datetime=spec.start_solar_datetime,
                from_index=previous.index,
                from_gan_zhi=(previous.pillar.gan_zhi if previous.pillar else None),
                to_index=spec.index,
                to_gan_zhi=spec.pillar.gan_zhi,
            )
    return None


def _annual_fortune(
    year: int,
    *,
    index: int,
    nominal_age: int,
    day_master: str,
    big_luck_spec: _BigLuckSpec,
    big_luck_specs: tuple[_BigLuckSpec, ...],
) -> AnnualFortune:
    year_gan_zhi = _flow_year_gan_zhi(year)
    year_gan_index = GAN.index(year_gan_zhi[0])
    first_month_gan_index = (year_gan_index % 5 * 2 + 2) % len(GAN)
    month_starts = _flow_month_starts(year)
    annual_start = month_starts[0][1]
    next_flow_year_start = _flow_month_starts(year + 1)[0][1]
    segment_start = max(annual_start, big_luck_spec.start_solar_datetime)
    segment_end = min(next_flow_year_start, big_luck_spec.end_solar_datetime)
    if segment_start >= segment_end:  # pragma: no cover - caller filters intervals
        raise ChartCalculationError("flow year does not overlap the big-luck period")

    annual_transition = _transition_in_interval(
        big_luck_specs,
        annual_start,
        next_flow_year_start,
    )
    annual_transition_phase = None
    if annual_transition is not None:
        if (
            segment_start == annual_transition.solar_datetime
            and annual_transition.to_index == big_luck_spec.index
        ):
            annual_transition_phase = "after"
        elif (
            segment_end == annual_transition.solar_datetime
            and annual_transition.from_index == big_luck_spec.index
        ):
            annual_transition_phase = "before"
    if annual_transition_phase is None:
        annual_transition = None

    months: list[MonthlyFortune] = []
    for month_index in range(12):
        month_start = month_starts[month_index][1]
        month_end = (
            month_starts[month_index + 1][1]
            if month_index < 11
            else next_flow_year_start
        )
        month_segment_start = max(month_start, segment_start)
        month_segment_end = min(month_end, segment_end)
        if month_segment_start >= month_segment_end:
            continue

        month_transition = _transition_in_interval(
            big_luck_specs,
            month_start,
            month_end,
        )
        month_transition_phase = None
        if month_transition is not None:
            if (
                month_segment_start == month_transition.solar_datetime
                and month_transition.to_index == big_luck_spec.index
            ):
                month_transition_phase = "after"
            elif (
                month_segment_end == month_transition.solar_datetime
                and month_transition.from_index == big_luck_spec.index
            ):
                month_transition_phase = "before"
        if month_transition_phase is None:
            month_transition = None
        months.append(
            MonthlyFortune(
                index=month_index + 1,
                solar_term=month_starts[month_index][0],
                start_solar_datetime=month_start,
                segment_start_solar_datetime=month_segment_start,
                segment_end_solar_datetime=month_segment_end,
                pillar=_fortune_pillar(
                    GAN[(first_month_gan_index + month_index) % len(GAN)]
                    + ZHI[(2 + month_index) % len(ZHI)],
                    day_master,
                ),
                big_luck_index_at_start=big_luck_spec.index,
                big_luck_gan_zhi_at_start=(
                    big_luck_spec.pillar.gan_zhi if big_luck_spec.pillar else None
                ),
                transition_phase=month_transition_phase,
                transition=month_transition,
            )
        )

    return AnnualFortune(
        index=index,
        year=year,
        nominal_age=nominal_age,
        segment_start_solar_datetime=segment_start,
        segment_end_solar_datetime=segment_end,
        pillar=_fortune_pillar(year_gan_zhi, day_master),
        months=months,
        big_luck_index_at_start=big_luck_spec.index,
        big_luck_gan_zhi_at_start=(
            big_luck_spec.pillar.gan_zhi if big_luck_spec.pillar else None
        ),
        transition_phase=annual_transition_phase,
        transition=annual_transition,
    )


def _calculate_fortune_cycles(
    eight_char: Any,
    *,
    day_master: str,
    gender: str,
) -> FortuneCycles:
    yun = eight_char.getYun(1 if gender == "male" else 0, FORTUNE_SECT)
    birth_solar = yun.getLunar().getSolar()
    birth_solar_datetime = _solar_datetime(birth_solar)
    birth_flow_year = _flow_year_for_datetime(birth_solar_datetime)
    first_start_solar = yun.getStartSolar()
    start_solar_datetime = _solar_datetime(first_start_solar)
    specs: list[_BigLuckSpec] = []
    for da_yun in yun.getDaYun(FORTUNE_BIG_LUCK_PERIODS):
        index = da_yun.getIndex()
        is_before_start = index == 0
        if is_before_start:
            exact_start = birth_solar_datetime
            exact_end = start_solar_datetime
        else:
            exact_start = _solar_datetime(first_start_solar.nextYear((index - 1) * 10))
            exact_end = _solar_datetime(first_start_solar.nextYear(index * 10))

        start_year = _flow_year_for_datetime(exact_start)
        end_year = _flow_year_for_datetime(exact_end - timedelta(microseconds=1))
        start_age = start_year - birth_flow_year + 1
        end_age = end_year - birth_flow_year + 1

        specs.append(
            _BigLuckSpec(
                index=index,
                is_before_start=is_before_start,
                start_year=start_year,
                end_year=end_year,
                start_nominal_age=start_age,
                end_nominal_age=end_age,
                start_solar_datetime=exact_start,
                end_solar_datetime=exact_end,
                pillar=(
                    None
                    if is_before_start
                    else _fortune_pillar(da_yun.getGanZhi(), day_master)
                ),
            )
        )

    frozen_specs = tuple(specs)
    periods = [
        BigLuckPeriod(
            index=spec.index,
            is_before_start=spec.is_before_start,
            start_year=spec.start_year,
            end_year=spec.end_year,
            start_nominal_age=spec.start_nominal_age,
            end_nominal_age=spec.end_nominal_age,
            start_solar_datetime=spec.start_solar_datetime,
            end_solar_datetime=spec.end_solar_datetime,
            pillar=spec.pillar,
            years=[
                _annual_fortune(
                    year,
                    index=year_index,
                    nominal_age=year - birth_flow_year + 1,
                    day_master=day_master,
                    big_luck_spec=spec,
                    big_luck_specs=frozen_specs,
                )
                for year_index, year in enumerate(
                    range(spec.start_year, spec.end_year + 1)
                )
            ],
        )
        for spec in frozen_specs
    ]

    return FortuneCycles(
        policy_version=FORTUNE_POLICY_VERSION,
        direction="forward" if yun.isForward() else "backward",
        start_offset=FortuneStartOffset(
            years=yun.getStartYear(),
            months=yun.getStartMonth(),
            days=yun.getStartDay(),
            hours=yun.getStartHour(),
        ),
        start_solar_datetime=start_solar_datetime,
        big_luck_periods=periods,
    )


def calculate_chart(
    birth: BirthInput,
    *,
    include_fortune_cycles: bool = True,
) -> ChartPreviewResponse:
    """Calculate a chart with a deterministic, versioned policy.

    Agent tools can disable the fortune timeline while the chart preview API
    keeps the complete result by default.
    """

    _validate_calendar_input(birth)
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
        lunar = solar.getLunar()
        eight_char = lunar.getEightChar()
        # lunar-python sect 1 rolls the day pillar at 23:00 (Zi-hour start).
        eight_char.setSect(1)
        raw_pillars = {
            "year": eight_char.getYear(),
            "month": eight_char.getMonth(),
            "day": eight_char.getDay(),
            "hour": eight_char.getTime(),
        }
    except Exception as exc:  # upstream exceptions are not part of our API contract
        raise ChartCalculationError("lunar-python could not calculate this date") from exc

    day_master = raw_pillars["day"][0]
    shen_sha = calculate_shen_sha(raw_pillars, gender=birth.gender.value)
    fortune_cycles = (
        _calculate_fortune_cycles(
            eight_char,
            day_master=day_master,
            gender=birth.gender.value,
        )
        if include_fortune_cycles
        else None
    )
    pillar_map = {
        name: _pillar(name, value, day_master, shen_sha[name])
        for name, value in raw_pillars.items()
    }
    pillars = Pillars(**pillar_map)  # type: ignore[arg-type]

    lunar_month = lunar.getMonth()
    destiny_type = {
        "male": "乾造",
        "female": "坤造",
    }[birth.gender.value]
    calendar = ChartCalendar(
        solar_datetime=true_solar,
        lunar_year=lunar.getYear(),
        lunar_month=abs(lunar_month),
        lunar_day=lunar.getDay(),
        is_leap_month=lunar_month < 0,
        lunar_text=(
            f"{lunar.getYear()}年{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"
        ),
        time_branch=raw_pillars["hour"][1],
        zodiac=lunar.getYearShengXiaoExact(),
        destiny_type=destiny_type,  # type: ignore[arg-type]
    )

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
            calendar=calendar,
            pillars=pillars,
            day_master=_component(day_master, ten_god="日主"),
            element_distribution=ElementDistribution(
                visible=visible,
                hidden_stems=hidden_counts,
                total=total,
            ),
            fortune_cycles=fortune_cycles,
        ),
        calculation_policy=birth.calculation_policy,
        solar_time_adjustment=solar_time_adjustment,
        engine=EngineInfo(
            name=ENGINE_NAME,
            version=ENGINE_VERSION,
            policy_version=birth.calculation_policy.version,
            shen_sha_policy_version=SHEN_SHA_POLICY_VERSION,
            fortune_policy_version=FORTUNE_POLICY_VERSION,
            solar_time_note=(
                SOLAR_TIME_NOTE if solar_time_adjustment is not None else BEIJING_TIME_NOTE
            ),
        ),
        warnings=warnings,
        limitations=LIMITATIONS,
    )
