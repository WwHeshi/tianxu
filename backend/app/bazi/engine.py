"""Deterministic BaZi calculations backed by lunar-python.

This module is the only place that knows the third-party library's object
model.  The API receives a stable, JSON-friendly representation instead.
"""

from __future__ import annotations

from datetime import timezone as datetime_timezone
from importlib.metadata import PackageNotFoundError, version
from zoneinfo import ZoneInfo

from lunar_python import Solar
from lunar_python.util import LunarUtil

from ..schemas import (
    BirthInput,
    BranchComponent,
    Chart,
    ChartPreviewResponse,
    Component,
    ElementDistribution,
    EngineInfo,
    HiddenStem,
    NormalizedBirthInput,
    Pillar,
    Pillars,
)


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

TIMEZONE_NOTE = "排盘使用所选 IANA 时区的当地民用时间，当前不应用经度真太阳时校正。"
LIMITATIONS = [
    "规则 v1 使用当地民用时间，并在当地 00:00 换日；当前未启用真太阳时校正。",
    "节气表由排盘库提供，非 Asia/Shanghai 时区或节气交界附近的时间仍需独立样例复核。",
    "性别当前仅用于记录，v1 尚未计算大运、起运和流年。",
    "五行分布是不加权计数，不代表旺衰或强弱评分。",
]


def _element_counts() -> dict[str, int]:
    return {element: 0 for element in ELEMENTS}


def _normalize_birth(birth: BirthInput) -> tuple[NormalizedBirthInput, list[str]]:
    zone = ZoneInfo(birth.timezone)
    local = birth.local_datetime
    warnings: list[str] = []
    if local.tzinfo is None:
        # ZoneInfo accepts nonexistent/ambiguous wall times without raising.
        # Round-tripping both folds lets us reject a spring-forward gap and make
        # the fall-back choice explicit and deterministic.
        candidates = [local.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
        valid = [
            candidate
            for candidate in candidates
            if candidate.astimezone(datetime_timezone.utc)
            .astimezone(zone)
            .replace(tzinfo=None)
            == local
        ]
        if not valid:
            raise ChartCalculationError("出生时间在所选时区不存在（夏令时切换造成的时间缺口）")
        if len(valid) == 2 and valid[0].utcoffset() != valid[1].utcoffset():
            warnings.append("出生时间在夏令时回拨区间内，已采用较早的一次（fold=0）")
        local = valid[0]
    else:
        local = local.astimezone(zone)
        if local.fold:
            warnings.append("输入偏移选择了夏令时回拨区间中的较晚一次")
    utc = local.astimezone(datetime_timezone.utc)
    return (
        NormalizedBirthInput(
            local_datetime=local,
            utc_datetime=utc,
            timezone=birth.timezone,
            gender=birth.gender,
            longitude=birth.longitude,
        ),
        warnings,
    )


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

    normalized, warnings = _normalize_birth(birth)
    local = normalized.local_datetime
    try:
        # lunar-python accepts civil calendar components, not a timezone object.
        solar = Solar.fromYmdHms(
            local.year,
            local.month,
            local.day,
            local.hour,
            local.minute,
            local.second,
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

    if birth.longitude is not None:
        warnings.append("已记录经度，但规则 v1 未启用真太阳时，因此本次未参与计算")
    if birth.local_datetime.tzinfo is not None:
        warnings.append("输入包含时区偏移，已转换为所选 IANA 时区后计算")

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
        engine=EngineInfo(
            name=ENGINE_NAME,
            version=ENGINE_VERSION,
            policy_version=birth.calculation_policy.version,
            timezone_note=TIMEZONE_NOTE,
        ),
        warnings=warnings,
        limitations=LIMITATIONS,
    )
