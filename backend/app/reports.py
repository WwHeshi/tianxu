"""Structured BaZi report configuration over the shared tool-calling agent."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from .agent_tools import AgentToolRegistry
from .bazi.fortune import FortuneAtRangeError, select_fortune_at
from .bazi.tool import (
    BaziChartToolInput,
    bazi_chart_agent_tool,
    run_bazi_chart_tool,
)
from .models import ModelCredential
from .schemas import (
    BaziReport,
    ChartPreviewResponse,
    Component,
    FortunePillar,
)
from .tool_calling_agent import (
    ToolCallingModelCall,
    ToolCallingRunError,
    ToolCallingToolExecution,
    run_tool_calling_agent,
)

PROMPT_VERSION = "bazi-report-v18-tool-text"
REPORT_SCHEMA_VERSION = "v1"
MODEL_TIMEOUT_SECONDS = 90.0
CONNECTION_TEST_TIMEOUT_SECONDS = 20.0
REPORT_FIELDS = tuple(BaziReport.model_fields)
REPORT_JSON_SCHEMA = {
    "type": "object",
    "properties": {field: {"type": "string"} for field in REPORT_FIELDS},
    "required": list(REPORT_FIELDS),
    "additionalProperties": False,
}

REPORT_INSTRUCTIONS = """你是八字命盘报告 Agent。
调用 calculate_bazi_chart 后，必须以返回结果为权威命盘事实，不得重新排盘、改写或质疑四柱。

必须遵守：
1. 调用工具时，参数必须逐字采用用户提示词给出的 gender 与 true_solar_datetime，
   不得自行换算时间或添加地点。
2. 当前版本没有知识库。不得声称依据某本古籍，不得虚构原文、书名、作者或引文。
3. 使用审慎、概率性的中文表述，把内容明确定位为传统文化视角下的参考，不把推断写成事实。
4. 不作疾病诊断、寿命判断、灾祸断言，不给出确定性的法律、投资或医疗建议。
5. 每一节应具体对应输入命盘，避免空泛套话；若数据不足，应直接说明局限。
6. 当前运势只讨论输入给出的当前大运、流年、流月，不推测未提供的完整时间线。
7. 神煞仅作辅助参考，不得依据单一神煞作吉凶断言。
8. 明确区分输入中的确定性事实与基于事实的传统解释，不得声称引擎已经计算输入未提供的结论。
9. 不得在最终回答中展示内部推理或工具调用过程。
10. 严格返回指定 JSON 结构，不添加其他字段。"""

CHAT_COMPLETIONS_OUTPUT_INSTRUCTIONS = """

请只输出一个合法的 JSON 对象，不要输出 Markdown 代码块、解释文字或其他内容。
JSON 对象必须且只能包含以下 8 个字段；所有字段均为非空字符串，不得遗漏、改名或增加字段：
- chart_overview：命盘整体概述，包括日主、四柱结构、十神配置与总体特征。
- temperament：性格倾向与行为模式。
- career：事业方向、工作特点与发展倾向。
- finance：财运特点与风险倾向。
- relationships：婚恋、人际关系与相处倾向。
- current_fortune：结合输入提供的当前大运、流年和流月分析当前阶段。
- recommendations：给出克制、具体、可执行的建议。
- limitations：说明本次分析的数据边界、不确定性与传统文化参考属性。
所有结论只能依据用户资料与工具结果；资料不足时应明确说明，不得虚构命盘数据。
字段值中不要嵌套 JSON。"""


class ModelProviderError(RuntimeError):
    """A safe-to-display model-provider failure without upstream secrets or payloads."""


class ModelOutputFormatError(ModelProviderError):
    """A model response that can be inspected safely but failed report validation."""

    def __init__(
        self,
        message: str,
        *,
        system_prompt: str,
        user_prompt: str,
        endpoint: str,
        request_body: dict[str, Any],
        raw_response: dict[str, Any],
        model_latency_ms: int,
        model_calls: tuple["ReportModelCall", ...] = (),
        tool_executions: tuple["ReportToolExecution", ...] = (),
    ) -> None:
        super().__init__(message)
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.endpoint = endpoint
        self.request_body = request_body
        self.raw_response = raw_response
        self.model_latency_ms = model_latency_ms
        self.model_calls = model_calls
        self.tool_executions = tool_executions


ReportModelCall = ToolCallingModelCall
ReportToolExecution = ToolCallingToolExecution


@dataclass(frozen=True)
class ReportGenerationResult:
    report: BaziReport
    context: dict[str, Any]
    system_prompt: str
    user_prompt: str
    endpoint: str
    request_body: dict[str, Any]
    raw_response: dict[str, Any]
    model_latency_ms: int
    model_calls: tuple[ReportModelCall, ...] = ()
    tool_executions: tuple[ReportToolExecution, ...] = ()


async def test_model_connection(
    *,
    base_url: str,
    model: str,
    api_key: str,
    api_protocol: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Validate the selected protocol with one minimal generation request."""

    timeout = httpx.Timeout(CONNECTION_TEST_TIMEOUT_SECONDS, connect=10.0)
    if api_protocol == "responses":
        url = f"{base_url.rstrip('/')}/responses"
        request_body = {
            "model": model,
            "input": "只回复 OK",
            "max_output_tokens": 16,
            "store": False,
        }
    elif api_protocol == "chat_completions":
        url = f"{base_url.rstrip('/')}/chat/completions"
        request_body = {
            "model": model,
            "messages": [{"role": "user", "content": "只回复 OK"}],
            "max_tokens": 8,
            "stream": False,
        }
    else:
        raise ModelProviderError("不支持所选 API 协议。")
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
    except httpx.TimeoutException as exc:
        raise ModelProviderError("连接测试超时，请稍后重试。") from exc
    except httpx.RequestError as exc:
        raise ModelProviderError("无法连接模型服务，请检查 Base URL。") from exc

    if response.status_code in {401, 403}:
        raise ModelProviderError("模型服务鉴权失败，请检查 API 密钥。")
    if response.status_code == 404:
        raise ModelProviderError("没有找到所选接口或模型，请检查协议、Base URL 和模型 ID。")
    if response.status_code == 429:
        raise ModelProviderError("模型服务当前请求过多，请稍后重试。")
    if not response.is_success:
        raise ModelProviderError(f"连接测试失败（HTTP {response.status_code}）。")


