import pytest

from app.agent_capabilities import AgentCapabilityRegistry
from app.graph_store import GraphRuleNeighborhood, GraphRuleSummary
from app.rule_graph_capability import (
    RULE_GRAPH_INSTRUCTIONS,
    RULE_GRAPH_QUERY_TOOL_DESCRIPTION,
    RuleGraphReadCapability,
)


class FakeReadGraphStore:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def list_rule_summaries(self) -> tuple[GraphRuleSummary, ...]:
        return ()

    async def get_rule_neighborhoods(
        self,
        rule_ids: tuple[str, ...],
    ) -> tuple[GraphRuleNeighborhood, ...]:
        assert rule_ids == ()
        return ()

    async def execute_read_query(self, cypher: str) -> tuple[dict, ...]:
        self.queries.append(cypher)
        return ({"rule": "财格败条件", "condition": "财透七煞"},)


@pytest.mark.asyncio
async def test_rule_graph_capability_registers_only_live_read_tools() -> None:
    store = FakeReadGraphStore()
    capability = RuleGraphReadCapability(store)  # type: ignore[arg-type]
    registry = AgentCapabilityRegistry((capability,))
    tools = {tool.name: tool for tool in registry.tools()}

    assert capability.name == "rule_graph_read"
    assert list(tools) == ["search_rule_graph", "query_rule_graph"]
    assert "submit_rule_graph" not in tools
    assert registry.apply_prompt("基础提示词") == (
        "基础提示词\n\n" + RULE_GRAPH_INSTRUCTIONS
    )
    assert "优先使用 search_rule_graph" in capability.prompt_section()
    assert "search_knowledge 定位原文" in capability.prompt_section()
    assert "read_knowledge 阅读" in capability.prompt_section()
    assert tools["query_rule_graph"].description == RULE_GRAPH_QUERY_TOOL_DESCRIPTION
    assert "Rule-[:HAS_CONDITION_GROUP]->ConditionGroup" in (
        tools["query_rule_graph"].description
    )

    search_result = await tools["search_rule_graph"].execute(
        tools["search_rule_graph"].input_model(queries=["财透七煞"])
    )
    assert search_result.root[0].query == "财透七煞"
    assert search_result.root[0].rules == []

    query_result = await tools["query_rule_graph"].execute(
        tools["query_rule_graph"].input_model(
            cypher="MATCH (rule:Rule) RETURN rule.name AS rule LIMIT 1"
        )
    )
    assert query_result.root == [
        {"rule": "财格败条件", "condition": "财透七煞"}
    ]
    assert store.queries == [
        "MATCH (rule:Rule) RETURN rule.name AS rule LIMIT 1"
    ]
    assert capability.finalize("最终回答").metadata == {}
