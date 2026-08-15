import json
from dataclasses import dataclass

import httpx
import pytest
from pydantic import BaseModel

from app.agent_capabilities import (
    AgentCapabilityError,
    AgentCapabilityRegistry,
    AgentCapabilityResult,
)
from app.agent_tools import AgentTool
from app.tool_calling_agent import run_tool_calling_agent


class EmptyInput(BaseModel):
    pass


class EmptyOutput(BaseModel):
    ok: bool


def empty_tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description="Test tool.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        input_model=EmptyInput,
        execute=lambda _: EmptyOutput(ok=True),
    )


@dataclass
class FakeCapability:
    name: str
    section: str
    tool: AgentTool
    result_name: str | None = None

    def prompt_section(self) -> str:
        return self.section

    def tools(self) -> tuple[AgentTool, ...]:
        return (self.tool,)

    def finalize(self, output_text: str) -> AgentCapabilityResult:
        return AgentCapabilityResult(
            name=self.result_name or self.name,
            metadata={"output_length": len(output_text)},
            artifacts=(output_text,),
        )


def test_registry_composes_prompt_tools_and_finalizers_as_one_capability() -> None:
    registry = AgentCapabilityRegistry(
        (FakeCapability("memory", "动态目录", empty_tool("memory_search")),)
    )

    assert registry.apply_prompt("基础提示词") == "基础提示词\n\n动态目录"
    assert [tool.name for tool in registry.tools()] == ["memory_search"]
    assert registry.finalize("最终回答") == (
        AgentCapabilityResult(
            name="memory",
            metadata={"output_length": 4},
            artifacts=("最终回答",),
        ),
    )


def test_registry_rejects_duplicate_names_and_mismatched_results() -> None:
    capability = FakeCapability("memory", "目录", empty_tool("memory_search"))
    with pytest.raises(ValueError, match="duplicate Agent capability"):
        AgentCapabilityRegistry((capability, capability))

    mismatched = FakeCapability(
        "memory",
        "目录",
        empty_tool("memory_search"),
        result_name="other",
    )
    with pytest.raises(AgentCapabilityError, match="mismatched result"):
        AgentCapabilityRegistry((mismatched,)).finalize("最终回答")


@pytest.mark.asyncio
async def test_agent_can_register_only_a_capability_without_a_base_tool_registry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        if len(requests) == 1:
            assert body["instructions"] == "基础提示词\n\n动态目录"
            assert [tool["name"] for tool in body["tools"]] == ["memory_search"]
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_memory",
                            "name": "memory_search",
                            "arguments": "{}",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"answer":"完成"}'}
                        ],
                    }
                ]
            },
        )

    capability = FakeCapability(
        "memory",
        "动态目录",
        empty_tool("memory_search"),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://example.test",
    ) as client:
        result = await run_tool_calling_agent(
            api_protocol="responses",
            model="test-model",
            base_url="https://example.test/v1",
            api_key="sk-test",
            system_prompt="基础提示词",
            user_prompt="问题",
            output_schema_name="answer",
            output_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            client=client,
            capabilities=(capability,),
        )

    assert [execution.name for execution in result.tool_executions] == ["memory_search"]
    assert result.system_prompt == "基础提示词\n\n动态目录"
    assert result.capability_results[0].name == "memory"
