"""Provider-neutral Agent tool registration, validation and dispatch."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from pydantic import BaseModel, ValidationError

AgentToolOutput = dict[str, Any] | list[Any]


class AgentToolError(RuntimeError):
    """Base error raised while validating or dispatching an Agent tool call."""


class AgentToolInputError(AgentToolError):
    """The model supplied malformed arguments for an allowed tool."""


class AgentToolAuthorizationError(AgentToolError):
    """The model attempted to change server-bound tool arguments."""


class AgentToolExecutionError(AgentToolError):
    """An allowed tool failed while executing validated arguments."""


@dataclass(frozen=True)
class AgentTool:
    """One tool bound to the context and permissions of a specific Agent run."""

    name: str
    description: str
    input_schema: dict[str, Any]
    input_model: type[BaseModel]
    execute: Callable[[BaseModel], BaseModel | Awaitable[BaseModel]]
    authorize: Callable[[BaseModel], None] | None = None
    terminal: bool = False

    def responses_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
            "strict": True,
        }

    def chat_completions_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class AgentToolDispatchResult:
    """Validated input and serialized output from one dispatched tool call."""

    name: str
    input: dict[str, Any]
    output: AgentToolOutput
    terminal: bool


class AgentToolRegistry:
    """The explicit allow-list of tools available to one Agent invocation."""

    def __init__(self, tools: Iterable[AgentTool], *, allow_empty: bool = False) -> None:
        registered: dict[str, AgentTool] = {}
        for tool in tools:
            if not tool.name:
                raise ValueError("Agent tool name must not be empty")
            if tool.name in registered:
                raise ValueError(f"duplicate Agent tool name: {tool.name}")
            registered[tool.name] = tool
        if not registered and not allow_empty:
            raise ValueError("an Agent tool registry must contain at least one tool")
        self._tools = registered

    @classmethod
    def empty(cls) -> "AgentToolRegistry":
        """Create an empty base registry intended to be populated by capabilities."""

        return cls((), allow_empty=True)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def is_terminal(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool.terminal if tool is not None else False

    def extended(self, tools: Iterable[AgentTool]) -> "AgentToolRegistry":
        """Return a new registry containing the base tools and capability tools."""

        return AgentToolRegistry([*self._tools.values(), *tools], allow_empty=True)

    def definitions(self, protocol: str) -> list[dict[str, Any]]:
        if protocol == "responses":
            return [tool.responses_definition() for tool in self._tools.values()]
        if protocol == "chat_completions":
            return [tool.chat_completions_definition() for tool in self._tools.values()]
        raise ValueError(f"unsupported API protocol: {protocol}")

    def _validated_call(self, name: str, arguments: str) -> tuple[AgentTool, BaseModel]:
        tool = self._tools.get(name)
        if tool is None:
            raise AgentToolAuthorizationError(f"模型调用了不允许的工具：{name}。")

        try:
            raw_arguments = json.loads(arguments)
            tool_input = tool.input_model.model_validate(raw_arguments)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise AgentToolInputError(f"模型生成的 {name} 工具参数无效。") from exc

        if tool.authorize is not None:
            tool.authorize(tool_input)
        return tool, tool_input

    @staticmethod
    def _dispatch_result(
        tool: AgentTool,
        tool_input: BaseModel,
        tool_output: BaseModel,
    ) -> AgentToolDispatchResult:
        return AgentToolDispatchResult(
            name=tool.name,
            input=tool_input.model_dump(mode="json"),
            output=tool_output.model_dump(mode="json"),
            terminal=tool.terminal,
        )

    def dispatch(self, name: str, arguments: str) -> AgentToolDispatchResult:
        tool, tool_input = self._validated_call(name, arguments)
        try:
            tool_output = tool.execute(tool_input)
            if isawaitable(tool_output):
                close = getattr(tool_output, "close", None)
                if callable(close):
                    close()
                raise AgentToolExecutionError(
                    f"{name} 是异步工具，必须通过异步 Agent 调度器执行。"
                )
        except AgentToolError:
            raise
        except Exception as exc:
            raise AgentToolExecutionError(f"{name} 工具执行失败：{exc}") from exc
        return self._dispatch_result(tool, tool_input, tool_output)

    async def dispatch_async(self, name: str, arguments: str) -> AgentToolDispatchResult:
        tool, tool_input = self._validated_call(name, arguments)
        try:
            tool_output = tool.execute(tool_input)
            if isawaitable(tool_output):
                tool_output = await tool_output
        except AgentToolError:
            raise
        except Exception as exc:
            raise AgentToolExecutionError(f"{name} 工具执行失败：{exc}") from exc
        return self._dispatch_result(tool, tool_input, tool_output)