YIN_YANG_LABEL = {"yang": "阳", "yin": "阴"}
BIG_LUCK_DIRECTION_LABEL = {"forward": "顺排", "backward": "逆排"}


def _component_context(component: Component) -> dict[str, Any]:
    return {
        "symbol": component.symbol,
        "element": component.element,
        "yin_yang": YIN_YANG_LABEL[component.polarity],
        "ten_god": component.ten_god,
    }


def _fortune_pillar_context(pillar: FortunePillar | None) -> dict[str, Any] | None:
    if pillar is None:
        return None
    branch = pillar.earthly_branch
    return {
        "gan_zhi": pillar.gan_zhi,
        "heavenly_stem": _component_context(pillar.heavenly_stem),
        "earthly_branch": {
            "symbol": branch.symbol,
            "primary_element": branch.element,
            "yin_yang": YIN_YANG_LABEL[branch.polarity],
            "main_qi_ten_god": branch.ten_god,
        },
    }


def _current_fortune_context(
    *,
    chart: ChartPreviewResponse,
    current_time: datetime,
) -> dict[str, Any] | None:
    cycles = chart.chart.fortune_cycles
    if cycles is None:
        return None

    try:
        selection = select_fortune_at(cycles, current_time)
    except FortuneAtRangeError:
        return None
    period = selection.big_luck
    annual = selection.annual
    month = selection.monthly

    current_big_luck = {
        "phase": "起运前" if period.is_before_start else "行运中",
        "effective_from": period.start_solar_datetime.isoformat(),
        "effective_until_exclusive": period.end_solar_datetime.isoformat(),
        "pillar": _fortune_pillar_context(period.pillar),
    }

    current_annual = {
        "year": annual.year,
        "nominal_age_sui": annual.nominal_age,
        "effective_from": annual.segment_start_solar_datetime.isoformat(),
        "effective_until_exclusive": annual.segment_end_solar_datetime.isoformat(),
        "pillar": _fortune_pillar_context(annual.pillar),
    }

    current_month = {
        "boundary_solar_term": month.solar_term,
        "solar_month_started_at": month.start_solar_datetime.isoformat(),
        "effective_from": month.segment_start_solar_datetime.isoformat(),
        "effective_until_exclusive": month.segment_end_solar_datetime.isoformat(),
        "pillar": _fortune_pillar_context(month.pillar),
    }

    return {
        "as_of_beijing_datetime": current_time.isoformat(timespec="seconds"),
        "big_luck_sequence_direction": BIG_LUCK_DIRECTION_LABEL[cycles.direction],
        "first_big_luck_start_datetime": cycles.start_solar_datetime.isoformat(),
        "current_big_luck": current_big_luck,
        "current_annual": current_annual,
        "current_month": current_month,
    }


