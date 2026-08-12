"""Shared single-tool calling loop for report and evaluation agents."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from .bazi.tool import (
    BAZI_CHART_TOOL_NAME,
    BaziChartToolInput,
    BaziChartToolResult,
    bazi_chart_tool_definition,
)

ApiProtocol = Literal["responses", "chat_completions"]
MAX_TOOL_CALLING_MODEL_CALLS = 10


class ToolCallProtocolError(RuntimeError):
    """The model produced an invalid or unsafe tool call."""


@dataclass(frozen=True)
class RequestedToolCall:
    call_id: str
    name: str
    arguments: str
    continuation: dict[str, Any]


@dataclass(frozen=True)
class ToolCallingModelCall:
    stage: str
    request_body: dict[str, Any]
    raw_response: dict[str, Any]
    latency_ms: int
    status_code: int | None = None


@dataclass(frozen=True)
class ToolCallingToolExecution:
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    duration_ms: int


@dataclass(frozen=True)
class ToolCallingResult:
    output_text: str
    endpoint: str
    request_body: dict[str, Any]
    raw_response: dict[str, Any]
    response_status_code: int
    model_latency_ms: int
    input_tokens: int
    output_tokens: int
    model_calls: tuple[ToolCallingModelCall, ...]
    tool_executions: tuple[ToolCallingToolExecution, ...]


class ToolCallingRunError(RuntimeError):
    """Failure with enough shared state for each business adapter to report it."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        request_body: dict[str, Any],
        raw_response: dict[str, Any],
        response_status_code: int | None,
        model_latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        model_calls: tuple[ToolCallingModelCall, ...],
        tool_executions: tuple[ToolCallingToolExecution, ...],
        provider_error: bool = False,
        retryable: bool = False,
        fatal: bool = False,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.request_body = request_body
        self.raw_response = raw_response
        self.response_status_code = response_status_code
        self.model_latency_ms = model_latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model_calls = model_calls
        self.tool_executions = tool_executions
        self.provider_error = provider_error
        self.retryable = retryable
        self.fatal = fatal


def responses_bazi_tool_definition() -> dict[str, Any]:
    definition = bazi_chart_tool_definition()
    return {
        "type": "function",
        "name": definition["name"],
        "description": definition["description"],
        "parameters": definition["input_schema"],
        "strict": True,
    }


def chat_bazi_tool_definition() -> dict[str, Any]:
    definition = bazi_chart_tool_definition()
    return {
        "type": "function",
        "function": {
            "name": definition["name"],
            "description": definition["description"],
            "parameters": definition["input_schema"],
        },
    }


