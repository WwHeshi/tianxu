import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent_tools import (
    AgentTool,
    AgentToolAuthorizationError,
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


def test_registry_rejects_tools_outside_the_agent_allow_list() -> None:
    registry = AgentToolRegistry([echo_tool()])

    with pytest.raises(AgentToolAuthorizationError, match="不允许的工具"):
        registry.dispatch("delete_everything", "{}")


def test_registry_rejects_empty_and_duplicate_registrations() -> None:
    with pytest.raises(ValueError, match="at least one"):
        AgentToolRegistry([])
    with pytest.raises(ValueError, match="duplicate"):
        AgentToolRegistry([echo_tool(), echo_tool()])
