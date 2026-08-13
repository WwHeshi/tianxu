"""Point-in-time selection over the deterministic fortune timeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..schemas import AnnualFortune, BigLuckPeriod, FortuneCycles, MonthlyFortune


class FortuneAtRangeError(ValueError):
    """The requested instant is outside the generated fortune timeline."""


@dataclass(frozen=True)
class FortuneAtSelection:
    """The only big-luck, annual and monthly segments active at one instant."""

    as_of_datetime: datetime
    big_luck: BigLuckPeriod
    annual: AnnualFortune
    monthly: MonthlyFortune


def select_fortune_at(
    cycles: FortuneCycles,
    as_of_datetime: datetime,
) -> FortuneAtSelection:
    """Select active half-open intervals without silently clamping the date."""

    period = next(
        (
            item
            for item in cycles.big_luck_periods
            if item.start_solar_datetime <= as_of_datetime < item.end_solar_datetime
        ),
        None,
    )
    if period is None:
        if not cycles.big_luck_periods:
            raise FortuneAtRangeError("运势时间线为空，无法查询指定时点。")
        supported_from = cycles.big_luck_periods[0].start_solar_datetime
        supported_until = cycles.big_luck_periods[-1].end_solar_datetime
        raise FortuneAtRangeError(
            "查询时点不在可计算范围内："
            f"[{supported_from.isoformat()}, {supported_until.isoformat()})。"
        )

    annual = next(
        (
            item
            for item in period.years
            if item.segment_start_solar_datetime
            <= as_of_datetime
            < item.segment_end_solar_datetime
        ),
        None,
    )
    if annual is None:  # pragma: no cover - fortune timeline construction invariant
        raise FortuneAtRangeError("指定时点未命中流年片段。")

    monthly = next(
        (
            item
            for item in annual.months
            if item.segment_start_solar_datetime
            <= as_of_datetime
            < item.segment_end_solar_datetime
        ),
        None,
    )
    if monthly is None:  # pragma: no cover - fortune timeline construction invariant
        raise FortuneAtRangeError("指定时点未命中流月片段。")

    return FortuneAtSelection(
        as_of_datetime=as_of_datetime,
        big_luck=period,
        annual=annual,
        monthly=monthly,
    )
