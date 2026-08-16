import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent_tools import (
    AgentTool,
    AgentToolAuthorizationError,
    AgentToolExecutionError,
    AgentToolInputError,
    AgentToolRegistry,
)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class EchoOutput(BaseModel):
    echoed: str


def echo_tool(name: str = "echo") -> AgentTool:
    def execute(payload: BaseModel) -> BaseModel:
        assert isinstance(payload, EchoInput)
        return EchoOutput(echoed=payload.value)

    return AgentTool(
        name=name,
        description="Echo one value.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        input_model=EchoInput,
        execute=execute,
    )


def test_registry_converts_all_allowed_tools_for_both_protocols() -> None:
    registry = AgentToolRegistry([echo_tool("first"), echo_tool("second")])

    responses = registry.definitions("responses")
    chat = registry.definitions("chat_completions")

    assert registry.names == ("first", "second")
    assert [item["name"] for item in responses] == ["first", "second"]
    assert all(item["strict"] is True for item in responses)
    assert [item["function"]["name"] for item in chat] == ["first", "second"]
    assert responses[0]["parameters"] == chat[0]["function"]["parameters"]


def test_registry_validates_and_dispatches_by_name() -> None:
    registry = AgentToolRegistry([echo_tool()])

    result = registry.dispatch("echo", json.dumps({"value": "hello"}))

    assert result.name == "echo"
    assert result.input == {"value": "hello"}
    assert result.output == {"echoed": "hello"}
    assert result.terminal is False


def test_registry_rejects_tools_outside_the_agent_allow_list() -> None:
    registry = AgentToolRegistry([echo_tool()])

    with pytest.raises(AgentToolAuthorizationError, match="不允许的工具"):
        registry.dispatch("delete_everything", "{}")


def test_registry_rejects_empty_and_duplicate_registrations() -> None:
    with pytest.raises(ValueError, match="at least one"):
        AgentToolRegistry([])
    with pytest.raises(ValueError, match="duplicate"):
        AgentToolRegistry([echo_tool(), echo_tool()])


@pytest.mark.asyncio
async def test_registry_dispatches_async_tools_without_breaking_sync_tools() -> None:
    async def execute(payload: BaseModel) -> BaseModel:
        assert isinstance(payload, EchoInput)
        return EchoOutput(echoed=f"async:{payload.value}")

    tool = AgentTool(
        name="async_echo",
        description="Echo asynchronously.",
        input_schema=EchoInput.model_json_schema(),
        input_model=EchoInput,
        execute=execute,
        terminal=True,
    )
    registry = AgentToolRegistry([tool])

    result = await registry.dispatch_async("async_echo", '{"value":"hello"}')

    assert result.output == {"echoed": "async:hello"}
    assert result.terminal is True
    assert registry.is_terminal("async_echo") is True
    with pytest.raises(AgentToolExecutionError, match="异步工具"):
        registry.dispatch("async_echo", '{"value":"hello"}')


@pytest.mark.asyncio
async def test_terminal_tool_can_return_detailed_input_error_without_terminating() -> None:
    def reject(_payload: BaseModel) -> BaseModel:
        raise AgentToolInputError("value：不在允许范围内。")

    tool = AgentTool(
        name="submit",
        description="Submit one value.",
        input_schema=EchoInput.model_json_schema(),
        input_model=EchoInput,
        execute=reject,
        terminal=True,
        return_input_errors=True,
    )
    registry = AgentToolRegistry([tool])

    invalid_schema = await registry.dispatch_async("submit", "{}")
    rejected_value = await registry.dispatch_async("submit", '{"value":"bad"}')

    assert invalid_schema.input == {}
    assert "value：Field required" in invalid_schema.output["error"]
    assert invalid_schema.terminal is False
    assert rejected_value.input == {"value": "bad"}
    assert "value：不在允许范围内" in rejected_value.output["error"]
    assert "重新调用 submit" in rejected_value.output["error"]
    assert rejected_value.terminal is False
