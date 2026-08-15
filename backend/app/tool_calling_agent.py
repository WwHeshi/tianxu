"""Shared provider-neutral tool-calling loop for Tianxu agents."""

from __future__ import annotations

import json
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

import httpx

from .agent_capabilities import (
    AgentCapability,
    AgentCapabilityError,
    AgentCapabilityRegistry,
    AgentCapabilityResult,
)
from .agent_tools import (
    AgentToolError,
    AgentToolOutput,
    AgentToolRegistry,
)

ApiProtocol = Literal["responses", "chat_completions"]


class ToolCallProtocolError(RuntimeError):
    """The model produced an invalid or unsafe tool call."""


@dataclass(frozen=True)
class RequestedToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolCallingModelCall:
    stage: str
    request_body: dict[str, Any]
    raw_response: dict[str, Any]
    latency_ms: int
    status_code: int | None = None
    tool_call_count: int = 0


@dataclass(frozen=True)
class ToolCallingToolExecution:
    name: str
    input: dict[str, Any]
    output: AgentToolOutput
    duration_ms: int


@dataclass(frozen=True)
class ToolCallingResult:
    output_text: str
    system_prompt: str
    endpoint: str
    request_body: dict[str, Any]
    raw_response: dict[str, Any]
    response_status_code: int
    model_latency_ms: int
    input_tokens: int
    output_tokens: int
    model_calls: tuple[ToolCallingModelCall, ...]
    tool_executions: tuple[ToolCallingToolExecution, ...]
    capability_results: tuple[AgentCapabilityResult, ...] = ()


class ToolCallingRunError(RuntimeError):
    """Failure with enough shared state for each business adapter to report it."""

    def __init__(
        self,
        message: str,
        *,
        system_prompt: str,
        endpoint: str,
        request_body: dict[str, Any],
        raw_response: dict[str, Any],
        response_status_code: int | None,
        model_latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        model_calls: tuple[ToolCallingModelCall, ...],
        tool_executions: tuple[ToolCallingToolExecution, ...],
        capability_results: tuple[AgentCapabilityResult, ...] = (),
        provider_error: bool = False,
        retryable: bool = False,
        fatal: bool = False,
    ) -> None:
        super().__init__(message)
        self.system_prompt = system_prompt
        self.endpoint = endpoint
        self.request_body = request_body
        self.raw_response = raw_response
        self.response_status_code = response_status_code
        self.model_latency_ms = model_latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model_calls = model_calls
        self.tool_executions = tool_executions
        self.capability_results = capability_results
        self.provider_error = provider_error
        self.retryable = retryable
        self.fatal = fatal


_CHAT_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")


def model_response_history_items(
    payload: dict[str, Any],
    api_protocol: ApiProtocol,
) -> tuple[dict[str, Any], ...]:
    """Build provider-native history for any later tool or user turn.

    Reasoning is replayed whenever a subsequent model request exists, regardless
    of whether that request is caused by a tool result or a future user follow-up.
    """

    if api_protocol == "responses":
        output = payload.get("output")
        if not isinstance(output, list):
            return ()
        return tuple(deepcopy(item) for item in output if isinstance(item, dict))

    if api_protocol == "chat_completions":
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return ()
        message = choices[0].get("message")
        if not isinstance(message, dict):
            return ()
        history_message = {
            "role": "assistant",
            "content": deepcopy(message.get("content")),
        }
        for key in (*_CHAT_REASONING_KEYS, "tool_calls"):
            if key in message:
                history_message[key] = deepcopy(message[key])
        return (history_message,)

    raise ValueError(f"unsupported API protocol: {api_protocol}")


