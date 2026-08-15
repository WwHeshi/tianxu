"""Extract and merge source-backed MingLi rules for the Neo4j graph."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import log2
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from .agent_capabilities import (
    AgentCapabilityOutputError,
    AgentCapabilityResult,
)
from .agent_tools import AgentTool, AgentToolInputError
from .agent_trace import snapshot_agent_trace
from .graph_store import (
    GraphApplyResult,
    GraphConditionGroup,
    GraphRuleMutation,
    GraphRuleNeighborhood,
    GraphRuleSummary,
    GraphSourceExcerpt,
    GraphStore,
    normalize_graph_key,
    stable_graph_node_id,
)
from .tool_calling_agent import ToolCallingRunError, run_tool_calling_agent

GRAPH_ORGANIZER_PROMPT_VERSION = "graph-organizer-v4"
SECTION_TARGET_CHARACTERS = 2_000
SECTION_MAX_CHARACTERS = 2_500

GRAPH_ORGANIZER_INSTRUCTIONS = (
    "你是命理规则图谱的整理 Agent。你只处理本次提供的 TXT 原文段落，目标是把原文中"
    "明确表达、可跨命例复用的判断规则准确映射到现有图谱。\n\n"
    "遵守以下约定：\n"
    "1. 只提取原文明示且可以独立复用的规则。单个命例、故事、目录、页眉、书目信息以及"
    "信息不足的残句不能自行提升为通用规则，也不得用段外知识补全。\n"
    "2. 一条规则表达一个完整判断。不要把同一判断的必要条件和结论拆成互不完整的多条规则，"
    "也不要把原文中彼此独立的判断强行合成一条。\n"
    "3. 每条规则都必须提供足以支持该规则的 source_excerpt。它必须是本段原文中的一段连续"
    "文字并逐字保留，不得改写、拼接或使用仅在附近但不能支持规则的文字。\n"
    "4. name 使用简短、稳定、可独立理解的规则名称；summary 准确概括适用条件和结论，不添加"
    "原文没有表达的因果、程度、吉凶或必然性。\n"
    "5. condition_groups 保留原文的条件逻辑：外层各组之间是 ANY；每组 all_of 中的条件必须"
    "同时成立（ALL），none_of 中任一条件都不得出现（NOT）。无前提规则返回空数组，不得把"
    "原文的‘或’放进同一个 all_of，也不得遗漏原文明示的限制条件。\n"
    "6. 字段各司其职：concepts 填写规则涉及的核心概念；strengthened_by 和 weakened_by 只填写"
    "原文明示会增强或削弱规则效力的因素；outcomes 只填写规则直接结论；does_not_prove 只填写"
    "原文明示不能由该规则推出的结论。没有对应内容时使用空数组。\n"
    "7. 只要 rules 非空，提交前必须用 search_rule_graph 查询每个候选规则的 name。一次最多"
    "查询 30 个名称，应尽量合并到最少批次；每个候选得到明确结果后，不要再换同义词重复查询。\n"
    "8. 只有既有规则与候选规则的适用条件和结论实质相同，才填写 existing_rule_id；仅名称相似、"
    "概念相关或结论部分重合时不得合并。无法确认时使用空字符串，让它成为新规则。\n"
    "9. equivalent_to_ids、refines_ids、exception_to_ids 和 conflicts_with_ids 只填写通过工具"
    "找到且逻辑关系明确的既有规则编号。更具体不等于冲突，结论不同也不自动构成冲突；不确定时"
    "返回空数组。\n"
    "10. 不要删除、覆盖或纠正既有规则。原文没有可提取规则时，也必须调用 submit_rule_graph，"
    "并把 rules 传为空数组。\n"
    "11. 完成必要查询后，必须且只能单独调用一次 submit_rule_graph 提交本段全部规则。只有该"
    "工具写入成功才表示本段完成；成功后不需要再生成最终回答。"
)


@dataclass(frozen=True)
class DocumentSection:
    index: int
    start: int
    end: int
    text: str


class ExtractedConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_of: list[str] = Field(max_length=30)
    none_of: list[str] = Field(max_length=30)

    @model_validator(mode="after")
    def validate_group(self) -> "ExtractedConditionGroup":
        if not self.all_of and not self.none_of:
            raise ValueError("条件组不能同时缺少 all_of 和 none_of")
        positive = {normalize_graph_key(value) for value in self.all_of}
        negative = {normalize_graph_key(value) for value in self.none_of}
        if "" in positive or "" in negative or positive & negative:
            raise ValueError("条件组包含空条件或相互矛盾的条件")
        return self


class ExtractedGraphRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    aliases: list[str] = Field(max_length=30)
    concepts: list[str] = Field(max_length=40)
    condition_groups: list[ExtractedConditionGroup] = Field(max_length=20)
    strengthened_by: list[str] = Field(max_length=40)
    weakened_by: list[str] = Field(max_length=40)
    outcomes: list[str] = Field(max_length=40)
    does_not_prove: list[str] = Field(max_length=40)
    source_excerpt: str = Field(min_length=1, max_length=4000)
    existing_rule_id: str = Field(max_length=100)
    equivalent_to_ids: list[str] = Field(max_length=30)
    refines_ids: list[str] = Field(max_length=30)
    exception_to_ids: list[str] = Field(max_length=30)
    conflicts_with_ids: list[str] = Field(max_length=30)


class GraphExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[ExtractedGraphRule] = Field(max_length=100)


class SubmitRuleGraphResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    rule_count: int
    rules_created: int
    rules_merged: int
    conditions_written: int
    relations_written: int
    conflicts_written: int


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


@dataclass(frozen=True)
class GraphSectionResult:
    extraction: GraphExtractionOutput
    apply_result: GraphApplyResult
    input_tokens: int
    output_tokens: int
    agent_trace: dict[str, Any] | None = None


@dataclass(frozen=True)
class GraphOrganizerContext:
    store: GraphStore
    job_id: str
    document_id: str
    document_title: str
    document_sha256: str
    section: DocumentSection


class GraphOrganizerModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        retryable: bool = False,
        fatal: bool = False,
        agent_trace: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.retryable = retryable
        self.fatal = fatal
        self.agent_trace = agent_trace


def split_document_sections(
    text: str,
    *,
    target: int = SECTION_TARGET_CHARACTERS,
    maximum: int = SECTION_MAX_CHARACTERS,
) -> tuple[DocumentSection, ...]:
    """Split for model reading while preserving every original character exactly once."""

    if target < 1 or maximum < target:
        raise ValueError("section limits are invalid")
    if not text:
        return ()

    sections: list[DocumentSection] = []
    start = 0
    while start < len(text):
        remaining = len(text) - start
        if remaining <= maximum:
            end = len(text)
        else:
            target_end = start + target
            maximum_end = start + maximum
            end = _preferred_break(text, target_end, maximum_end)
            if end <= start:
                end = maximum_end
        sections.append(
            DocumentSection(
                index=len(sections),
                start=start,
                end=end,
                text=text[start:end],
            )
        )
        start = end
    return tuple(sections)


def _preferred_break(text: str, target_end: int, maximum_end: int) -> int:
    window = text[target_end:maximum_end]
    for pattern in (r"(?:\r?\n){2,}", r"\r?\n", r"[。！？；]\s*"):
        match = re.search(pattern, window)
        if match is not None:
            return target_end + match.end()

    earlier_start = max(0, target_end - 2_000)
    earlier = text[earlier_start:target_end]
    for pattern in (r"(?:\r?\n){2,}", r"\r?\n", r"[。！？；]\s*"):
        matches = list(re.finditer(pattern, earlier))
        if matches:
            return earlier_start + matches[-1].end()
    return maximum_end


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


class GraphOrganizerCapability:
    """Live graph search plus atomic, source-validated section submission."""

    def __init__(self, context: GraphOrganizerContext) -> None:
        self._context = context
        self._submitted: GraphExtractionOutput | None = None
        self._apply_result: GraphApplyResult | None = None

    @property
    def name(self) -> str:
        return "graph_organizer"

    def prompt_section(self) -> str:
        return (
            "search_rule_graph 每次调用都查询当前真实规则图谱。submit_rule_graph 会在校验后"
            "立即写入当前段落的规则；写入成功后本段结束，后续段落可以查询到本段规则。"
        )

    def tools(self) -> tuple[AgentTool, ...]:
        return (
            AgentTool(
                name="search_rule_graph",
                description=(
                    "实时批量查询 Neo4j。规则名称、别名、摘要及其关联的概念、条件和结论都会"
                    "参与匹配；每个查询返回最相关的五条规则及其完整局部图谱，用于判断合并"
                    "与规则关系。"
                ),
                input_schema=SearchRuleGraphInput.model_json_schema(),
                input_model=SearchRuleGraphInput,
                execute=self._search,
            ),
            AgentTool(
                name="submit_rule_graph",
                description=(
                    "校验并立即写入从当前原文段提取出的全部规则。完成必要的现有规则查询后"
                    "调用一次；没有可提取规则时将 rules 传为空数组。写入成功即结束本段。"
                ),
                input_schema=GraphExtractionOutput.model_json_schema(),
                input_model=GraphExtractionOutput,
                execute=self._submit,
                terminal=True,
            ),
        )

    async def _search(self, tool_input: BaseModel) -> SearchRuleGraphOutput:
        queries = SearchRuleGraphInput.model_validate(tool_input).queries
        rules = await self._context.store.list_rule_summaries()
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
            for neighborhood in await self._context.store.get_rule_neighborhoods(
                matched_rule_ids
            )
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

    async def _submit(self, tool_input: BaseModel) -> SubmitRuleGraphResult:
        extraction = GraphExtractionOutput.model_validate(tool_input)
        for rule in extraction.rules:
            excerpt = rule.source_excerpt
            stripped_excerpt = excerpt.strip()
            if (
                excerpt not in self._context.section.text
                and stripped_excerpt not in self._context.section.text
            ):
                raise AgentToolInputError(
                    "submit_rule_graph 的 source_excerpt 必须逐字来自当前原文段。"
                )

        existing_rules = await self._context.store.list_rule_summaries()
        mutations = merge_graph_extractions(
            ((self._context.section, extraction),),
            existing_rules,
        )
        apply_result = await self._context.store.apply_rules(
            job_id=self._context.job_id,
            document_id=self._context.document_id,
            document_title=self._context.document_title,
            document_sha256=self._context.document_sha256,
            rules=mutations,
        )
        self._submitted = extraction
        self._apply_result = apply_result
        return SubmitRuleGraphResult(
            accepted=True,
            rule_count=len(extraction.rules),
            rules_created=apply_result.rules_created,
            rules_merged=apply_result.rules_merged,
            conditions_written=apply_result.conditions_written,
            relations_written=apply_result.relations_written,
            conflicts_written=apply_result.conflicts_written,
        )

    def finalize(self, output_text: str) -> AgentCapabilityResult:
        del output_text
        if self._submitted is None or self._apply_result is None:
            raise AgentCapabilityOutputError("模型没有通过 submit_rule_graph 工具提交规则。")
        return AgentCapabilityResult(
            name=self.name,
            metadata={
                "rule_count": len(self._submitted.rules),
                "rules_created": self._apply_result.rules_created,
                "rules_merged": self._apply_result.rules_merged,
            },
            artifacts=(self._submitted, self._apply_result),
        )


async def extract_graph_section(
    *,
    context: GraphOrganizerContext,
    api_protocol: str,
    model: str,
    base_url: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> GraphSectionResult:
    capability = GraphOrganizerCapability(context)
    system_prompt = GRAPH_ORGANIZER_INSTRUCTIONS
    user_prompt = (
        f"资料名称：{context.document_title}\n"
        f"本段字符范围：{context.section.start}-{context.section.end}\n\n"
        "以下是本次需要整理的原文：\n<document>\n"
        f"{context.section.text}\n"
        "</document>"
    )
    try:
        execution = await run_tool_calling_agent(
            api_protocol=api_protocol,  # type: ignore[arg-type]
            model=model,
            base_url=base_url,
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema_name=None,
            output_schema=None,
            client=client,
            capabilities=(capability,),
        )
    except ToolCallingRunError as exc:
        raise GraphOrganizerModelError(
            str(exc),
            input_tokens=exc.input_tokens,
            output_tokens=exc.output_tokens,
            retryable=exc.retryable or not exc.provider_error,
            fatal=exc.fatal,
            agent_trace=snapshot_agent_trace(
                body=exc.request_body,
                model_calls=exc.model_calls,
                tool_executions=exc.tool_executions,
            ),
        ) from exc

    capability_result = next(
        result for result in execution.capability_results if result.name == capability.name
    )
    agent_trace = snapshot_agent_trace(
        body=execution.request_body,
        model_calls=execution.model_calls,
        tool_executions=execution.tool_executions,
    )
    extraction = capability_result.artifacts[0]
    apply_result = capability_result.artifacts[1]
    if not isinstance(extraction, GraphExtractionOutput) or not isinstance(
        apply_result, GraphApplyResult
    ):
        raise GraphOrganizerModelError(
            "整理 Agent 没有返回有效规则写入结果。",
            retryable=True,
            agent_trace=agent_trace,
        )
    return GraphSectionResult(
        extraction=extraction,
        apply_result=apply_result,
        input_tokens=execution.input_tokens,
        output_tokens=execution.output_tokens,
        agent_trace=agent_trace,
    )


def _clean_text(value: str, *, maximum: int = 2000) -> str:
    return re.sub(r"\s+", " ", value).strip()[:maximum]


def _unique_texts(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    keys: set[str] = set()
    for raw_value in values:
        value = _clean_text(raw_value, maximum=500)
        key = normalize_graph_key(value)
        if not value or not key or key in keys:
            continue
        keys.add(key)
        unique.append(value)
    return tuple(unique)


def _condition_group(value: ExtractedConditionGroup) -> GraphConditionGroup | None:
    all_of = _unique_texts(value.all_of)
    none_of = _unique_texts(value.none_of)
    negative_keys = {normalize_graph_key(item) for item in none_of}
    all_of = tuple(item for item in all_of if normalize_graph_key(item) not in negative_keys)
    if not all_of and not none_of:
        return None
    return GraphConditionGroup(all_of=all_of, none_of=none_of)


def _unique_condition_groups(
    values: list[GraphConditionGroup],
) -> tuple[GraphConditionGroup, ...]:
    unique: list[GraphConditionGroup] = []
    keys: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for value in values:
        key = (
            tuple(sorted(normalize_graph_key(item) for item in value.all_of)),
            tuple(sorted(normalize_graph_key(item) for item in value.none_of)),
        )
        if key not in keys:
            keys.add(key)
            unique.append(value)
    return tuple(unique)


@dataclass
class _MutableRule:
    id: str
    name: str
    summary: str
    aliases: list[str]
    concepts: list[str]
    condition_groups: list[GraphConditionGroup]
    strengthened_by: list[str]
    weakened_by: list[str]
    outcomes: list[str]
    does_not_prove: list[str]
    equivalent_to_ids: list[str]
    refines_ids: list[str]
    exception_to_ids: list[str]
    conflicts_with_ids: list[str]
    excerpts: list[GraphSourceExcerpt]


def merge_graph_extractions(
    extracted_sections: tuple[tuple[DocumentSection, GraphExtractionOutput], ...],
    existing_rules: tuple[GraphRuleSummary, ...],
) -> tuple[GraphRuleMutation, ...]:
    """Validate excerpts and combine all section outputs into one safe change set."""

    existing_by_id = {rule.id: rule for rule in existing_rules}
    existing_by_key: dict[str, str] = {}
    for rule in existing_rules:
        for value in (rule.name, *rule.aliases):
            key = normalize_graph_key(value)
            if key:
                existing_by_key.setdefault(key, rule.id)

    merged: dict[str, _MutableRule] = {}
    for section, extraction in extracted_sections:
        for candidate in extraction.rules:
            name = _clean_text(candidate.name, maximum=200)
            summary = _clean_text(candidate.summary)
            excerpt_text = candidate.source_excerpt
            excerpt_index = section.text.find(excerpt_text)
            if excerpt_index < 0:
                excerpt_text = excerpt_text.strip()
                excerpt_index = section.text.find(excerpt_text)
            if not name or not summary or not excerpt_text or excerpt_index < 0:
                continue

            rule_id = candidate.existing_rule_id.strip()
            if rule_id not in existing_by_id:
                rule_id = ""
            if not rule_id:
                candidate_keys = (
                    normalize_graph_key(name),
                    *(normalize_graph_key(value) for value in candidate.aliases),
                )
                rule_id = next(
                    (
                        existing_by_key[key]
                        for key in candidate_keys
                        if key and key in existing_by_key
                    ),
                    stable_graph_node_id("R", name),
                )

            current = merged.get(rule_id)
            if current is None:
                existing = existing_by_id.get(rule_id)
                canonical_name = existing.name if existing is not None else name
                canonical_summary = existing.summary if existing is not None else summary
                current = _MutableRule(
                    id=rule_id,
                    name=canonical_name,
                    summary=canonical_summary or summary,
                    aliases=list(existing.aliases if existing is not None else ()),
                    concepts=[],
                    condition_groups=[],
                    strengthened_by=[],
                    weakened_by=[],
                    outcomes=[],
                    does_not_prove=[],
                    equivalent_to_ids=[],
                    refines_ids=[],
                    exception_to_ids=[],
                    conflicts_with_ids=[],
                    excerpts=[],
                )
                merged[rule_id] = current
            elif len(summary) > len(current.summary) and rule_id not in existing_by_id:
                current.summary = summary

            if normalize_graph_key(name) != normalize_graph_key(current.name):
                current.aliases.append(name)
            current.aliases.extend(candidate.aliases)
            current.concepts.extend(candidate.concepts)
            current.condition_groups.extend(
                group
                for value in candidate.condition_groups
                if (group := _condition_group(value)) is not None
            )
            current.strengthened_by.extend(candidate.strengthened_by)
            current.weakened_by.extend(candidate.weakened_by)
            current.outcomes.extend(candidate.outcomes)
            current.does_not_prove.extend(candidate.does_not_prove)
            for target, values in (
                (current.equivalent_to_ids, candidate.equivalent_to_ids),
                (current.refines_ids, candidate.refines_ids),
                (current.exception_to_ids, candidate.exception_to_ids),
                (current.conflicts_with_ids, candidate.conflicts_with_ids),
            ):
                target.extend(
                    other_id
                    for other_id in values
                    if other_id in existing_by_id and other_id != rule_id
                )
            excerpt = GraphSourceExcerpt(
                text=excerpt_text,
                start=section.start + excerpt_index,
                end=section.start + excerpt_index + len(excerpt_text),
            )
            if excerpt not in current.excerpts:
                current.excerpts.append(excerpt)

    return tuple(
        GraphRuleMutation(
            id=rule.id,
            name=rule.name,
            summary=rule.summary,
            aliases=_unique_texts(rule.aliases),
            concepts=_unique_texts(rule.concepts),
            condition_groups=_unique_condition_groups(rule.condition_groups),
            strengthened_by=_unique_texts(rule.strengthened_by),
            weakened_by=_unique_texts(rule.weakened_by),
            outcomes=_unique_texts(rule.outcomes),
            does_not_prove=_unique_texts(rule.does_not_prove),
            equivalent_to_ids=tuple(dict.fromkeys(rule.equivalent_to_ids)),
            refines_ids=tuple(dict.fromkeys(rule.refines_ids)),
            exception_to_ids=tuple(dict.fromkeys(rule.exception_to_ids)),
            conflicts_with_ids=tuple(dict.fromkeys(rule.conflicts_with_ids)),
            excerpts=tuple(rule.excerpts),
        )
        for rule in sorted(merged.values(), key=lambda item: (item.name, item.id))
    )