def responses_tool_call(payload: dict[str, Any]) -> RequestedToolCall | None:
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    calls = [
        item
        for item in output
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    if not calls:
        return None
    if len(calls) > 1:
        raise ToolCallProtocolError("单轮模型响应只能调用一次八字排盘工具。")
    call = calls[0]
    call_id = call.get("call_id")
    name = call.get("name")
    arguments = call.get("arguments")
    if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
        raise ToolCallProtocolError("模型返回的工具调用结构不完整。")
    return RequestedToolCall(
        call_id=call_id,
        name=name,
        arguments=arguments,
        continuation=call,
    )


def chat_tool_call(payload: dict[str, Any]) -> RequestedToolCall | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return None
    if len(tool_calls) > 1:
        raise ToolCallProtocolError("单轮模型响应只能调用一次八字排盘工具。")
    call = tool_calls[0]
    function = call.get("function") if isinstance(call, dict) else None
    call_id = call.get("id") if isinstance(call, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
        raise ToolCallProtocolError("模型返回的工具调用结构不完整。")
    return RequestedToolCall(
        call_id=call_id,
        name=name,
        arguments=arguments,
        continuation={
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": tool_calls,
        },
    )


def validate_bazi_tool_input(
    call: RequestedToolCall,
    expected: BaziChartToolInput,
) -> BaziChartToolInput:
    if call.name != BAZI_CHART_TOOL_NAME:
        raise ToolCallProtocolError(f"模型调用了不允许的工具：{call.name}。")
    try:
        raw_arguments = json.loads(call.arguments)
        actual = BaziChartToolInput.model_validate(raw_arguments)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise ToolCallProtocolError("模型生成的排盘工具参数无效。") from exc
    if actual != expected:
        raise ToolCallProtocolError("模型擅自修改了排盘工具参数，已拒绝执行。")
    return actual


def _usage(payload: dict[str, Any], protocol: ApiProtocol) -> tuple[int, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    if protocol == "responses":
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {"unparsed_response": response.text[:50_000]}
    return payload if isinstance(payload, dict) else {"response_body": payload}


def _responses_output_text(payload: dict[str, Any]) -> str:
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
                raise ToolCallProtocolError("模型拒绝生成最终答案。")
            text = content.get("text")
            if content.get("type") == "output_text" and isinstance(text, str):
                return text
    raise ToolCallProtocolError("模型没有返回可用的最终答案。")


def _chat_output_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ToolCallProtocolError("模型没有返回可用的最终答案。")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip():
        return content
    if choices[0].get("finish_reason") == "length":
        raise ToolCallProtocolError("模型耗尽输出长度限制，未生成最终答案。")
    raise ToolCallProtocolError("模型没有返回可用的最终答案。")


async def run_tool_calling_agent(
    *,
    api_protocol: ApiProtocol,
    model: str,
    base_url: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    output_schema_name: str,
    output_schema: dict[str, Any],
    expected_tool_input: BaziChartToolInput,
    execute_tool: Callable[[BaziChartToolInput], BaziChartToolResult],
    client: httpx.AsyncClient,
) -> ToolCallingResult:
    """Run one shared native tool-calling loop and return the final text."""

    if api_protocol == "responses":
        endpoint = f"{base_url.rstrip('/')}/responses"
        responses_input: list[dict[str, Any]] = [
            {"role": "user", "content": user_prompt}
        ]
        chat_messages: list[dict[str, Any]] = []
    elif api_protocol == "chat_completions":
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        responses_input = []
        chat_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    else:
        raise ValueError(f"unsupported API protocol: {api_protocol}")

    model_calls: list[ToolCallingModelCall] = []
    tool_executions: list[ToolCallingToolExecution] = []
    total_latency_ms = 0
    total_input_tokens = 0
    total_output_tokens = 0
    last_request_body: dict[str, Any] = {}
    last_payload: dict[str, Any] = {}
    last_status_code: int | None = None

    def run_error(
        message: str,
        *,
        provider_error: bool = False,
        retryable: bool = False,
        fatal: bool = False,
    ) -> ToolCallingRunError:
        return ToolCallingRunError(
            message,
            endpoint=endpoint,
            request_body=last_request_body,
            raw_response=last_payload,
            response_status_code=last_status_code,
            model_latency_ms=total_latency_ms,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            model_calls=tuple(model_calls),
            tool_executions=tuple(tool_executions),
            provider_error=provider_error,
            retryable=retryable,
            fatal=fatal,
        )

    for _ in range(MAX_TOOL_CALLING_MODEL_CALLS):
        if api_protocol == "responses":
            request_body = {
                "model": model,
                "instructions": system_prompt,
                "input": deepcopy(responses_input),
                "tools": [responses_bazi_tool_definition()],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "store": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": output_schema_name,
                        "strict": True,
                        "schema": output_schema,
                    }
                },
            }
        else:
            request_body = {
                "model": model,
                "messages": deepcopy(chat_messages),
                "tools": [chat_bazi_tool_definition()],
                "tool_choice": "auto",
                "stream": False,
            }
        last_request_body = request_body

        started = perf_counter()
        try:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
        except httpx.TimeoutException as exc:
            total_latency_ms += round((perf_counter() - started) * 1000)
            raise run_error(
                "模型服务响应超时。",
                provider_error=True,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            total_latency_ms += round((perf_counter() - started) * 1000)
            raise run_error(
                "无法连接模型服务。",
                provider_error=True,
                retryable=True,
            ) from exc

        latency_ms = round((perf_counter() - started) * 1000)
        total_latency_ms += latency_ms
        status_code = response.status_code
        payload = _response_payload(response)
        last_payload = payload
        last_status_code = status_code
        input_tokens, output_tokens = _usage(payload, api_protocol)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

        if not response.is_success:
            model_calls.append(
                ToolCallingModelCall(
                    stage="error",
                    request_body=request_body,
                    raw_response=payload,
                    latency_ms=latency_ms,
                    status_code=status_code,
                )
            )
            if status_code in {401, 403}:
                message, retryable, fatal = "模型服务鉴权失败。", False, True
            elif status_code == 404:
                message, retryable, fatal = "模型接口或模型不存在。", False, True
            elif status_code == 429 or status_code >= 500:
                message = f"模型服务暂时不可用（HTTP {status_code}）。"
                retryable, fatal = True, False
            else:
                message = f"模型调用失败（HTTP {status_code}）。"
                retryable, fatal = False, False
            raise run_error(
                message,
                provider_error=True,
                retryable=retryable,
                fatal=fatal,
            )

        try:
            requested_call = (
                responses_tool_call(payload)
                if api_protocol == "responses"
                else chat_tool_call(payload)
            )
        except ToolCallProtocolError as exc:
            model_calls.append(
                ToolCallingModelCall(
                    stage="action_selection",
                    request_body=request_body,
                    raw_response=payload,
                    latency_ms=latency_ms,
                    status_code=status_code,
                )
            )
            raise run_error(str(exc)) from exc

        stage = "action_selection" if requested_call is not None else "final_answer"
        model_calls.append(
            ToolCallingModelCall(
                stage=stage,
                request_body=request_body,
                raw_response=payload,
                latency_ms=latency_ms,
                status_code=status_code,
            )
        )

        if requested_call is not None:
            try:
                tool_input = validate_bazi_tool_input(requested_call, expected_tool_input)
            except ToolCallProtocolError as exc:
                raise run_error(str(exc)) from exc

            tool_started = perf_counter()
            tool_result = execute_tool(tool_input)
            tool_duration_ms = round((perf_counter() - tool_started) * 1000)
            tool_output = tool_result.model_dump(mode="json")
            tool_executions.append(
                ToolCallingToolExecution(
                    name=BAZI_CHART_TOOL_NAME,
                    input=tool_input.model_dump(mode="json"),
                    output=tool_output,
                    duration_ms=tool_duration_ms,
                )
            )
            serialized_output = json.dumps(
                tool_output,
                ensure_ascii=False,
                separators=(",", ":"),
            )

            if api_protocol == "responses":
                continuation = payload.get("output")
                if not isinstance(continuation, list):
                    continuation = [requested_call.continuation]
                responses_input.extend(deepcopy(continuation))
                responses_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": requested_call.call_id,
                        "output": serialized_output,
                    }
                )
            else:
                chat_messages.append(deepcopy(requested_call.continuation))
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": requested_call.call_id,
                        "name": BAZI_CHART_TOOL_NAME,
                        "content": serialized_output,
                    }
                )
            continue

        try:
            output_text = (
                _responses_output_text(payload)
                if api_protocol == "responses"
                else _chat_output_text(payload)
            )
        except ToolCallProtocolError as exc:
            raise run_error(str(exc)) from exc

        return ToolCallingResult(
            output_text=output_text,
            endpoint=endpoint,
            request_body=request_body,
            raw_response=payload,
            response_status_code=status_code,
            model_latency_ms=total_latency_ms,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            model_calls=tuple(model_calls),
            tool_executions=tuple(tool_executions),
        )

    raise run_error(
        f"工具调用循环超过 {MAX_TOOL_CALLING_MODEL_CALLS} 次模型响应，已安全终止。"
    )
