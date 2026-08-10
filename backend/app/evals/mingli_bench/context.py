"""Build answer-free Tianxu chart context for one MingLi question."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ...bazi.engine import calculate_chart
from ...schemas import BirthInput, ChartPreviewResponse, FortunePillar, Pillar
from .dataset import EvaluationQuestion

PROMPT_VERSION = "mingli-eval-v1"
YEAR_PATTERN = re.compile(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)")
RELATIVE_TIME_PATTERN = re.compile(r"目前|现在|現時|當前|当前|至今|如今|现今|現今")
AGE_PATTERN = re.compile(r"岁|歲|大运|大運|大限")

SYSTEM_PROMPT = """你是天序八字选择题评测助手。
输入中的四柱、十神、藏干、神煞、大运和流年由确定性排盘引擎计算完成，不得重新排盘或改写四柱。
这是对公开历史选择题的封闭分类评测，不是对现实个人作确定性断言，也不构成医疗、法律或投资建议。
请比较四个选项，选择传统八字语境下最符合输入命盘的一项。
只返回指定 JSON；answer 必须是 A、B、C、D 之一；confidence 为 0 到 100 的整数；
reasoning_summary 使用不超过 120 个汉字概括依据，不输出详细思维链。"""


def chart_for_question(question: EvaluationQuestion) -> ChartPreviewResponse:
    birth = question.birth_info
    gender = {"男": "male", "女": "female"}.get(str(birth.get("gender")), "other")
    try:
        chart_datetime = (
            f"{int(birth['year']):04}-{int(birth['month']):02}-{int(birth['day']):02}"
            f"T{int(birth['hour']):02}:{int(birth['minute']):02}:00"
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"题目 {question.id} 的出生时间无效") from exc
    return calculate_chart(BirthInput(beijing_datetime=chart_datetime, gender=gender))


def _component(component: Any) -> dict[str, Any]:
    return {
        "symbol": component.symbol,
        "element": component.element,
        "polarity": component.polarity,
        "ten_god": getattr(component, "ten_god", None),
    }


def _pillar(pillar: Pillar) -> dict[str, Any]:
    return {
        "gan_zhi": pillar.gan_zhi,
        "heavenly_stem": _component(pillar.heavenly_stem),
        "earthly_branch": {
            **_component(pillar.earthly_branch),
            "hidden_stems": [_component(stem) for stem in pillar.earthly_branch.hidden_stems],
        },
        "growth_stage": pillar.growth_stage,
        "self_growth_stage": pillar.self_growth_stage,
        "xun_kong": pillar.xun_kong,
        "na_yin": pillar.na_yin,
        "shen_sha": pillar.shen_sha,
    }


def _fortune_pillar(pillar: FortunePillar | None) -> dict[str, Any] | None:
    if pillar is None:
        return None
    return {
        "gan_zhi": pillar.gan_zhi,
        "heavenly_stem": _component(pillar.heavenly_stem),
        "earthly_branch": _component(pillar.earthly_branch),
    }


def _question_text(question: EvaluationQuestion) -> str:
    return question.question + " " + " ".join(option.text for option in question.options)


def target_years(question: EvaluationQuestion) -> tuple[int, ...]:
    text = _question_text(question)
    years = {int(value) for value in YEAR_PATTERN.findall(text)}
    if RELATIVE_TIME_PATTERN.search(text):
        years.add(question.benchmark_year)
    return tuple(sorted(years))


def _fortune_context(
    chart: ChartPreviewResponse,
    years: tuple[int, ...],
    *,
    include_schedule: bool,
) -> dict[str, Any]:
    cycles = chart.chart.fortune_cycles
    if cycles is None:
        return {"available": False, "target_years": {}, "big_luck_schedule": []}
    year_context: dict[str, list[dict[str, Any]]] = {str(year): [] for year in years}
    for period in cycles.big_luck_periods:
        for annual in period.years:
            key = str(annual.year)
            if key not in year_context:
                continue
            year_context[key].append(
                {
                    "nominal_age_sui": annual.nominal_age,
                    "annual_pillar": _fortune_pillar(annual.pillar),
                    "big_luck_pillar": _fortune_pillar(period.pillar),
                    "big_luck_index": period.index,
                    "effective_from": annual.segment_start_solar_datetime.isoformat(),
                    "effective_until_exclusive": annual.segment_end_solar_datetime.isoformat(),
                    "transition_phase": annual.transition_phase,
                }
            )
    schedule = []
    if include_schedule:
        schedule = [
            {
                "index": period.index,
                "phase": "before_start" if period.is_before_start else "active_luck",
                "start_year": period.start_year,
                "end_year": period.end_year,
                "start_nominal_age": period.start_nominal_age,
                "end_nominal_age": period.end_nominal_age,
                "pillar": _fortune_pillar(period.pillar),
            }
            for period in cycles.big_luck_periods
        ]
    return {
        "available": True,
        "direction": cycles.direction,
        "target_years": year_context,
        "big_luck_schedule": schedule,
    }


def build_evaluation_context(
    question: EvaluationQuestion,
    chart: ChartPreviewResponse,
) -> dict[str, Any]:
    text = _question_text(question)
    years = target_years(question)
    pillars = chart.chart.pillars
    return {
        "benchmark": {
            "question_id": question.id,
            "reference_year": question.benchmark_year,
            "relative_time_rule": "目前、现在、至今等词按 reference_year 解释",
        },
        "birth": {
            "published_text": question.birth_info.get("raw"),
            "published_clock_policy": "按题面钟表时间排盘，不作地点或真太阳时修正",
            "gender": question.birth_info.get("gender"),
        },
        "tianxu_chart": {
            "calculation_policy": chart.calculation_policy.model_dump(mode="json"),
            "pillars": {
                "year": _pillar(pillars.year),
                "month": _pillar(pillars.month),
                "day": _pillar(pillars.day),
                "hour": _pillar(pillars.hour),
            },
            "fortune": _fortune_context(
                chart,
                years,
                include_schedule=bool(AGE_PATTERN.search(text)),
            ),
        },
        "question": question.question,
        "options": [
            {"letter": option.letter, "text": option.text} for option in question.options
        ],
    }


def build_evaluation_prompt(
    question: EvaluationQuestion,
    chart: ChartPreviewResponse,
) -> tuple[str, dict[str, Any], str]:
    context = build_evaluation_context(question, chart)
    user_prompt = "请完成以下天序命理选择题：\n" + json.dumps(
        context,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    forbidden = ("correct_answer", "has_answer", "正确答案", "正確答案")
    if any(marker in user_prompt for marker in forbidden):
        raise ValueError(f"题目 {question.id} 的模型输入发生答案泄漏")
    return user_prompt, context, hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
