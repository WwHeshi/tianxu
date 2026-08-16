"""Multi-turn MingLi chat built on the shared tool-calling Agent."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from .agent_capabilities import AgentCapability
from .agent_tools import AgentToolRegistry
from .bazi.fortune_tool import fortune_at_agent_tool
from .bazi.tool import (
    BaziChartToolInput,
    bazi_chart_agent_tool,
    run_bazi_chart_tool,
)
from .models import ModelCredential
from .tool_calling_agent import (
    AgentStreamCallback,
    ToolCallingResult,
    ToolCallingRunError,
    run_tool_calling_agent,
)

CHAT_INSTRUCTIONS = """你是天序命理对话 Agent，负责八字命理及相关传统文化问答。

工作规则：
1. 紧扣用户当前问题并承接对话历史；短问题简洁回答，不把每次追问都扩写成完整命盘报告。
2. 涉及某张命盘的结构或个性化判断时，本轮先调用 calculate_bazi_chart。以工具结果为
   权威命盘事实，不自行重排，也不把历史回答当作本轮工具结果。
3. 涉及具体年份、月份或当前运势时，调用 calculate_fortune_at 查询相应时点；先分析原局，
   再分析大运、流年和流月，不混淆不同层次。
4. 命理分析优先考察月令、旺衰、透藏、组合与十神，神煞只作辅助，不凭单一标签下结论。
5. 需要规则依据时优先搜索规则图谱；需要核对书籍原文时，再搜索并阅读知识库。
6. 明确区分工具给出的命盘事实与传统命理推断。推断使用审慎、概率性的中文表述，给出
   与问题直接相关的依据、边界和可执行建议。
7. 使用清晰的 Markdown 段落或列表组织回答，不展示内部推理、工具参数或调用过程。
   不作疾病诊断、寿命判断、灾祸断言，也不提供确定性的法律、投资或医疗建议。"""

NO_CHART_INSTRUCTIONS = """

【本会话没有绑定命盘】
- 一般命理知识可直接回答，不需要为了概念解释而调用排盘工具。
- 个性化分析只有在用户明确提供性别和已经校正的真太阳出生时间后才可排盘；否则请用户先在
  排盘页生成命盘并点击“就此命盘提问”。不要猜测出生资料或自行校正真太阳时。"""


def _bound_chart_instructions(chart_input: BaziChartToolInput) -> str:
    chart = run_bazi_chart_tool(chart_input)
    chart_datetime = chart_input.true_solar_datetime.isoformat(timespec="seconds")
    pillars = "　".join(
        pillar.heavenly_stem.symbol + pillar.earthly_branch.symbol
        for pillar in (chart.year, chart.month, chart.day, chart.hour)
    )
    gender = "男" if chart_input.gender.value == "male" else "女"
    return f"""

【本会话已绑定命盘】
- 固定对象：{gender}命；四柱索引：{pillars}；日主：{chart.day.heavenly_stem.symbol}。
- 权威排盘参数：gender={chart_input.gender.value}；
  true_solar_datetime={chart_datetime}。
- 用户所说的“我”“本人”“这个命盘”“此命”等，默认均指向这张命盘。不要再次询问已经绑定的
  出生资料，也不得用对话中出现的另一组资料替换它。
- 调用 calculate_bazi_chart 时原样使用上述两个参数；调用 calculate_fortune_at 时也原样使用
  它们，并只按用户所问的时间填写 as_of_datetime。
- 如果用户确实要分析另一张命盘，请说明应从对应排盘结果新建对话；当前会话继续保持原绑定。"""


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
        system_prompt += _bound_chart_instructions(chart_input)

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
