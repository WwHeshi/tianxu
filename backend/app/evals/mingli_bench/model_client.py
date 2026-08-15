"""MingLi evaluation configuration over the shared tool-calling agent."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from ...agent_capabilities import AgentCapability
from ...agent_tools import AgentToolRegistry
from ...agent_trace import snapshot_agent_trace
from ...bazi.fortune_tool import fortune_at_agent_tool
from ...bazi.tool import (
    BaziChartToolInput,
    BaziChartToolResult,
    bazi_chart_agent_tool,
    run_bazi_chart_tool,
)
from ...models import EvaluationRun
from ...tool_calling_agent import (
    ToolCallingRunError,
    run_tool_calling_agent,
)
from .context import (
    SYSTEM_PROMPT,
    chart_tool_input_for_question,
)
from .dataset import EvaluationQuestion

MODEL_TIMEOUT_SECONDS = 90.0
ANSWER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasoning_summary": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    "required": ["answer", "confidence", "reasoning_summary"],
    "additionalProperties": False,
}


class EvaluationAnswer(BaseModel):
    answer: str = Field(pattern="^[A-D]$")
    confidence: int = Field(ge=0, le=100)
    reasoning_summary: str = Field(min_length=1, max_length=500)


class EvaluationModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        fatal: bool = False,
        agent_trace: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal
        self.agent_trace = agent_trace
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass(frozen=True)
class EvaluationModelResult:
    answer: EvaluationAnswer
    agent_trace: dict[str, Any]
    latency_ms: int
    input_tokens: int
    output_tokens: int


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        stripped = "\n".join(lines).strip()
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model answer is not an object")
    return value


async def request_evaluation_answer(
    *,
    run: EvaluationRun,
    api_key: str,
    user_prompt: str,
    question: EvaluationQuestion,
    client: httpx.AsyncClient,
    chart_cache: dict[str, BaziChartToolResult] | None = None,
    capabilities: Iterable[AgentCapability] = (),
) -> EvaluationModelResult:
    expected_tool_input = chart_tool_input_for_question(question)
    if run.api_protocol not in {"responses", "chat_completions"}:
        raise EvaluationModelError("评测运行使用了不支持的模型协议", fatal=True)

    def execute_tool(tool_input: BaziChartToolInput) -> BaziChartToolResult:
        if chart_cache is not None:
            chart = chart_cache.get(question.case_id)
            if chart is not None:
                return chart
        chart = run_bazi_chart_tool(tool_input)
        if chart_cache is not None:
            chart_cache[question.case_id] = chart
        return chart

    try:
        execution = await run_tool_calling_agent(
            api_protocol=run.api_protocol,
            model=run.model,
            base_url=run.base_url,
            api_key=api_key,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema_name="mingli_evaluation_answer",
            output_schema=ANSWER_JSON_SCHEMA,
            tool_registry=AgentToolRegistry(
                [
                    bazi_chart_agent_tool(
                        expected_tool_input,
                        execute_tool=execute_tool,
                    ),
                    fortune_at_agent_tool(),
                ]
            ),
            client=client,
            capabilities=capabilities,
        )
    except ToolCallingRunError as exc:
        message = str(exc)
        if "耗尽输出长度限制" in message:
            message = "模型推理耗尽输出长度限制，未生成最终答案 JSON"
        raise EvaluationModelError(
            message,
            retryable=exc.retryable,
            fatal=exc.fatal,
            agent_trace=snapshot_agent_trace(
                body=exc.request_body,
                model_calls=exc.model_calls,
                tool_executions=exc.tool_executions,
            ),
            latency_ms=exc.model_latency_ms,
            input_tokens=exc.input_tokens,
            output_tokens=exc.output_tokens,
        ) from exc

    try:
        answer = EvaluationAnswer.model_validate(_json_object(execution.output_text))
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise EvaluationModelError(
            "模型返回的答案结构不符合约定",
            agent_trace=snapshot_agent_trace(
                body=execution.request_body,
                model_calls=execution.model_calls,
                tool_executions=execution.tool_executions,
            ),
            latency_ms=execution.model_latency_ms,
            input_tokens=execution.input_tokens,
            output_tokens=execution.output_tokens,
        ) from exc

    return EvaluationModelResult(
        answer=answer,
        agent_trace=snapshot_agent_trace(
            body=execution.request_body,
            model_calls=execution.model_calls,
            tool_executions=execution.tool_executions,
        ),
        latency_ms=execution.model_latency_ms,
        input_tokens=execution.input_tokens,
        output_tokens=execution.output_tokens,
    )


def model_timeout() -> httpx.Timeout:
    return httpx.Timeout(MODEL_TIMEOUT_SECONDS, connect=15.0)
