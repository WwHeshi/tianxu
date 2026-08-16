"""Shared provider-neutral tool-calling loop for Tianxu agents."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
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
ConversationHistory = Iterable[dict[str, str]]
StreamEventType = Literal["output_delta", "output_reset", "tool_started", "tool_completed"]


@dataclass(frozen=True)
class ToolCallingStreamEvent:
    type: StreamEventType
    text: str | None = None
    tool_name: str | None = None


AgentStreamCallback = Callable[[ToolCallingStreamEvent], Awaitable[None] | None]


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
        item for item in output if isinstance(item, dict) and item.get("type") == "function_call"
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


async def _emit_stream_event(
    callback: AgentStreamCallback | None,
    event: ToolCallingStreamEvent,
) -> None:
    if callback is None:
        return
    pending = callback(event)
    if pending is not None:
        await pending


def _stream_json(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("event:") or stripped.startswith(":"):
        return None
    if stripped.startswith("data:"):
        stripped = stripped[5:].strip()
    if not stripped or stripped == "[DONE]":
        return None
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _visible_output_delta(delta: str, *, emitted_text: bool) -> str:
    """Drop whitespace only at the beginning of one model response."""

    return delta if emitted_text else delta.lstrip()


async def _responses_stream_payload(
    response: httpx.Response,
    callback: AgentStreamCallback,
) -> tuple[dict[str, Any], bool]:
    completed: dict[str, Any] | None = None
    items: dict[int, dict[str, Any]] = {}
    text_parts: dict[int, list[str]] = {}
    argument_parts: dict[int, list[str]] = {}
    emitted_text = False
    stream_error: str | None = None

    async for line in response.aiter_lines():
        event = _stream_json(line)
        if event is None:
            continue
        if isinstance(event.get("output"), list) and "type" not in event:
            completed = event
            continue

        event_type = event.get("type")
        if event_type in {"response.completed", "response.failed", "response.incomplete"}:
            candidate = event.get("response")
            if isinstance(candidate, dict):
                completed = candidate
            continue
        if event_type == "error":
            error = event.get("error")
            if isinstance(error, dict):
                stream_error = str(error.get("message") or error)
            else:
                stream_error = str(event.get("message") or error or "模型流式响应失败")
            continue

        output_index = int(event.get("output_index") or 0)
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item")
            if isinstance(item, dict):
                items[output_index] = deepcopy(item)
            continue
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                text_parts.setdefault(output_index, []).append(delta)
                visible_delta = _visible_output_delta(delta, emitted_text=emitted_text)
                if visible_delta:
                    emitted_text = True
                    await _emit_stream_event(
                        callback,
                        ToolCallingStreamEvent(type="output_delta", text=visible_delta),
                    )
            continue
        if event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str) and output_index not in text_parts:
                text_parts[output_index] = [text]
            continue
        if event_type == "response.function_call_arguments.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                argument_parts.setdefault(output_index, []).append(delta)
            continue
        if event_type == "response.function_call_arguments.done":
            arguments = event.get("arguments")
            if isinstance(arguments, str):
                argument_parts[output_index] = [arguments]

    if completed is not None:
        return completed, emitted_text
    if stream_error is not None:
        return {"stream_error": stream_error}, emitted_text

    for output_index, parts in text_parts.items():
        item = items.setdefault(output_index, {"type": "message", "role": "assistant"})
        item["content"] = [{"type": "output_text", "text": "".join(parts)}]
    for output_index, parts in argument_parts.items():
        item = items.setdefault(output_index, {"type": "function_call"})
        item["arguments"] = "".join(parts)
    output = [items[index] for index in sorted(items)]
    return {"output": output}, emitted_text


async def _chat_stream_payload(
    response: httpx.Response,
    callback: AgentStreamCallback,
) -> tuple[dict[str, Any], bool]:
    response_values: dict[str, Any] = {}
    message: dict[str, Any] = {"role": "assistant", "content": ""}
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    emitted_text = False
    full_payload: dict[str, Any] | None = None
    stream_error: str | None = None

    async for line in response.aiter_lines():
        chunk = _stream_json(line)
        if chunk is None:
            continue
        if isinstance(chunk.get("error"), dict):
            error = chunk["error"]
            stream_error = str(error.get("message") or error)
            continue
        choices = chunk.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            if isinstance(choices[0].get("message"), dict):
                full_payload = chunk
                continue
            choice = choices[0]
            delta = choice.get("delta")
            if isinstance(delta, dict):
                if isinstance(delta.get("role"), str):
                    message["role"] = delta["role"]
                content = delta.get("content")
                if isinstance(content, str) and content:
                    message["content"] = str(message.get("content") or "") + content
                    visible_content = _visible_output_delta(
                        content,
                        emitted_text=emitted_text,
                    )
                    if visible_content:
                        emitted_text = True
                        await _emit_stream_event(
                            callback,
                            ToolCallingStreamEvent(
                                type="output_delta",
                                text=visible_content,
                            ),
                        )
                for key in _CHAT_REASONING_KEYS:
                    value = delta.get(key)
                    if isinstance(value, str):
                        message[key] = str(message.get(key) or "") + value
                raw_tool_calls = delta.get("tool_calls")
                if isinstance(raw_tool_calls, list):
                    for raw_call in raw_tool_calls:
                        if not isinstance(raw_call, dict):
                            continue
                        index = int(raw_call.get("index") or 0)
                        call = tool_calls.setdefault(
                            index,
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        if isinstance(raw_call.get("id"), str):
                            call["id"] = raw_call["id"]
                        if isinstance(raw_call.get("type"), str):
                            call["type"] = raw_call["type"]
                        function = raw_call.get("function")
                        if isinstance(function, dict):
                            target = call["function"]
                            if isinstance(function.get("name"), str):
                                target["name"] += function["name"]
                            if isinstance(function.get("arguments"), str):
                                target["arguments"] += function["arguments"]
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        for key, value in chunk.items():
            if key not in {"choices", "usage"}:
                response_values[key] = value

    if full_payload is not None:
        return full_payload, emitted_text
    if stream_error is not None:
        return {"stream_error": stream_error}, emitted_text
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    payload = {
        **response_values,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload, emitted_text


async def _post_streaming_model(
    *,
    client: httpx.AsyncClient,
    endpoint: str,
    headers: dict[str, str],
    request_body: dict[str, Any],
    api_protocol: ApiProtocol,
    callback: AgentStreamCallback,
) -> tuple[int, bool, dict[str, Any], bool]:
    async with client.stream(
        "POST",
        endpoint,
        headers=headers,
        json=request_body,
    ) as response:
        if not response.is_success:
            await response.aread()
            return response.status_code, False, _response_payload(response), False
        if api_protocol == "responses":
            payload, emitted_text = await _responses_stream_payload(response, callback)
        else:
            payload, emitted_text = await _chat_stream_payload(response, callback)
        return response.status_code, True, payload, emitted_text


def _normalized_conversation_history(
    history: ConversationHistory,
) -> list[dict[str, str]]:
    """Validate the small, provider-neutral history persisted by chat features."""

    normalized: list[dict[str, str]] = []
    for item in history:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            raise ValueError("conversation history roles must be user or assistant")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("conversation history content must not be empty")
        normalized.append({"role": role, "content": content})
    return normalized


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
    conversation_history: ConversationHistory = (),
    output_schema_name: str | None,
    output_schema: dict[str, Any] | None,
    client: httpx.AsyncClient,
    tool_registry: AgentToolRegistry | None = None,
    capabilities: Iterable[AgentCapability] = (),
    timeout_seconds: float | None = None,
    stream_callback: AgentStreamCallback | None = None,
) -> ToolCallingResult:
    """Run a tool loop with explicitly registered tools and complete capabilities."""

    if (output_schema_name is None) != (output_schema is None):
        raise ValueError("output schema name and schema must be provided together")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    capability_registry = AgentCapabilityRegistry(tuple(capabilities))
    system_prompt = capability_registry.apply_prompt(system_prompt)
    tool_registry = (tool_registry or AgentToolRegistry.empty()).extended(
        capability_registry.tools()
    )
    normalized_history = _normalized_conversation_history(conversation_history)

    if api_protocol == "responses":
        endpoint = f"{base_url.rstrip('/')}/responses"
        responses_input: list[dict[str, Any]] = [
            *normalized_history,
            {"role": "user", "content": user_prompt},
        ]
        chat_messages: list[dict[str, Any]] = []
    elif api_protocol == "chat_completions":
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        responses_input = []
        chat_messages = [
            {"role": "system", "content": system_prompt},
            *normalized_history,
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
    deadline = perf_counter() + timeout_seconds if timeout_seconds is not None else None

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

    def timeout_error() -> ToolCallingRunError:
        assert timeout_seconds is not None
        if timeout_seconds >= 60 and timeout_seconds % 60 == 0:
            duration = f"{timeout_seconds / 60:g} 分钟"
        else:
            duration = f"{timeout_seconds:g} 秒"
        return run_error(f"Agent Session 运行超过 {duration}。", fatal=True)

    def remaining_timeout() -> float | None:
        if deadline is None:
            return None
        remaining = deadline - perf_counter()
        if remaining <= 0:
            raise timeout_error()
        return remaining

    while True:
        remaining_timeout()
        if api_protocol == "responses":
            request_body = {
                "model": model,
                "instructions": system_prompt,
                "input": deepcopy(responses_input),
                "store": False,
                "include": ["reasoning.encrypted_content"],
            }
            if stream_callback is not None:
                request_body["stream"] = True
            if output_schema_name is not None and output_schema is not None:
                request_body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": output_schema_name,
                        "strict": True,
                        "schema": output_schema,
                    }
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
                "stream": stream_callback is not None,
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

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async def send_model_request() -> tuple[int, bool, dict[str, Any], bool]:
            if stream_callback is not None:
                return await _post_streaming_model(
                    client=client,
                    endpoint=endpoint,
                    headers=headers,
                    request_body=request_body,
                    api_protocol=api_protocol,
                    callback=stream_callback,
                )
            response = await client.post(
                endpoint,
                headers=headers,
                json=request_body,
            )
            return (
                response.status_code,
                response.is_success,
                _response_payload(response),
                False,
            )

        started = perf_counter()
        try:
            remaining = remaining_timeout()
            if remaining is None:
                status_code, response_success, payload, streamed_text = (
                    await send_model_request()
                )
            else:
                async with asyncio.timeout(remaining):
                    status_code, response_success, payload, streamed_text = (
                        await send_model_request()
                    )
        except TimeoutError as exc:
            total_latency_ms += round((perf_counter() - started) * 1000)
            raise timeout_error() from exc
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
        last_payload = payload
        last_status_code = status_code
        input_tokens, output_tokens = _usage(payload, api_protocol)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

        if not response_success:
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

        stream_error = payload.get("stream_error")
        if isinstance(stream_error, str):
            model_calls.append(
                ToolCallingModelCall(
                    stage="error",
                    request_body=request_body,
                    raw_response=payload,
                    latency_ms=latency_ms,
                    status_code=status_code,
                )
            )
            raise run_error(stream_error, provider_error=True, retryable=True)

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
            await _emit_stream_event(
                stream_callback,
                ToolCallingStreamEvent(type="output_reset"),
            )
            terminal_requested = [
                requested_call
                for requested_call in requested_calls
                if tool_registry.is_terminal(requested_call.name)
            ]
            if terminal_requested and len(requested_calls) != 1:
                raise run_error("终止工具必须单独调用。")

            dispatched_calls = []
            for requested_call in requested_calls:
                await _emit_stream_event(
                    stream_callback,
                    ToolCallingStreamEvent(
                        type="tool_started",
                        tool_name=requested_call.name,
                    ),
                )
                tool_started = perf_counter()
                try:
                    remaining = remaining_timeout()
                    if remaining is None:
                        dispatched = await tool_registry.dispatch_async(
                            requested_call.name,
                            requested_call.arguments,
                        )
                    else:
                        async with asyncio.timeout(remaining):
                            dispatched = await tool_registry.dispatch_async(
                                requested_call.name,
                                requested_call.arguments,
                            )
                except TimeoutError as exc:
                    raise timeout_error() from exc
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
                await _emit_stream_event(
                    stream_callback,
                    ToolCallingStreamEvent(
                        type="tool_completed",
                        tool_name=dispatched.name,
                    ),
                )
                dispatched_calls.append((requested_call, dispatched))

            terminal_calls = [
                dispatched for _, dispatched in dispatched_calls if dispatched.terminal
            ]
            if terminal_calls:
                try:
                    capability_results = capability_registry.finalize("")
                except AgentCapabilityError as exc:
                    raise run_error(str(exc)) from exc
                return ToolCallingResult(
                    output_text="",
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
            ).lstrip()
        except ToolCallProtocolError as exc:
            raise run_error(str(exc)) from exc

        if stream_callback is not None and not streamed_text:
            await _emit_stream_event(
                stream_callback,
                ToolCallingStreamEvent(type="output_delta", text=output_text),
            )

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
