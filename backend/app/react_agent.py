"""Shared single-tool ReAct protocol helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .bazi.tool import (
    BAZI_CHART_TOOL_NAME,
    BaziChartToolInput,
    bazi_chart_tool_definition,
)

MAX_REACT_MODEL_CALLS = 10


class ReactProtocolError(RuntimeError):
    """The model produced an invalid or unsafe ReAct action."""


@dataclass(frozen=True)
class RequestedToolCall:
    call_id: str
    name: str
    arguments: str
    continuation: dict[str, Any]


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
        raise ReactProtocolError("单轮模型响应只能调用一次八字排盘工具。")
    call = calls[0]
    call_id = call.get("call_id")
    name = call.get("name")
    arguments = call.get("arguments")
    if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
        raise ReactProtocolError("模型返回的工具调用结构不完整。")
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
        raise ReactProtocolError("单轮模型响应只能调用一次八字排盘工具。")
    call = tool_calls[0]
    function = call.get("function") if isinstance(call, dict) else None
    call_id = call.get("id") if isinstance(call, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
        raise ReactProtocolError("模型返回的工具调用结构不完整。")
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
        raise ReactProtocolError(f"模型调用了不允许的工具：{call.name}。")
    try:
        raw_arguments = json.loads(call.arguments)
        actual = BaziChartToolInput.model_validate(raw_arguments)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise ReactProtocolError("模型生成的排盘工具参数无效。") from exc
    if actual != expected:
        raise ReactProtocolError("模型擅自修改了排盘工具参数，已拒绝执行。")
    return actual
