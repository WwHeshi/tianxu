"""Dynamic single-tool ReAct model client for MingLi evaluations."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from ...bazi.tool import BAZI_CHART_TOOL_NAME, run_bazi_chart_tool
from ...models import EvaluationRun
from ...react_agent import (
    MAX_REACT_MODEL_CALLS,
    ReactProtocolError,
    chat_bazi_tool_definition,
    chat_tool_call,
    responses_bazi_tool_definition,
    responses_tool_call,
    validate_bazi_tool_input,
)
from ...schemas import ChartPreviewResponse
from .context import (
    SYSTEM_PROMPT,
    build_evaluation_tool_observation,
    chart_tool_input_for_question,
)
from .dataset import EvaluationQuestion

MODEL_TIMEOUT_SECONDS = 90.0
MAX_OUTPUT_TOKENS = 65_536
ANSWER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasoning_summary": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    "required": ["answer", "confidence", "reasoning_summary"],
    "additionalProperties": False,
}


class EvaluationAnswer(BaseModel):
    answer: str = Field(pattern="^[A-D]$")
    confidence: int = Field(ge=0, le=100)
    reasoning_summary: str = Field(min_length=1, max_length=500)


class EvaluationModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        fatal: bool = False,
        request_snapshot: dict[str, Any] | None = None,
        response_status_code: int | None = None,
        raw_response: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal
        self.request_snapshot = request_snapshot
        self.response_status_code = response_status_code
        self.raw_response = raw_response
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass(frozen=True)
class EvaluationModelResult:
    answer: EvaluationAnswer
    request_snapshot: dict[str, Any]
    response_status_code: int
    raw_response: dict[str, Any]
    latency_ms: int
    input_tokens: int
    output_tokens: int


def _responses_text(payload: dict[str, Any]) -> str:
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
                raise EvaluationModelError("模型拒绝回答该评测题")
            text = content.get("text")
            if content.get("type") == "output_text" and isinstance(text, str):
                return text
    raise EvaluationModelError("模型没有返回可用的评测答案")


def _chat_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise EvaluationModelError("模型没有返回可用的评测答案")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        if choices[0].get("finish_reason") == "length":
            raise EvaluationModelError("模型推理耗尽输出长度限制，未生成最终答案 JSON")
        raise EvaluationModelError("模型没有返回可用的评测答案")
    return content


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        stripped = "\n".join(lines).strip()
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model answer is not an object")
    return value


def _usage(payload: dict[str, Any], protocol: str) -> tuple[int, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    if protocol == "responses":
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _raw_response(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {"unparsed_response": response.text[:50_000]}
    return payload if isinstance(payload, dict) else {"response_body": payload}


async def request_evaluation_answer(
    *,
    run: EvaluationRun,
    api_key: str,
    user_prompt: str,
    question: EvaluationQuestion,
    client: httpx.AsyncClient,
    chart_cache: dict[str, ChartPreviewResponse] | None = None,
) -> EvaluationModelResult:
    expected_tool_input = chart_tool_input_for_question(question)
    if run.api_protocol == "responses":
        url = f"{run.base_url.rstrip('/')}/responses"
        responses_input: list[dict[str, Any]] = [
            {"role": "user", "content": user_prompt}
        ]
        chat_messages: list[dict[str, Any]] = []
    elif run.api_protocol == "chat_completions":
        url = f"{run.base_url.rstrip('/')}/chat/completions"
        responses_input = []
        chat_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
    else:
        raise EvaluationModelError("评测运行使用了不支持的模型协议", fatal=True)

    model_calls: list[dict[str, Any]] = []
    tool_executions: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0
    last_body: dict[str, Any] = {}
    last_payload: dict[str, Any] = {}
    last_status_code: int | None = None

    def snapshot(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "method": "POST",
            "endpoint": url,
            "provider": run.provider,
            "api_protocol": run.api_protocol,
            "model": run.model,
            "headers": {
                "Authorization": "Bearer [REDACTED]",
                "Content-Type": "application/json",
            },
            "body": body,
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "model_calls": model_calls,
            "tool_executions": tool_executions,
        }

    def output_error(message: str) -> EvaluationModelError:
        return EvaluationModelError(
            message,
            request_snapshot=snapshot(last_body),
            response_status_code=last_status_code,
            raw_response=last_payload or None,
            latency_ms=total_latency_ms,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

    for _ in range(MAX_REACT_MODEL_CALLS):
        if run.api_protocol == "responses":
            body = {
                "model": run.model,
                "instructions": SYSTEM_PROMPT,
                "input": deepcopy(responses_input),
                "tools": [responses_bazi_tool_definition()],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "store": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "mingli_evaluation_answer",
                        "strict": True,
                        "schema": ANSWER_JSON_SCHEMA,
                    }
                },
            }
        else:
            body = {
                "model": run.model,
                "messages": deepcopy(chat_messages),
                "tools": [chat_bazi_tool_definition()],
                "tool_choice": "auto",
                "max_tokens": MAX_OUTPUT_TOKENS,
                "stream": False,
            }
        last_body = body
        started = perf_counter()
        try:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.TimeoutException as exc:
            total_latency_ms += round((perf_counter() - started) * 1000)
            error = EvaluationModelError(
                "模型服务响应超时",
                retryable=True,
                request_snapshot=snapshot(body),
                latency_ms=total_latency_ms,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )
            raise error from exc
        except httpx.RequestError as exc:
            total_latency_ms += round((perf_counter() - started) * 1000)
            error = EvaluationModelError(
                "无法连接模型服务",
                retryable=True,
                request_snapshot=snapshot(body),
                latency_ms=total_latency_ms,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )
            raise error from exc

        latency_ms = round((perf_counter() - started) * 1000)
        total_latency_ms += latency_ms
        status_code = response.status_code
        payload = _raw_response(response)
        last_payload = payload
        last_status_code = status_code
        input_tokens, output_tokens = _usage(payload, run.api_protocol)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

        if not response.is_success:
            model_calls.append(
                {
                    "sequence": len(model_calls) + 1,
                    "stage": "error",
                    "request_body": body,
                    "response_body": payload,
                    "duration_ms": latency_ms,
                    "status_code": status_code,
                }
            )
            if status_code in {401, 403}:
                message, retryable, fatal = "模型服务鉴权失败", False, True
            elif status_code == 404:
                message, retryable, fatal = "模型接口或模型不存在", False, True
            elif status_code == 429 or status_code >= 500:
                message = f"模型服务暂时不可用（HTTP {status_code}）"
                retryable, fatal = True, False
            else:
                message = f"模型调用失败（HTTP {status_code}）"
                retryable, fatal = False, False
            raise EvaluationModelError(
                message,
                retryable=retryable,
                fatal=fatal,
                request_snapshot=snapshot(body),
                response_status_code=status_code,
                raw_response=payload,
                latency_ms=total_latency_ms,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )

        try:
            requested_call = (
                responses_tool_call(payload)
                if run.api_protocol == "responses"
                else chat_tool_call(payload)
            )
        except ReactProtocolError as exc:
            model_calls.append(
                {
                    "sequence": len(model_calls) + 1,
                    "stage": "action_selection",
                    "request_body": body,
                    "response_body": payload,
                    "duration_ms": latency_ms,
                    "status_code": status_code,
                }
            )
            raise output_error(str(exc)) from exc

        stage = "action_selection" if requested_call is not None else "final_answer"
        model_calls.append(
            {
                "sequence": len(model_calls) + 1,
                "stage": stage,
                "request_body": body,
                "response_body": payload,
                "duration_ms": latency_ms,
                "status_code": status_code,
            }
        )

        if requested_call is not None:
            try:
                tool_input = validate_bazi_tool_input(
                    requested_call,
                    expected_tool_input,
                )
            except ReactProtocolError as exc:
                raise output_error(str(exc)) from exc
            tool_started = perf_counter()
            chart = chart_cache.get(question.case_id) if chart_cache is not None else None
            if chart is None:
                chart = run_bazi_chart_tool(tool_input)
                if chart_cache is not None:
                    chart_cache[question.case_id] = chart
            tool_output = build_evaluation_tool_observation(question, chart)
            tool_duration_ms = round((perf_counter() - tool_started) * 1000)
            tool_executions.append(
                {
                    "sequence": len(tool_executions) + 1,
                    "name": BAZI_CHART_TOOL_NAME,
                    "input": tool_input.model_dump(mode="json"),
                    "output": tool_output,
                    "duration_ms": tool_duration_ms,
                }
            )
            observation = json.dumps(
                tool_output,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if run.api_protocol == "responses":
                continuation = payload.get("output")
                if not isinstance(continuation, list):
                    continuation = [requested_call.continuation]
                responses_input.extend(deepcopy(continuation))
                responses_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": requested_call.call_id,
                        "output": observation,
                    }
                )
            else:
                chat_messages.append(deepcopy(requested_call.continuation))
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": requested_call.call_id,
                        "name": BAZI_CHART_TOOL_NAME,
                        "content": observation,
                    }
                )
            continue

        try:
            output_text = (
                _responses_text(payload)
                if run.api_protocol == "responses"
                else _chat_text(payload)
            )
            answer = EvaluationAnswer.model_validate(_json_object(output_text))
        except EvaluationModelError as exc:
            raise output_error(str(exc)) from exc
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise output_error("模型返回的答案结构不符合约定") from exc

        return EvaluationModelResult(
            answer=answer,
            request_snapshot=snapshot(body),
            response_status_code=status_code,
            raw_response=payload,
            latency_ms=total_latency_ms,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

    raise output_error(
        f"ReAct 循环超过 {MAX_REACT_MODEL_CALLS} 次模型响应，已安全终止。"
    )


def model_timeout() -> httpx.Timeout:
    return httpx.Timeout(MODEL_TIMEOUT_SECONDS, connect=15.0)
