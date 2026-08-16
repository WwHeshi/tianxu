"""Multi-turn MingLi chat built on the shared tool-calling Agent."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from .agent_capabilities import AgentCapability
from .agent_tools import AgentToolRegistry
from .bazi.fortune_tool import fortune_at_agent_tool
from .bazi.tool import BaziChartToolInput, bazi_chart_agent_tool
from .models import ModelCredential
from .tool_calling_agent import (
    AgentStreamCallback,
    ToolCallingResult,
    ToolCallingRunError,
    run_tool_calling_agent,
)

CHAT_INSTRUCTIONS = """你是天序命理对话 Agent，负责回答八字命理及相关传统文化问题。

遵守以下原则：
1. 结合本次会话的上下文回答追问，不重复询问已经明确的信息。
2. 个性化命盘分析必须先使用 calculate_bazi_chart；涉及具体年份、月份或当前运势时，
   使用 calculate_fortune_at 查询对应时点。工具结果是命盘和运势的确定性事实，不得自行重排。
3. 查找论断依据时优先搜索规则图谱；需要核对书籍原文时，再搜索并阅读知识库。
4. 使用审慎、概率性的中文表述，不作疾病诊断、寿命判断、灾祸断言，也不提供确定性的
   法律、投资或医疗建议。
5. 回答应直接、清楚，不展示内部推理、工具参数或调用过程。"""

NO_CHART_INSTRUCTIONS = """

本会话没有绑定命盘。可以回答一般命理知识；用户明确提供性别和已经校正的真太阳出生时间时，
可以调用命盘工具。否则请用户先在排盘页生成命盘并点击“就此命盘提问”，不要猜测出生资料
或自行校正真太阳时。"""


class ChatAgentError(RuntimeError):
    """Safe chat-generation failure suitable for an HTTP response."""


@dataclass(frozen=True)
class ChatAgentResult:
    output_text: str
    execution: ToolCallingResult


async def run_chat_agent(
    *,
    credential: ModelCredential,
    api_key: str,
    user_message: str,
    history: Iterable[dict[str, str]],
    chart_input: BaziChartToolInput | None,
    capabilities: Iterable[AgentCapability],
    transport: httpx.AsyncBaseTransport | None = None,
    stream_callback: AgentStreamCallback | None = None,
) -> ChatAgentResult:
    system_prompt = CHAT_INSTRUCTIONS
    tools = [
        bazi_chart_agent_tool(chart_input),
        fortune_at_agent_tool(chart_input),
    ]
    if chart_input is None:
        system_prompt += NO_CHART_INSTRUCTIONS
    else:
        system_prompt += (
            "\n\n本会话已绑定命盘。调用命盘工具时必须原样使用："
            f"gender={chart_input.gender.value}，"
            "true_solar_datetime="
            f"{chart_input.true_solar_datetime.isoformat(timespec='seconds')}。"
        )

    timeout = httpx.Timeout(None, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            execution = await run_tool_calling_agent(
                api_protocol=credential.api_protocol,
                model=credential.model,
                base_url=credential.base_url,
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=user_message,
                conversation_history=history,
                output_schema_name=None,
                output_schema=None,
                client=client,
                tool_registry=AgentToolRegistry(tools),
                capabilities=capabilities,
                stream_callback=stream_callback,
            )
        except ToolCallingRunError as exc:
            raise ChatAgentError(str(exc)) from exc
    return ChatAgentResult(output_text=execution.output_text, execution=execution)
