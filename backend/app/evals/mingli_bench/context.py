"""Build answer-free Tianxu chart context for one MingLi question."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ...bazi.tool import BaziChartToolInput, run_bazi_chart_tool
from ...schemas import ChartPreviewResponse, FortunePillar, Pillar
from .dataset import EvaluationQuestion

PROMPT_VERSION = "mingli-eval-v5-react-text"
YEAR_PATTERN = re.compile(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)")
AGE_PATTERN = re.compile(r"岁|歲|大运|大運|大限")

SYSTEM_PROMPT = """你是采用 ReAct 工作方式的天序八字选择题 Agent。
唯一任务是从 A、B、C、D 中选出最符合命盘的一项。
你可以按需调用 calculate_bazi_chart，也可以直接返回最终答案。
如果调用工具，其 Observation 是权威命盘事实，不得重新排盘、改写四柱或质疑计算口径。
这是公开历史选择题的封闭分类评测，不是对现实个人作确定性断言，也不构成医疗、法律或投资建议。

必须遵守以下输出规则：
1. 即使依据不足或存在不确定性，也必须选择一个最可能的选项，不得拒答、追问或只给分析。
2. 不得输出详细推理过程、Markdown、代码围栏、前言、后记、道歉或任何额外文字。
3. 可见回答必须直接以“{”开始、以“}”结束，并且只能包含一个合法 JSON 对象。
4. JSON 必须且只能包含 answer、confidence、reasoning_summary 三个字段。
5. answer 必须是 A、B、C、D 之一；confidence 必须是 0 到 100 的整数。
6. reasoning_summary 必须在 120 个汉字以内，只概括最关键依据。
   不要进行长篇推演，优先确保完整输出 JSON。

严格按此格式返回：
{"answer":"A","confidence":75,"reasoning_summary":"简要依据"}"""


def chart_tool_input_for_question(question: EvaluationQuestion) -> BaziChartToolInput:
    """Build the tool input from the benchmark's authoritative chart clock.

    MingLi-Bench does not contain coordinates precise enough to recalculate
    apparent solar time. To preserve all existing evaluation results, its
    published birth clock continues to be treated as the already-normalized
    chart time.
    """

    birth = question.birth_info
    gender = {"男": "male", "女": "female"}.get(str(birth.get("gender")), "other")
    try:
        chart_datetime = (
            f"{int(birth['year']):04}-{int(birth['month']):02}-{int(birth['day']):02}"
            f"T{int(birth['hour']):02}:{int(birth['minute']):02}:00"
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"题目 {question.id} 的出生时间无效") from exc
    return BaziChartToolInput(
        gender=gender,
        true_solar_datetime=chart_datetime,
    )


def chart_for_question(question: EvaluationQuestion) -> ChartPreviewResponse:
    return run_bazi_chart_tool(chart_tool_input_for_question(question))


def _component(component: Any) -> dict[str, Any]:
    return {
        "symbol": component.symbol,
        "ten_god": getattr(component, "ten_god", None),
    }


def _pillar(pillar: Pillar) -> dict[str, Any]:
    return {
        "gan_zhi": pillar.gan_zhi,
        "heavenly_stem": _component(pillar.heavenly_stem),
        "earthly_branch": {
            "symbol": pillar.earthly_branch.symbol,
            "hidden_stems": [_component(stem) for stem in pillar.earthly_branch.hidden_stems],
        },
        "growth_stage": pillar.growth_stage,
        "xun_kong": pillar.xun_kong,
        "na_yin": pillar.na_yin,
        "shen_sha": pillar.shen_sha,
    }


def _fortune_pillar(pillar: FortunePillar | None) -> dict[str, Any] | None:
    if pillar is None:
        return None
    return {
        "gan_zhi": pillar.gan_zhi,
        "stem_ten_god": pillar.heavenly_stem.ten_god,
        "branch_ten_god": pillar.earthly_branch.ten_god,
    }


def _question_text(question: EvaluationQuestion) -> str:
    return question.question + " " + " ".join(option.text for option in question.options)


def target_years(question: EvaluationQuestion) -> tuple[int, ...]:
    text = _question_text(question)
    years = {int(value) for value in YEAR_PATTERN.findall(text)}
    return tuple(sorted(years))


def _fortune_context(
    chart: ChartPreviewResponse,
    years: tuple[int, ...],
    *,
    include_schedule: bool,
) -> dict[str, Any]:
    cycles = chart.chart.fortune_cycles
    if cycles is None:
        return {"target_years": {}, "big_luck_schedule": []}
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
                    "effective_from": annual.segment_start_solar_datetime.date().isoformat(),
                    "effective_until_exclusive": (
                        annual.segment_end_solar_datetime.date().isoformat()
                    ),
                }
            )
    schedule = []
    if include_schedule:
        schedule = [
            {
                "start_year": period.start_year,
                "end_year": period.end_year,
                "start_nominal_age": period.start_nominal_age,
                "end_nominal_age": period.end_nominal_age,
                "pillar": _fortune_pillar(period.pillar),
            }
            for period in cycles.big_luck_periods
        ]
    return {
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
        "birth": {
            "gender": question.birth_info.get("gender"),
            "year": question.birth_info.get("year"),
            "month": question.birth_info.get("month"),
            "day": question.birth_info.get("day"),
            "hour": question.birth_info.get("hour"),
            "minute": question.birth_info.get("minute"),
            "calendar_type": question.birth_info.get("calendar_type"),
            "country": question.birth_info.get("country"),
            "location": question.birth_info.get("location"),
        },
        "tianxu_chart": {
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


def build_evaluation_tool_observation(
    question: EvaluationQuestion,
    chart: ChartPreviewResponse,
) -> dict[str, Any]:
    return build_evaluation_context(question, chart)["tianxu_chart"]


def build_evaluation_prompt(
    question: EvaluationQuestion,
) -> tuple[str, dict[str, Any], str]:
    tool_input = chart_tool_input_for_question(question)
    context = {
        "birth": {
            "raw": question.birth_info.get("raw"),
            "gender": tool_input.gender.value,
            "true_solar_datetime": tool_input.true_solar_datetime.isoformat(
                timespec="seconds"
            ),
        },
        "question": question.question,
        "options": [
            {"letter": option.letter, "text": option.text}
            for option in question.options
        ],
    }
    option_text = "\n".join(
        f"{option.letter}. {option.text}" for option in question.options
    )
    user_prompt = (
        "请完成以下天序命理选择题：\n"
        f"原始出生资料：{question.birth_info.get('raw') or '未提供'}\n"
        f"性别：{tool_input.gender.value}\n"
        f"真太阳出生时间：{tool_input.true_solar_datetime.isoformat(timespec='seconds')}\n"
        f"题目：{question.question}\n"
        f"选项：\n{option_text}"
    )
    forbidden = ("correct_answer", "has_answer", "正确答案", "正確答案")
    if any(marker in user_prompt for marker in forbidden):
        raise ValueError(f"题目 {question.id} 的模型输入发生答案泄漏")
    return user_prompt, context, hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