def responses_tool_calls(payload: dict[str, Any]) -> tuple[RequestedToolCall, ...]:
    output = payload.get("output")
    if not isinstance(output, list):
        return ()
    calls = [
        item
        for item in output
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    if not calls:
        return ()
    requested: list[RequestedToolCall] = []
    for call in calls:
        call_id = call.get("call_id")
        name = call.get("name")
        arguments = call.get("arguments")
        if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
            raise ToolCallProtocolError("模型返回的工具调用结构不完整。")
        requested.append(
            RequestedToolCall(
                call_id=call_id,
                name=name,
                arguments=arguments,
            )
        )
    return tuple(requested)


def chat_tool_calls(payload: dict[str, Any]) -> tuple[RequestedToolCall, ...]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ()
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ()
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return ()
    requested = []
    for call in tool_calls:
        function = call.get("function") if isinstance(call, dict) else None
        call_id = call.get("id") if isinstance(call, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
            raise ToolCallProtocolError("模型返回的工具调用结构不完整。")
        requested.append(
            RequestedToolCall(
                call_id=call_id,
                name=name,
                arguments=arguments,
            )
        )
    return tuple(requested)


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
    client: httpx.AsyncClient,
    tool_registry: AgentToolRegistry | None = None,
    capabilities: Iterable[AgentCapability] = (),
) -> ToolCallingResult:
    """Run a tool loop with explicitly registered tools and complete capabilities."""

    capability_registry = AgentCapabilityRegistry(tuple(capabilities))
    system_prompt = capability_registry.apply_prompt(system_prompt)
    tool_registry = (tool_registry or AgentToolRegistry.empty()).extended(
        capability_registry.tools()
    )

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
            system_prompt=system_prompt,
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

    while True:
        if api_protocol == "responses":
            request_body = {
                "model": model,
                "instructions": system_prompt,
                "input": deepcopy(responses_input),
                "store": False,
                "include": ["reasoning.encrypted_content"],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": output_schema_name,
                        "strict": True,
                        "schema": output_schema,
                    }
                },
            }
            if tool_registry.names:
                request_body.update(
                    {
                        "tools": tool_registry.definitions("responses"),
                        "tool_choice": "auto",
                        "parallel_tool_calls": True,
                    }
                )
        else:
            request_body = {
                "model": model,
                "messages": deepcopy(chat_messages),
                "stream": False,
            }
            if tool_registry.names:
                request_body.update(
                    {
                        "tools": tool_registry.definitions("chat_completions"),
                        "tool_choice": "auto",
                        "parallel_tool_calls": True,
                    }
                )
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
            requested_calls = (
                responses_tool_calls(payload)
                if api_protocol == "responses"
                else chat_tool_calls(payload)
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

        stage = "action_selection" if requested_calls else "final_answer"
        model_calls.append(
            ToolCallingModelCall(
                stage=stage,
                request_body=request_body,
                raw_response=payload,
                latency_ms=latency_ms,
                status_code=status_code,
                tool_call_count=len(requested_calls),
            )
        )

        if requested_calls:
            dispatched_calls = []
            for requested_call in requested_calls:
                tool_started = perf_counter()
                try:
                    dispatched = tool_registry.dispatch(
                        requested_call.name,
                        requested_call.arguments,
                    )
                except AgentToolError as exc:
                    raise run_error(str(exc)) from exc
                tool_duration_ms = round((perf_counter() - tool_started) * 1000)
                tool_executions.append(
                    ToolCallingToolExecution(
                        name=dispatched.name,
                        input=dispatched.input,
                        output=dispatched.output,
                        duration_ms=tool_duration_ms,
                    )
                )
                dispatched_calls.append((requested_call, dispatched))

            if api_protocol == "responses":
                responses_input.extend(model_response_history_items(payload, api_protocol))
                responses_input.extend(
                    {
                        "type": "function_call_output",
                        "call_id": requested_call.call_id,
                        "output": json.dumps(
                            dispatched.output,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                    for requested_call, dispatched in dispatched_calls
                )
            else:
                chat_messages.extend(model_response_history_items(payload, api_protocol))
                chat_messages.extend(
                    {
                        "role": "tool",
                        "tool_call_id": requested_call.call_id,
                        "name": dispatched.name,
                        "content": json.dumps(
                            dispatched.output,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                    for requested_call, dispatched in dispatched_calls
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

        try:
            capability_results = capability_registry.finalize(output_text)
        except AgentCapabilityError as exc:
            raise run_error(str(exc)) from exc

        return ToolCallingResult(
            output_text=output_text,
            system_prompt=system_prompt,
            endpoint=endpoint,
            request_body=request_body,
            raw_response=payload,
            response_status_code=status_code,
            model_latency_ms=total_latency_ms,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            model_calls=tuple(model_calls),
            tool_executions=tuple(tool_executions),
            capability_results=capability_results,
        )
