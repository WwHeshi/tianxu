"""Compact chart context and one-shot structured report generation."""

import json
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from .models import ModelCredential
from .schemas import (
    AnnualFortune,
    BaziReport,
    BigLuckPeriod,
    ChartPreviewResponse,
    MonthlyFortune,
)

PROMPT_VERSION = "bazi-report-v1"
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

REPORT_INSTRUCTIONS = """你是八字命盘报告撰写助手。
输入中的四柱、十神、藏干、五行分布、神煞和运势周期都由确定性排盘引擎计算完成。

必须遵守：
1. 只能解释输入数据，不得重新排盘，不得改写或质疑四柱。
2. 当前版本没有知识库。不得声称依据某本古籍，不得虚构原文、书名、作者或引文。
3. 使用审慎、概率性的中文表述，把内容明确定位为传统文化视角下的参考，不把推断写成事实。
4. 不作疾病诊断、寿命判断、灾祸断言，不给出确定性的法律、投资或医疗建议。
5. 每一节应具体对应输入命盘，避免空泛套话；若数据不足，应直接说明局限。
6. 当前运势只讨论输入给出的当前大运、流年、流月，不推测未提供的完整时间线。
7. 严格返回指定 JSON 结构，不添加其他字段。"""


class ModelProviderError(RuntimeError):
    """A safe-to-display model-provider failure without upstream secrets or payloads."""


@dataclass(frozen=True)
class ReportGenerationResult:
    report: BaziReport
    context: dict[str, Any]
    system_prompt: str
    user_prompt: str
    endpoint: str
    request_body: dict[str, Any]
    response_format: str
    model_latency_ms: int


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


def _select_period(
    periods: list[BigLuckPeriod], now: datetime
) -> BigLuckPeriod | None:
    if not periods:
        return None
    return next(
        (
            item
            for item in periods
            if item.start_solar_datetime <= now < item.end_solar_datetime
        ),
        periods[0] if now < periods[0].start_solar_datetime else periods[-1],
    )


def _select_annual(period: BigLuckPeriod, now: datetime) -> AnnualFortune | None:
    if not period.years:
        return None
    return next(
        (
            item
            for item in period.years
            if item.segment_start_solar_datetime <= now < item.segment_end_solar_datetime
        ),
        period.years[0] if now < period.years[0].segment_start_solar_datetime else period.years[-1],
    )


def _select_month(annual: AnnualFortune, now: datetime) -> MonthlyFortune | None:
    if not annual.months:
        return None
    return next(
        (
            item
            for item in annual.months
            if item.segment_start_solar_datetime <= now < item.segment_end_solar_datetime
        ),
        (
            annual.months[0]
            if now < annual.months[0].segment_start_solar_datetime
            else annual.months[-1]
        ),
    )


