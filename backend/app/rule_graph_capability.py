"""Reusable read-only rule graph capability for any Agent run."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import log2
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from .agent_capabilities import AgentCapabilityResult
from .agent_tools import AgentTool, AgentToolInputError
from .graph_store import (
    GraphReadQueryError,
    GraphRuleNeighborhood,
    GraphRuleSummary,
    GraphStore,
    normalize_graph_key,
)

RULE_GRAPH_CAPABILITY_NAME = "rule_graph_read"

RULE_GRAPH_INSTRUCTIONS = (
    "规则图谱能力已启用。优先使用 search_rule_graph 查找相关规则；需要自定义路径、多层关系或"
    "聚合时使用 query_rule_graph。图谱用于读取结构化规则；需要核对书籍原文时继续使用知识库"
    "工具。"
)

RULE_GRAPH_QUERY_TOOL_DESCRIPTION = (
    "执行一条只读 Cypher，自由查询当前真实规则图谱，可使用多跳、反向、路径、过滤、排序和"
    "聚合。节点及常用属性：Rule(id,name,summary,aliases)、ConditionGroup(id,name,logic)、"
    "Condition(id,name)、Concept(id,name)、Outcome(id,name)、Source(id,document_id,title,sha256)。"
    "关系方向：Rule-[:HAS_CONDITION_GROUP]->ConditionGroup，ConditionGroup-[:REQUIRES|EXCLUDES]"
    "->Condition，Rule-[:RELATES_TO]->Concept，Rule-[:PRODUCES|DOES_NOT_PROVE]->Outcome，"
    "Condition-[:STRENGTHENS|WEAKENS]->Rule，Rule-[:SOURCED_FROM]->Source，Rule 之间可用"
    " EQUIVALENT_TO、REFINES、EXCEPTION_TO、CONTRADICTS。只允许读取；数据库过程、外部文件"
    "和修改语句会被拒绝。结果超过 100 行或内容过大时应使用 WHERE、SKIP、LIMIT 或减少字段。"
)


class SearchRuleGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(min_length=1, max_length=30)

    @field_validator("queries")
    @classmethod
    def normalize_queries(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = raw_value.strip()
            key = normalize_graph_key(value)
            if not value or len(value) > 200 or not key:
                raise ValueError("每个查询必须是 1 至 200 个字符的有效名称")
            if key not in seen:
                seen.add(key)
                normalized.append(value)
        return normalized


class SearchRuleGraphMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    match: Literal["exact", "partial"]


class SearchRuleGraphItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    summary: str
    aliases: list[str]
    score: int
    matched_on: list[SearchRuleGraphMatch]
    condition_groups: list["SearchRuleGraphConditionGroup"]
    strengthened_by: list[str]
    weakened_by: list[str]
    concepts: list[str]
    outcomes: list[str]
    does_not_prove: list[str]
    sources: list[str]
    related_rules: list["SearchRuleGraphRelatedRule"]


class SearchRuleGraphConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_of: list[str]
    none_of: list[str]


class SearchRuleGraphRelatedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    summary: str
    relation: Literal["EQUIVALENT_TO", "REFINES", "EXCEPTION_TO", "CONTRADICTS"]
    direction: Literal["outgoing", "incoming"]


class SearchRuleGraphResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    rules: list[SearchRuleGraphItem]


class SearchRuleGraphOutput(RootModel[list[SearchRuleGraphResult]]):
    pass


class QueryRuleGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cypher: str = Field(
        min_length=1,
        max_length=10_000,
        description="要执行的只读 Cypher 查询语句。",
    )

    @field_validator("cypher")
    @classmethod
    def normalize_cypher(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("cypher 不能为空")
        return normalized


class QueryRuleGraphOutput(RootModel[list[dict[str, Any]]]):
    pass


@dataclass(frozen=True)
class _SearchEvidence:
    kind: str
    name: str
    match: Literal["exact", "partial"]
    base_score: int


@dataclass(frozen=True)
class _ScoredRule:
    rule: GraphRuleSummary
    score: int
    matched_on: tuple[_SearchEvidence, ...]


def _query_terms(query: str) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in re.split(r"[\s,，、;；/|：:。！？]+", query):
        key = normalize_graph_key(value)
        if key and key not in seen:
            seen.add(key)
            terms.append(key)
    if not terms:
        key = normalize_graph_key(query)
        if key:
            terms.append(key)
    return tuple(terms)


def _semantic_frequencies(
    rules: tuple[GraphRuleSummary, ...],
) -> dict[tuple[str, str], int]:
    frequencies: dict[tuple[str, str], int] = {}
    for rule in rules:
        for kind, values in (
            ("Concept", rule.concepts),
            ("Condition", rule.conditions),
            ("Outcome", rule.outcomes),
        ):
            keys = {normalize_graph_key(value) for value in values}
            for key in keys - {""}:
                frequency_key = (kind, key)
                frequencies[frequency_key] = frequencies.get(frequency_key, 0) + 1
    return frequencies


def _search_evidence(
    query: str,
    rule: GraphRuleSummary,
) -> tuple[_SearchEvidence, ...]:
    query_key = normalize_graph_key(query)
    terms = _query_terms(query)
    evidence: dict[tuple[str, str], _SearchEvidence] = {}

    def inspect_values(
        kind: str,
        values: tuple[str, ...],
        *,
        exact_score: int,
        partial_score: int,
    ) -> None:
        for value in values:
            value_key = normalize_graph_key(value)
            if not value_key:
                continue
            if query_key == value_key or value_key in terms:
                match: Literal["exact", "partial"] = "exact"
                base_score = exact_score
            elif (
                query_key in value_key
                or value_key in query_key
                or any(term in value_key or value_key in term for term in terms)
            ):
                match = "partial"
                base_score = partial_score
            else:
                continue
            evidence_key = (kind, value_key)
            current = evidence.get(evidence_key)
            candidate = _SearchEvidence(kind, value, match, base_score)
            if current is None or candidate.base_score > current.base_score:
                evidence[evidence_key] = candidate

    inspect_values("Rule", (rule.name,), exact_score=100, partial_score=50)
    inspect_values("Alias", rule.aliases, exact_score=95, partial_score=50)
    inspect_values("Condition", rule.conditions, exact_score=85, partial_score=45)
    inspect_values("Outcome", rule.outcomes, exact_score=75, partial_score=40)
    inspect_values("Concept", rule.concepts, exact_score=65, partial_score=35)

    summary_key = normalize_graph_key(rule.summary)
    if summary_key:
        if query_key == summary_key:
            evidence[("Summary", summary_key)] = _SearchEvidence(
                "Summary", rule.summary, "exact", 55
            )
        elif query_key in summary_key:
            evidence[("Summary", summary_key)] = _SearchEvidence(
                "Summary", rule.summary, "partial", 55
            )
        elif any(term in summary_key or summary_key in term for term in terms):
            evidence[("Summary", summary_key)] = _SearchEvidence(
                "Summary", rule.summary, "partial", 25
            )

    return tuple(
        sorted(
            evidence.values(),
            key=lambda item: (-item.base_score, item.kind, item.name),
        )
    )


def _score_rule_search(
    query: str,
    rule: GraphRuleSummary,
    semantic_frequencies: dict[tuple[str, str], int],
) -> _ScoredRule:
    matched_on = _search_evidence(query, rule)
    if not matched_on:
        return _ScoredRule(rule=rule, score=0, matched_on=())

    terms = _query_terms(query)
    searchable_keys = tuple(
        normalize_graph_key(value)
        for value in (
            rule.name,
            *rule.aliases,
            rule.summary,
            *rule.concepts,
            *rule.conditions,
            *rule.outcomes,
        )
        if normalize_graph_key(value)
    )
    covered_terms = {
        term
        for term in terms
        if any(term in value or value in term for value in searchable_keys)
    }
    coverage_bonus = len(covered_terms) * 5
    if len(terms) > 1 and len(covered_terms) == len(terms):
        coverage_bonus += 20

    specificity_bonus = 0
    for match in matched_on:
        if match.match != "exact":
            continue
        if match.kind in {"Rule", "Alias"}:
            specificity_bonus = max(specificity_bonus, 20)
            continue
        frequency = semantic_frequencies.get(
            (match.kind, normalize_graph_key(match.name))
        )
        if frequency:
            specificity_bonus = max(
                specificity_bonus,
                round(20 / log2(frequency + 1)),
            )

    return _ScoredRule(
        rule=rule,
        score=max(item.base_score for item in matched_on)
        + coverage_bonus
        + specificity_bonus,
        matched_on=matched_on,
    )


def _project_search_rule(
    scored: _ScoredRule,
    neighborhood: GraphRuleNeighborhood,
) -> SearchRuleGraphItem:
    rule_id = scored.rule.id
    nodes = {node.id: node for node in neighborhood.nodes}
    relationships = neighborhood.relationships

    def related_names(
        relationship_kind: str,
        node_kind: str,
        *,
        direction: Literal["outgoing", "incoming"],
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for relationship in relationships:
            if relationship.kind != relationship_kind:
                continue
            node_id = (
                relationship.target_id
                if direction == "outgoing" and relationship.source_id == rule_id
                else relationship.source_id
                if direction == "incoming" and relationship.target_id == rule_id
                else None
            )
            node = nodes.get(node_id or "")
            if node is None or node.kind != node_kind:
                continue
            key = normalize_graph_key(node.name)
            if key and key not in seen:
                seen.add(key)
                names.append(node.name)
        return names

    group_ids = [
        relationship.target_id
        for relationship in relationships
        if relationship.kind == "HAS_CONDITION_GROUP"
        and relationship.source_id == rule_id
        and nodes.get(relationship.target_id) is not None
        and nodes[relationship.target_id].kind == "ConditionGroup"
    ]
    condition_groups: list[SearchRuleGraphConditionGroup] = []
    for group_id in dict.fromkeys(group_ids):
        all_of = [
            nodes[relationship.target_id].name
            for relationship in relationships
            if relationship.kind == "REQUIRES"
            and relationship.source_id == group_id
            and relationship.target_id in nodes
            and nodes[relationship.target_id].kind == "Condition"
        ]
        none_of = [
            nodes[relationship.target_id].name
            for relationship in relationships
            if relationship.kind == "EXCLUDES"
            and relationship.source_id == group_id
            and relationship.target_id in nodes
            and nodes[relationship.target_id].kind == "Condition"
        ]
        condition_groups.append(
            SearchRuleGraphConditionGroup(
                all_of=list(dict.fromkeys(all_of)),
                none_of=list(dict.fromkeys(none_of)),
            )
        )

    related_rules: list[SearchRuleGraphRelatedRule] = []
    rule_relationships = {"EQUIVALENT_TO", "REFINES", "EXCEPTION_TO", "CONTRADICTS"}
    for relationship in relationships:
        if relationship.kind not in rule_relationships:
            continue
        if relationship.source_id == rule_id:
            other_id = relationship.target_id
            direction: Literal["outgoing", "incoming"] = "outgoing"
        elif relationship.target_id == rule_id:
            other_id = relationship.source_id
            direction = "incoming"
        else:
            continue
        other = nodes.get(other_id)
        if other is None or other.kind != "Rule":
            continue
        related_rules.append(
            SearchRuleGraphRelatedRule(
                id=other.id,
                name=other.name,
                summary=other.summary,
                relation=relationship.kind,  # type: ignore[arg-type]
                direction=direction,
            )
        )

    return SearchRuleGraphItem(
        id=rule_id,
        name=scored.rule.name,
        summary=scored.rule.summary,
        aliases=list(scored.rule.aliases),
        score=scored.score,
        matched_on=[
            SearchRuleGraphMatch(
                kind=match.kind,
                name=match.name,
                match=match.match,
            )
            for match in scored.matched_on
        ],
        condition_groups=condition_groups,
        strengthened_by=related_names("STRENGTHENS", "Condition", direction="incoming"),
        weakened_by=related_names("WEAKENS", "Condition", direction="incoming"),
        concepts=related_names("RELATES_TO", "Concept", direction="outgoing"),
        outcomes=related_names("PRODUCES", "Outcome", direction="outgoing"),
        does_not_prove=related_names("DOES_NOT_PROVE", "Outcome", direction="outgoing"),
        sources=related_names("SOURCED_FROM", "Source", direction="outgoing"),
        related_rules=related_rules,
    )


class RuleGraphReadCapability:
    """Live keyword search and free read-only Cypher for one Agent run."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    @property
    def name(self) -> str:
        return RULE_GRAPH_CAPABILITY_NAME

    def prompt_section(self) -> str:
        return RULE_GRAPH_INSTRUCTIONS

    def tools(self) -> tuple[AgentTool, ...]:
        return (
            AgentTool(
                name="search_rule_graph",
                description=(
                    "实时批量查询 Neo4j。规则名称、别名、摘要及其关联的概念、条件和结论都会"
                    "参与匹配；每个查询返回最相关的五条规则及其完整局部图谱。"
                ),
                input_schema=SearchRuleGraphInput.model_json_schema(),
                input_model=SearchRuleGraphInput,
                execute=self._search,
            ),
            AgentTool(
                name="query_rule_graph",
                description=RULE_GRAPH_QUERY_TOOL_DESCRIPTION,
                input_schema=QueryRuleGraphInput.model_json_schema(),
                input_model=QueryRuleGraphInput,
                execute=self._query,
                return_input_errors=True,
            ),
        )

    async def _search(self, tool_input: BaseModel) -> SearchRuleGraphOutput:
        queries = SearchRuleGraphInput.model_validate(tool_input).queries
        rules = await self.store.list_rule_summaries()
        semantic_frequencies = _semantic_frequencies(rules)
        ranked_by_query = [
            (
                query,
                tuple(
                    scored_rule
                    for scored_rule in sorted(
                        (
                            _score_rule_search(query, rule, semantic_frequencies)
                            for rule in rules
                        ),
                        key=lambda item: (-item.score, item.rule.name, item.rule.id),
                    )[:5]
                    if scored_rule.score > 0
                ),
            )
            for query in queries
        ]
        matched_rule_ids = tuple(
            dict.fromkeys(
                scored.rule.id
                for _, matched_rules in ranked_by_query
                for scored in matched_rules
            )
        )
        neighborhoods = {
            neighborhood.rule_id: neighborhood
            for neighborhood in await self.store.get_rule_neighborhoods(matched_rule_ids)
        }
        return SearchRuleGraphOutput(
            root=[
                SearchRuleGraphResult(
                    query=query,
                    rules=[
                        _project_search_rule(
                            scored,
                            neighborhoods[scored.rule.id],
                        )
                        for scored in matched_rules
                    ],
                )
                for query, matched_rules in ranked_by_query
            ]
        )

    async def _query(self, tool_input: BaseModel) -> QueryRuleGraphOutput:
        cypher = QueryRuleGraphInput.model_validate(tool_input).cypher
        try:
            rows = await self.store.execute_read_query(cypher)
        except GraphReadQueryError as exc:
            raise AgentToolInputError(str(exc)) from exc
        return QueryRuleGraphOutput(root=list(rows))

    def finalize(self, output_text: str) -> AgentCapabilityResult:
        del output_text
        return AgentCapabilityResult(name=self.name, metadata={})