def _current_fortune_prompt(context: dict[str, Any] | None) -> str:
    if context is None:
        return "当前大运：未提供\n当前流年：未提供\n当前流月：未提供"

    def fortune_line(label: str, item: dict[str, Any] | None) -> str:
        if item is None or item.get("pillar") is None:
            return f"{label}：未提供"
        pillar = item["pillar"]["gan_zhi"]
        effective_from = item["effective_from"]
        effective_until = item["effective_until_exclusive"]
        return f"{label}：{pillar}（{effective_from} 至 {effective_until}，结束时间不含）"

    annual = context.get("current_annual")
    month = context.get("current_month")
    lines = [
        f"当前运势基准时间：{context['as_of_beijing_datetime']}",
        fortune_line("当前大运", context.get("current_big_luck")),
        fortune_line("当前流年", annual),
        fortune_line("当前流月", month),
    ]
    if annual is not None:
        lines[2] += f"，公历年份 {annual['year']}，虚岁 {annual['nominal_age_sui']}"
    if month is not None:
        lines[3] += f"，月界节气 {month['boundary_solar_term']}"
    return "\n".join(lines)


def _report_user_prompt(
    tool_input: BaziChartToolInput,
    current_fortune: dict[str, Any] | None,
) -> str:
    return (
        "请根据以下标准化出生资料，生成一份固定八章节的八字分析报告：\n"
        f"性别：{tool_input.gender.value}\n"
        f"真太阳出生时间：{tool_input.true_solar_datetime.isoformat(timespec='seconds')}\n"
        f"{_current_fortune_prompt(current_fortune)}"
    )


def _parse_report_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("report payload must be an object")
    return value


async def generate_structured_report(
    *,
    chart: ChartPreviewResponse,
    credential: ModelCredential,
    api_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ReportGenerationResult:
    expected_tool_input = BaziChartToolInput(
        gender=chart.normalized_input.gender,
        true_solar_datetime=chart.normalized_input.true_solar_datetime,
    )
    report_now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    current_fortune = _current_fortune_context(
        chart=chart,
        current_time=report_now,
    )
    user_prompt = _report_user_prompt(expected_tool_input, current_fortune)
    if credential.api_protocol == "responses":
        system_prompt = REPORT_INSTRUCTIONS
    elif credential.api_protocol == "chat_completions":
        system_prompt = REPORT_INSTRUCTIONS + CHAT_COMPLETIONS_OUTPUT_INSTRUCTIONS
    else:
        raise ModelProviderError("不支持已保存的 API 协议，请重新保存模型设置。")

    timeout = httpx.Timeout(MODEL_TIMEOUT_SECONDS, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            execution = await run_tool_calling_agent(
                api_protocol=credential.api_protocol,
                model=credential.model,
                base_url=credential.base_url,
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_schema_name="bazi_report",
                output_schema=REPORT_JSON_SCHEMA,
                tool_registry=AgentToolRegistry(
                    [
                        bazi_chart_agent_tool(
                            expected_tool_input,
                            execute_tool=run_bazi_chart_tool,
                        )
                    ]
                ),
                client=client,
            )
        except ToolCallingRunError as exc:
            if exc.provider_error:
                raise ModelProviderError(str(exc)) from exc
            raise ModelOutputFormatError(
                str(exc),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                endpoint=exc.endpoint,
                request_body=exc.request_body,
                raw_response=exc.raw_response,
                model_latency_ms=exc.model_latency_ms,
                model_calls=exc.model_calls,
                tool_executions=exc.tool_executions,
            ) from exc

    try:
        report = BaziReport.model_validate(_parse_report_json(execution.output_text))
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ModelOutputFormatError(
            "模型返回的报告结构不符合约定，请重试。",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            endpoint=execution.endpoint,
            request_body=execution.request_body,
            raw_response=execution.raw_response,
            model_latency_ms=execution.model_latency_ms,
            model_calls=execution.model_calls,
            tool_executions=execution.tool_executions,
        ) from exc

    return ReportGenerationResult(
        report=report,
        context=(
            execution.tool_executions[-1].output
            if execution.tool_executions
            else {"birth": expected_tool_input.model_dump(mode="json")}
        ),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        endpoint=execution.endpoint,
        request_body=execution.request_body,
        raw_response=execution.raw_response,
        model_latency_ms=execution.model_latency_ms,
        model_calls=execution.model_calls,
        tool_executions=execution.tool_executions,
    )