def build_report_context(
    chart: ChartPreviewResponse,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a compact context and intentionally omit the full fortune timeline."""

    current_time = now or datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    fortune: dict[str, Any] | None = None
    cycles = chart.chart.fortune_cycles
    if cycles is not None:
        period = _select_period(cycles.big_luck_periods, current_time)
        annual = _select_annual(period, current_time) if period else None
        month = _select_month(annual, current_time) if annual else None
        fortune = {
            "as_of_beijing_time": current_time.isoformat(timespec="seconds"),
            "direction": cycles.direction,
            "fortune_start": cycles.start_solar_datetime.isoformat(),
            "current_big_luck": (
                period.model_dump(mode="json", exclude={"years"}) if period else None
            ),
            "current_annual": (
                annual.model_dump(mode="json", exclude={"months"}) if annual else None
            ),
            "current_month": month.model_dump(mode="json") if month else None,
        }

    return {
        "context_version": "v1",
        "interpretation_scope": "traditional_bazi_cultural_reference",
        "normalized_input": {
            "beijing_datetime": chart.normalized_input.beijing_datetime.isoformat(),
            "true_solar_datetime": chart.normalized_input.true_solar_datetime.isoformat(),
            "calendar_type": chart.normalized_input.calendar_type,
            "gender": chart.normalized_input.gender,
        },
        "calendar": chart.chart.calendar.model_dump(mode="json"),
        "pillars": chart.chart.pillars.model_dump(mode="json"),
        "day_master": chart.chart.day_master.model_dump(mode="json"),
        "element_distribution": chart.chart.element_distribution.model_dump(mode="json"),
        "current_fortune": fortune,
        "calculation_policy": chart.calculation_policy.model_dump(mode="json"),
        "engine": chart.engine.model_dump(mode="json"),
        "warnings": chart.warnings,
        "limitations": chart.limitations,
    }


def _extract_responses_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for output in payload.get("output", []):
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise ModelProviderError("模型拒绝生成这份报告，请调整输入后重试。")
            text = content.get("text")
            if content.get("type") == "output_text" and isinstance(text, str):
                return text
    raise ModelProviderError("模型没有返回可用的结构化报告。")


def _extract_chat_output_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelProviderError("模型没有返回可用的报告内容。")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ModelProviderError("模型没有返回可用的报告内容。")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    raise ModelProviderError("模型没有返回可用的报告内容。")


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
    context = build_report_context(chart)
    context_text = "请根据以下确定性命盘 JSON 生成固定结构报告：\n" + json.dumps(
        context, ensure_ascii=False, separators=(",", ":")
    )
    if credential.api_protocol == "responses":
        url = f"{credential.base_url.rstrip('/')}/responses"
        system_prompt = REPORT_INSTRUCTIONS
        response_format = "json_schema"
        request_body = {
            "model": credential.model,
            "instructions": system_prompt,
            "input": context_text,
            "max_output_tokens": 5000,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "bazi_report",
                    "strict": True,
                    "schema": REPORT_JSON_SCHEMA,
                }
            },
        }
    elif credential.api_protocol == "chat_completions":
        url = f"{credential.base_url.rstrip('/')}/chat/completions"
        field_names = "、".join(REPORT_FIELDS)
        system_prompt = REPORT_INSTRUCTIONS + (
            f"\n只返回一个 JSON 对象，必须包含这些字符串字段：{field_names}。"
        )
        response_format = "prompted_json"
        request_body = {
            "model": credential.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": context_text},
            ],
            "max_tokens": 5000,
            "stream": False,
        }
    else:
        raise ModelProviderError("不支持已保存的 API 协议，请重新保存模型设置。")
    timeout = httpx.Timeout(MODEL_TIMEOUT_SECONDS, connect=15.0)
    model_started = perf_counter()
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
        raise ModelProviderError("模型服务响应超时，请稍后重试。") from exc
    except httpx.RequestError as exc:
        raise ModelProviderError("无法连接模型服务，请检查 Base URL。") from exc
    model_latency_ms = round((perf_counter() - model_started) * 1000)

    if response.status_code in {401, 403}:
        raise ModelProviderError("模型服务鉴权失败，请重新检查 API 密钥。")
    if response.status_code == 429:
        raise ModelProviderError("模型服务当前请求过多，请稍后重试。")
    if not response.is_success:
        raise ModelProviderError(f"模型服务调用失败（HTTP {response.status_code}）。")

    try:
        payload = response.json()
        output_text = (
            _extract_responses_output_text(payload)
            if credential.api_protocol == "responses"
            else _extract_chat_output_text(payload)
        )
        raw_report = _parse_report_json(output_text)
        report = BaziReport.model_validate(raw_report)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ModelProviderError("模型返回的报告结构不符合约定，请重试。") from exc
    return ReportGenerationResult(
        report=report,
        context=context,
        system_prompt=system_prompt,
        user_prompt=context_text,
        endpoint=url,
        request_body=request_body,
        response_format=response_format,
        model_latency_ms=model_latency_ms,
    )
