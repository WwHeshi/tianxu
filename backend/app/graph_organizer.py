"""Extract and merge source-backed MingLi rules for the Neo4j graph."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .agent_capabilities import (
    AgentCapabilityOutputError,
    AgentCapabilityResult,
)
from .agent_tools import AgentTool
from .agent_trace import snapshot_agent_trace
from .config import graph_organizer_section_timeout_seconds
from .graph_store import (
    GraphApplyResult,
    GraphConditionGroup,
    GraphRuleMutation,
    GraphRuleSummary,
    GraphSourceSection,
    GraphStore,
    normalize_graph_key,
    stable_graph_node_id,
)
from .rule_graph_capability import RuleGraphReadCapability
from .tool_calling_agent import ToolCallingRunError, run_tool_calling_agent

GRAPH_ORGANIZER_PROMPT_VERSION = "graph-organizer-v13"
SECTION_TARGET_CHARACTERS = 2_000
SECTION_MAX_CHARACTERS = 2_500

GRAPH_ORGANIZER_INSTRUCTIONS = (
    "你是命理知识图谱整理 Agent。只处理本次提供的 TXT 原文段落，把原文明示、脱离上下文仍可"
    "理解并能用于后续命理分析的知识命题写入图谱。\n\n"
    "遵守以下约定：\n"
    "1. 可提取基础定义、属性映射、对应或比较关系、生克顺序、通用原理和条件判断；知识不要求"
    "具有‘当……则……’句式，也不要求包含吉凶结论。目录、页眉、书目信息、作者经历、纯叙事、"
    "孤立命例和信息不足的残句不提取，不使用段外知识补全。\n"
    "2. 一条规则表达一个可独立理解的知识命题。name 简短明确，summary 忠实概括，不添加原文"
    "没有表达的因果、程度或必然性。无需提交原文引文，后端会自动记录当前原文片段范围。\n"
    "3. concepts 填主要概念，outcomes 填原文直接陈述的事实或结论。没有前提时 condition_groups"
    " 必须直接传空数组，不得创建 all_of 和 none_of 都为空的条件组。条件判断的组间为任一组"
    "成立，all_of 为同时成立，none_of 为不得出现。strengthened_by、weakened_by 和"
    " does_not_prove 只填写原文明示的内容；其余填空数组。\n"
    "4. rules 非空时，提交前用 search_rule_graph 批量查询每个候选名称。适用条件和结论实质相同"
    "才用 existing_rule_id 合并；名称相似或部分重合不能合并，无法确认时留空。\n"
    "5. rule_links 只关联搜索到且关系明确的规则：REFINES 表示本规则更具体，EXCEPTION_TO 表示"
    "本规则是其例外，CONTRADICTS 表示两者确有冲突；语义相同应合并，不建立关系。\n"
    "6. 调用 submit_rule_graph 提交规则；没有可提取知识时提交空 rules。工具返回 error 时按其中"
    "位置修正后再次提交；工具返回写入结果后，检查是否还有遗漏，必要时继续搜索或提交。"
    "确认本段全部完成后，停止调用工具并简短回复完成。"
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


class ExtractedRuleLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    relation: Literal["REFINES", "EXCEPTION_TO", "CONTRADICTS"]


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
    existing_rule_id: str = Field(max_length=100)
    rule_links: list[ExtractedRuleLink] = Field(max_length=30)

    @field_validator("condition_groups", mode="before")
    @classmethod
    def discard_empty_condition_groups(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [
            group
            for group in value
            if not (
                isinstance(group, dict)
                and not group.get("all_of")
                and not group.get("none_of")
            )
        ]


class GraphExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[ExtractedGraphRule] = Field(max_length=100)


class SubmitRuleGraphResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: int
    merged: int


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




class GraphOrganizerCapability:
    """Live graph search plus atomic, source-validated section submission."""

    def __init__(self, context: GraphOrganizerContext) -> None:
        self._context = context
        self._reader = RuleGraphReadCapability(context.store)
        self._submitted: GraphExtractionOutput | None = None
        self._apply_result: GraphApplyResult | None = None

    @property
    def name(self) -> str:
        return "graph_organizer"

    def prompt_section(self) -> str:
        return (
            "search_rule_graph 用关键词查询当前真实规则图谱；需要自定义路径、聚合或多层读取时"
            "使用 query_rule_graph。submit_rule_graph 会在校验后立即写入当前段落的规则并返回"
            "结果；同一 Session 后续查询和提交可以看到刚写入的规则，停止调用工具后本段结束。"
        )

    def tools(self) -> tuple[AgentTool, ...]:
        return (
            *self._reader.tools(),
            AgentTool(
                name="submit_rule_graph",
                description=(
                    "校验并立即写入从当前原文段提取出的全部规则。完成必要的现有规则查询后"
                    "调用；没有可提取规则时将 rules 传为空数组。成功结果会返回当前 Session，"
                    "确认没有遗漏后停止调用工具。"
                ),
                input_schema=GraphExtractionOutput.model_json_schema(),
                input_model=GraphExtractionOutput,
                execute=self._submit,
                return_input_errors=True,
            ),
        )

    async def _submit(self, tool_input: BaseModel) -> SubmitRuleGraphResult:
        extraction = GraphExtractionOutput.model_validate(tool_input)
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
            created=apply_result.rules_created,
            merged=apply_result.rules_merged,
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
            timeout_seconds=graph_organizer_section_timeout_seconds(),
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
    refines_ids: list[str]
    exception_to_ids: list[str]
    conflicts_with_ids: list[str]
    source_sections: list[GraphSourceSection]


def merge_graph_extractions(
    extracted_sections: tuple[tuple[DocumentSection, GraphExtractionOutput], ...],
    existing_rules: tuple[GraphRuleSummary, ...],
) -> tuple[GraphRuleMutation, ...]:
    """Combine section outputs and attach their server-known source ranges."""

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
            if not name or not summary:
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
                    refines_ids=[],
                    exception_to_ids=[],
                    conflicts_with_ids=[],
                    source_sections=[],
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
            link_targets = {
                "REFINES": current.refines_ids,
                "EXCEPTION_TO": current.exception_to_ids,
                "CONTRADICTS": current.conflicts_with_ids,
            }
            for link in candidate.rule_links:
                if link.id in existing_by_id and link.id != rule_id:
                    link_targets[link.relation].append(link.id)
            source_section = GraphSourceSection(start=section.start, end=section.end)
            if source_section not in current.source_sections:
                current.source_sections.append(source_section)

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
            equivalent_to_ids=(),
            refines_ids=tuple(dict.fromkeys(rule.refines_ids)),
            exception_to_ids=tuple(dict.fromkeys(rule.exception_to_ids)),
            conflicts_with_ids=tuple(dict.fromkeys(rule.conflicts_with_ids)),
            source_sections=tuple(rule.source_sections),
        )
        for rule in sorted(merged.values(), key=lambda item: (item.name, item.id))
    )
