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
    return_input_errors: bool = False

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
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AgentToolInputError("参数不是有效的 JSON。") from exc
        try:
            tool_input = tool.input_model.model_validate(raw_arguments)
        except ValidationError as exc:
            details = []
            for error in exc.errors(include_url=False)[:10]:
                location = ".".join(str(value) for value in error["loc"])
                details.append(f"{location or '参数'}：{error['msg']}")
            remaining = len(exc.errors()) - len(details)
            if remaining > 0:
                details.append(f"另有 {remaining} 处错误")
            raise AgentToolInputError(
                f"参数校验未通过：{'；'.join(details)}。"
            ) from exc

        if tool.authorize is not None:
            tool.authorize(tool_input)
        return tool, tool_input

    @staticmethod
    def _unvalidated_input(arguments: str) -> dict[str, Any]:
        try:
            value = json.loads(arguments)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {"arguments": arguments}
        return value if isinstance(value, dict) else {"arguments": value}

    @classmethod
    def _input_error_result(
        cls,
        tool: AgentTool,
        arguments: str,
        error: AgentToolInputError,
        tool_input: BaseModel | None = None,
    ) -> AgentToolDispatchResult:
        return AgentToolDispatchResult(
            name=tool.name,
            input=(
                tool_input.model_dump(mode="json")
                if tool_input is not None
                else cls._unvalidated_input(arguments)
            ),
            output={
                "error": (
                    f"{tool.name} 提交失败：{error}请修正上述错误后重新调用"
                    f" {tool.name}。"
                )
            },
            terminal=False,
        )

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
        try:
            tool, tool_input = self._validated_call(name, arguments)
        except AgentToolInputError as exc:
            tool = self._tools[name]
            if tool.return_input_errors:
                return self._input_error_result(tool, arguments, exc)
            raise
        try:
            tool_output = tool.execute(tool_input)
            if isawaitable(tool_output):
                close = getattr(tool_output, "close", None)
                if callable(close):
                    close()
                raise AgentToolExecutionError(
                    f"{name} 是异步工具，必须通过异步 Agent 调度器执行。"
                )
        except AgentToolInputError as exc:
            if tool.return_input_errors:
                return self._input_error_result(tool, arguments, exc, tool_input)
            raise
        except AgentToolError:
            raise
        except Exception as exc:
            raise AgentToolExecutionError(f"{name} 工具执行失败：{exc}") from exc
        return self._dispatch_result(tool, tool_input, tool_output)

    async def dispatch_async(self, name: str, arguments: str) -> AgentToolDispatchResult:
        try:
            tool, tool_input = self._validated_call(name, arguments)
        except AgentToolInputError as exc:
            tool = self._tools[name]
            if tool.return_input_errors:
                return self._input_error_result(tool, arguments, exc)
            raise
        try:
            tool_output = tool.execute(tool_input)
            if isawaitable(tool_output):
                tool_output = await tool_output
        except AgentToolInputError as exc:
            if tool.return_input_errors:
                return self._input_error_result(tool, arguments, exc, tool_input)
            raise
        except AgentToolError:
            raise
        except Exception as exc:
            raise AgentToolExecutionError(f"{name} 工具执行失败：{exc}") from exc
        return self._dispatch_result(tool, tool_input, tool_output)
