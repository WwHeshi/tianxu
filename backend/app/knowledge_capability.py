"""Reusable knowledge capability binding its prompt and tools."""

from __future__ import annotations

from .agent_capabilities import AgentCapabilityResult
from .agent_tools import AgentTool
from .knowledge_tools import KnowledgeToolSession

KNOWLEDGE_CAPABILITY_NAME = "knowledge"

KNOWLEDGE_INSTRUCTIONS = """知识库能力已启用。
search_knowledge 用于定位书籍原文，read_knowledge 用于阅读命中位置的上下文。
需要用资料支持判断时，必须遵守：
1. 使用 search_knowledge 一次提交 1 至 6 个相关术语或短语，查看最多 5 条定位结果。
2. 只有通过本次提供的知识库工具实际搜索并使用 read_knowledge 阅读过的资料才可以采用；
   不得凭记忆虚构原文、书名、作者或引文。搜索结果只是定位线索，必要时继续阅读前后页。
3. 未找到相关资料时应说明资料不足，不能为了引用而使用无关内容。
4. 原文、注解和现代资料不得混称；目录信息不足以判断资料性质时，只按实际书名陈述。"""

NO_KNOWLEDGE_INSTRUCTIONS = """知识库能力已注册，但本次没有可用资料。
不得声称引用了任何书籍或原文。"""


class KnowledgeCapability:
    """One run-scoped registration for the knowledge prompt and tools."""

    def __init__(self, session: KnowledgeToolSession) -> None:
        self.session = session

    @property
    def name(self) -> str:
        return KNOWLEDGE_CAPABILITY_NAME

    def prompt_section(self) -> str:
        if not self.session.available:
            return NO_KNOWLEDGE_INSTRUCTIONS
        return KNOWLEDGE_INSTRUCTIONS + "\n" + self.session.catalog_prompt()

    def tools(self) -> tuple[AgentTool, ...]:
        return self.session.agent_tools() if self.session.available else ()

    def finalize(self, output_text: str) -> AgentCapabilityResult:
        del output_text
        return AgentCapabilityResult(
            name=self.name,
            metadata={},
        )
