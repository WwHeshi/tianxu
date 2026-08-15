"""Compact storage and reconstruction for shared Agent execution traces."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .tool_calling_agent import ToolCallingModelCall, ToolCallingToolExecution


class AgentModelCallTrace(BaseModel):
    sequence: int = Field(ge=1)
    stage: str
    request_body: dict[str, Any]
    response_body: dict[str, Any]
    duration_ms: int
    tool_call_count: int = Field(default=0, ge=0)


class AgentToolExecutionTrace(BaseModel):
    sequence: int = Field(ge=1)
    name: str
    input: dict[str, Any]
    output: dict[str, Any] | list[Any]
    duration_ms: int | None = None


class StoredAgentModelCall(BaseModel):
    sequence: int = Field(ge=1)
    stage: str
    response_body: dict[str, Any]
    duration_ms: int
    tool_call_count: int = Field(default=0, ge=0)


class StoredAgentTrace(BaseModel):
    initial_request_body: dict[str, Any]
    model_calls: list[StoredAgentModelCall] = Field(default_factory=list)
    tool_executions: list[AgentToolExecutionTrace] = Field(default_factory=list)


def snapshot_agent_trace(
    *,
    body: dict[str, Any],
    model_calls: tuple[ToolCallingModelCall, ...],
    tool_executions: tuple[ToolCallingToolExecution, ...],
) -> dict[str, Any]:
    """Store one Agent run without duplicating every reconstructed request body."""

    initial_request_body = model_calls[0].request_body if model_calls else body
    return StoredAgentTrace(
        initial_request_body=initial_request_body,
        model_calls=[
            StoredAgentModelCall(
                sequence=sequence,
                stage=call.stage,
                response_body=call.raw_response,
                duration_ms=call.latency_ms,
                tool_call_count=call.tool_call_count,
            )
            for sequence, call in enumerate(model_calls, start=1)
        ],
        tool_executions=[
            AgentToolExecutionTrace(
                sequence=sequence,
                name=execution.name,
                input=execution.input,
                output=execution.output,
                duration_ms=execution.duration_ms,
            )
            for sequence, execution in enumerate(tool_executions, start=1)
        ],
    ).model_dump(mode="json")


def _next_request_body(
    current: dict[str, Any],
    response: dict[str, Any],
    *,
    api_protocol: str,
    tool_executions: list[AgentToolExecutionTrace],
) -> dict[str, Any]:
    next_body = deepcopy(current)
    if api_protocol == "responses":
        response_output = response.get("output")
        continuation = deepcopy(response_output) if isinstance(response_output, list) else []
        request_input = next_body.setdefault("input", [])
        request_input.extend(continuation)
        function_calls = [
            value
            for value in continuation
            if isinstance(value, dict) and value.get("type") == "function_call"
        ]
        for call, execution in zip(function_calls, tool_executions, strict=False):
            request_input.append(
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": json.dumps(
                        execution.output,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
        return next_body

    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else []
    messages = next_body.setdefault("messages", [])
    messages.append(
        {
            "role": "assistant",
            "content": message.get("content") if isinstance(message, dict) else None,
            "tool_calls": deepcopy(tool_calls),
        }
    )
    for call, execution in zip(tool_calls, tool_executions, strict=False):
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id") if isinstance(call, dict) else None,
                "name": execution.name,
                "content": json.dumps(
                    execution.output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    return next_body


def _action_tool_call_count(response: dict[str, Any], api_protocol: str) -> int:
    if api_protocol == "responses":
        output = response.get("output")
        if not isinstance(output, list):
            return 0
        return sum(
            isinstance(value, dict) and value.get("type") == "function_call" for value in output
        )
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else []
    return len(tool_calls) if isinstance(tool_calls, list) else 0


def trace_model_calls(
    trace: StoredAgentTrace,
    *,
    api_protocol: str,
) -> list[AgentModelCallTrace]:
    model_calls: list[AgentModelCallTrace] = []
    request_body = deepcopy(trace.initial_request_body)
    tool_index = 0
    for stored_call in trace.model_calls:
        tool_call_count = stored_call.tool_call_count
        if stored_call.stage == "action_selection" and tool_call_count == 0:
            tool_call_count = _action_tool_call_count(
                stored_call.response_body,
                api_protocol,
            )
        model_calls.append(
            AgentModelCallTrace(
                sequence=stored_call.sequence,
                stage=stored_call.stage,
                request_body=deepcopy(request_body),
                response_body=stored_call.response_body,
                duration_ms=stored_call.duration_ms,
                tool_call_count=tool_call_count,
            )
        )
        call_tools = trace.tool_executions[tool_index : tool_index + tool_call_count]
        tool_index += len(call_tools)
        if stored_call.stage == "action_selection":
            request_body = _next_request_body(
                request_body,
                stored_call.response_body,
                api_protocol=api_protocol,
                tool_executions=call_tools,
            )
    return model_calls


def trace_prompts(
    initial_request_body: dict[str, Any],
    api_protocol: str,
) -> tuple[str | None, str | None]:
    if api_protocol == "responses":
        system_prompt = initial_request_body.get("instructions")
        request_input = initial_request_body.get("input")
        user_prompt = None
        if isinstance(request_input, list):
            user_prompt = next(
                (
                    value.get("content")
                    for value in request_input
                    if isinstance(value, dict)
                    and value.get("role") == "user"
                    and isinstance(value.get("content"), str)
                ),
                None,
            )
        return system_prompt if isinstance(system_prompt, str) else None, user_prompt

    messages = initial_request_body.get("messages")
    if not isinstance(messages, list):
        return None, None

    def content_for(role: str) -> str | None:
        contents = [
            value["content"]
            for value in messages
            if isinstance(value, dict)
            and value.get("role") == role
            and isinstance(value.get("content"), str)
        ]
        return "\n\n".join(contents) if contents else None

    return content_for("system"), content_for("user")
