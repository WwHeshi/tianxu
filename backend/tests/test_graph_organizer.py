import json

import httpx
import pytest

from app.agent_capabilities import AgentCapabilityOutputError
from app.agent_tools import AgentToolInputError
from app.graph_organizer import (
    GRAPH_ORGANIZER_INSTRUCTIONS,
    DocumentSection,
    ExtractedConditionGroup,
    ExtractedGraphRule,
    GraphExtractionOutput,
    GraphOrganizerCapability,
    GraphOrganizerContext,
    GraphOrganizerModelError,
    extract_graph_section,
    merge_graph_extractions,
    split_document_sections,
)
from app.graph_store import (
    GraphApplyResult,
    GraphNeighborhoodNode,
    GraphNeighborhoodRelationship,
    GraphRuleNeighborhood,
    GraphRuleSummary,
    stable_graph_node_id,
)


class FakeLiveGraphStore:
    def __init__(
        self,
        rules: tuple[GraphRuleSummary, ...] = (),
        neighborhoods: tuple[GraphRuleNeighborhood, ...] = (),
    ) -> None:
        self.rules = rules
        self.neighborhoods = {
            neighborhood.rule_id: neighborhood for neighborhood in neighborhoods
        }
        self.list_calls = 0
        self.neighborhood_calls: list[tuple[str, ...]] = []
        self.apply_calls: list[dict] = []

    async def list_rule_summaries(self) -> tuple[GraphRuleSummary, ...]:
        self.list_calls += 1
        return self.rules

    async def get_rule_neighborhoods(
        self,
        rule_ids: tuple[str, ...],
    ) -> tuple[GraphRuleNeighborhood, ...]:
        self.neighborhood_calls.append(rule_ids)
        return tuple(
            self.neighborhoods.get(
                rule_id,
                GraphRuleNeighborhood(rule_id=rule_id, nodes=(), relationships=()),
            )
            for rule_id in rule_ids
        )

    async def apply_rules(self, **kwargs) -> GraphApplyResult:
        self.apply_calls.append(kwargs)
        existing_ids = {rule.id for rule in self.rules}
        submitted_ids = {rule.id for rule in kwargs["rules"]}
        summaries = {rule.id: rule for rule in self.rules}
        for rule in kwargs["rules"]:
            summaries.setdefault(
                rule.id,
                GraphRuleSummary(
                    id=rule.id,
                    name=rule.name,
                    summary=rule.summary,
                    aliases=rule.aliases,
                    concepts=rule.concepts,
                    outcomes=rule.outcomes,
                ),
            )
        self.rules = tuple(summaries.values())
        return GraphApplyResult(
            rules_created=len(submitted_ids - existing_ids),
            rules_merged=len(submitted_ids & existing_ids),
            conditions_written=1 if submitted_ids else 0,
            relations_written=3 if submitted_ids else 0,
            conflicts_written=0,
        )


def organizer_context(
    store: FakeLiveGraphStore,
    section: DocumentSection | None = None,
) -> GraphOrganizerContext:
    return GraphOrganizerContext(
        store=store,  # type: ignore[arg-type]
        job_id="job-1",
        document_id="doc-1",
        document_title="测试资料",
        document_sha256="abc",
        section=section
        or DocumentSection(
            index=0,
            start=0,
            end=len("财星得地，财有根基。"),
            text="财星得地，财有根基。",
        ),
    )


def extracted_rule(**overrides) -> ExtractedGraphRule:
    values = {
        "name": "财星得地",
        "summary": "财星得地时财运较有根基。",
        "aliases": ["财星有根"],
        "concepts": ["财星"],
        "condition_groups": [
            {"all_of": ["财星得地", "日主有力"], "none_of": ["比劫夺财"]},
        ],
        "strengthened_by": ["得生扶"],
        "weakened_by": ["比劫夺财"],
        "outcomes": ["财运稳定"],
        "does_not_prove": ["必然暴富"],
        "source_excerpt": "财星得地，财有根基。",
        "existing_rule_id": "",
        "equivalent_to_ids": [],
        "refines_ids": [],
        "exception_to_ids": [],
        "conflicts_with_ids": [],
    }
    values.update(overrides)
    return ExtractedGraphRule.model_validate(values)


def test_sections_cover_original_text_without_persistent_chunk_changes() -> None:
    text = "甲" * 18 + "\n\n" + "乙" * 17 + "。" + "丙" * 23

    sections = split_document_sections(text, target=20, maximum=28)

    assert "".join(section.text for section in sections) == text
    assert sections[0].start == 0
    assert sections[-1].end == len(text)
    assert all(section.end - section.start <= 28 for section in sections)
    assert all(
        previous.end == current.start
        for previous, current in zip(sections, sections[1:], strict=False)
    )


def test_condition_group_rejects_the_same_positive_and_negative_condition() -> None:
    with pytest.raises(ValueError, match="相互矛盾"):
        ExtractedConditionGroup(all_of=["身旺"], none_of=["身旺"])


def test_merge_validates_excerpt_and_uses_existing_rule_alias() -> None:
    text = "前言。财星得地，财有根基。后文。"
    section = DocumentSection(index=0, start=100, end=100 + len(text), text=text)
    existing = GraphRuleSummary(
        id="R-existing",
        name="财星有根",
        summary="既有摘要",
        aliases=("财星得地",),
        concepts=("财星",),
        outcomes=("财运稳定",),
    )
    extraction = GraphExtractionOutput(
        rules=[
            extracted_rule(
                aliases=["财星有根", "财星有根"],
                concepts=["财星", "财星"],
                conflicts_with_ids=["R-existing", "R-missing"],
            ),
            extracted_rule(
                name="无原文规则",
                source_excerpt="原文中并不存在这句话",
            ),
        ]
    )

    mutations = merge_graph_extractions(((section, extraction),), (existing,))

    assert len(mutations) == 1
    mutation = mutations[0]
    assert mutation.id == "R-existing"
    assert mutation.name == "财星有根"
    assert mutation.summary == "既有摘要"
    assert mutation.concepts == ("财星",)
    assert mutation.condition_groups[0].all_of == ("财星得地", "日主有力")
    assert mutation.condition_groups[0].none_of == ("比劫夺财",)
    assert mutation.conflicts_with_ids == ()
    excerpt_start = 100 + text.index("财星得地，财有根基。")
    assert mutation.excerpts[0].start == excerpt_start
    assert mutation.excerpts[0].end == excerpt_start + len("财星得地，财有根基。")


def test_new_rule_uses_stable_id_deterministically() -> None:
    text = "财星得地，财有根基。"
    section = DocumentSection(index=0, start=0, end=len(text), text=text)
    extraction = GraphExtractionOutput(rules=[extracted_rule()])

    first = merge_graph_extractions(((section, extraction),), ())
    second = merge_graph_extractions(((section, extraction),), ())

    assert first[0].id == stable_graph_node_id("R", "财星得地")
    assert first == second


@pytest.mark.asyncio
async def test_graph_capability_searches_live_then_writes_through_submit_tool() -> None:
    store = FakeLiveGraphStore(
        rules=(
            GraphRuleSummary(
                id="R-wealth",
                name="财星有根",
                summary="财星得地时较稳定",
                aliases=("财星得地",),
                concepts=("财星",),
                outcomes=("财运稳定",),
                conditions=("财星得地", "比劫夺财", "得令", "受制"),
            ),
        ),
        neighborhoods=(
            GraphRuleNeighborhood(
                rule_id="R-wealth",
                nodes=(
                    GraphNeighborhoodNode(
                        id="G-wealth",
                        kind="ConditionGroup",
                        name="财星得地",
                        summary="",
                        aliases=(),
                    ),
                    GraphNeighborhoodNode(
                        id="C-wealth",
                        kind="Condition",
                        name="财星得地",
                        summary="",
                        aliases=(),
                    ),
                    GraphNeighborhoodNode("C-excluded", "Condition", "比劫夺财", "", ()),
                    GraphNeighborhoodNode("C-strong", "Condition", "得令", "", ()),
                    GraphNeighborhoodNode("C-weak", "Condition", "受制", "", ()),
                    GraphNeighborhoodNode("K-wealth", "Concept", "财星", "", ()),
                    GraphNeighborhoodNode("O-stable", "Outcome", "财运稳定", "", ()),
                    GraphNeighborhoodNode("O-rich", "Outcome", "必然暴富", "", ()),
                    GraphNeighborhoodNode("S-book", "Source", "测试命理书", "", ()),
                    GraphNeighborhoodNode(
                        "R-related",
                        "Rule",
                        "财星受制",
                        "财星受制时另论。",
                        (),
                    ),
                ),
                relationships=(
                    GraphNeighborhoodRelationship(
                        kind="HAS_CONDITION_GROUP",
                        source_id="R-wealth",
                        target_id="G-wealth",
                    ),
                    GraphNeighborhoodRelationship(
                        kind="REQUIRES",
                        source_id="G-wealth",
                        target_id="C-wealth",
                    ),
                    GraphNeighborhoodRelationship("EXCLUDES", "G-wealth", "C-excluded"),
                    GraphNeighborhoodRelationship("STRENGTHENS", "C-strong", "R-wealth"),
                    GraphNeighborhoodRelationship("WEAKENS", "C-weak", "R-wealth"),
                    GraphNeighborhoodRelationship("RELATES_TO", "R-wealth", "K-wealth"),
                    GraphNeighborhoodRelationship("PRODUCES", "R-wealth", "O-stable"),
                    GraphNeighborhoodRelationship("DOES_NOT_PROVE", "R-wealth", "O-rich"),
                    GraphNeighborhoodRelationship("SOURCED_FROM", "R-wealth", "S-book"),
                    GraphNeighborhoodRelationship("REFINES", "R-wealth", "R-related"),
                ),
            ),
        ),
    )
    capability = GraphOrganizerCapability(organizer_context(store))
    tools = {tool.name: tool for tool in capability.tools()}

    search_tool = tools["search_rule_graph"]
    dispatched = await search_tool.execute(
        search_tool.input_model(queries=["财星得地", "官杀混杂"])
    )
    assert dispatched.root[0].query == "财星得地"
    matched_rule = dispatched.root[0].rules[0]
    assert matched_rule.id == "R-wealth"
    assert [group.model_dump() for group in matched_rule.condition_groups] == [
        {"all_of": ["财星得地"], "none_of": ["比劫夺财"]}
    ]
    assert matched_rule.strengthened_by == ["得令"]
    assert matched_rule.weakened_by == ["受制"]
    assert matched_rule.concepts == ["财星"]
    assert matched_rule.outcomes == ["财运稳定"]
    assert matched_rule.does_not_prove == ["必然暴富"]
    assert matched_rule.sources == ["测试命理书"]
    assert matched_rule.related_rules[0].model_dump() == {
        "id": "R-related",
        "name": "财星受制",
        "summary": "财星受制时另论。",
        "relation": "REFINES",
        "direction": "outgoing",
    }
    assert "neighbors" not in matched_rule.model_dump()
    assert "relationships" not in matched_rule.model_dump()
    assert dispatched.root[1].query == "官杀混杂"
    assert dispatched.root[1].rules == []

    submission = GraphExtractionOutput(rules=[extracted_rule()])
    receipt = await tools["submit_rule_graph"].execute(submission)
    assert receipt.accepted is True
    assert receipt.rule_count == 1
    assert receipt.rules_merged == 1
    assert store.list_calls == 2
    assert store.apply_calls[0]["rules"][0].id == "R-wealth"

    result = capability.finalize("最终回答不需要是 JSON")
    assert result.metadata == {"rule_count": 1, "rules_created": 0, "rules_merged": 1}
    assert isinstance(result.artifacts[0], GraphExtractionOutput)
    assert isinstance(result.artifacts[1], GraphApplyResult)

    unsubmitted = GraphOrganizerCapability(organizer_context(FakeLiveGraphStore()))
    with pytest.raises(AgentCapabilityOutputError, match="submit_rule_graph"):
        unsubmitted.finalize("即使是 JSON 也不能代替提交工具")


@pytest.mark.asyncio
async def test_empty_live_graph_still_offers_search_and_submit_tools() -> None:
    store = FakeLiveGraphStore()
    capability = GraphOrganizerCapability(organizer_context(store))
    tools = {tool.name: tool for tool in capability.tools()}

    assert list(tools) == ["search_rule_graph", "submit_rule_graph"]
    search_tool = tools["search_rule_graph"]
    result = await search_tool.execute(
        search_tool.input_model(queries=["财星得地", "官杀混杂"])
    )
    assert [item.query for item in result.root] == ["财星得地", "官杀混杂"]
    assert all(item.rules == [] for item in result.root)
    assert "当前真实规则图谱" in capability.prompt_section()
    assert "只要 rules 非空" in GRAPH_ORGANIZER_INSTRUCTIONS
    assert "适用条件和结论实质相同" in GRAPH_ORGANIZER_INSTRUCTIONS
    assert "快照" not in capability.prompt_section()

    store.rules = (
        GraphRuleSummary(
            id="R-new-live",
            name="官杀失衡规则",
            summary="官杀结构失衡时需要结合条件判断。",
            aliases=(),
            concepts=("官杀",),
            outcomes=(),
            conditions=("官杀混杂",),
        ),
    )
    refreshed = await search_tool.execute(search_tool.input_model(queries=["官杀混杂"]))
    assert refreshed.root[0].rules[0].id == "R-new-live"
    assert store.list_calls == 2


@pytest.mark.asyncio
async def test_graph_search_prefers_complete_multi_condition_coverage() -> None:
    store = FakeLiveGraphStore(
        rules=(
            GraphRuleSummary(
                id="R-complete",
                name="身财配合",
                summary="同时考察身旺与财星状态。",
                aliases=(),
                concepts=("财星",),
                outcomes=("能够任财",),
                conditions=("身旺", "财星得地"),
            ),
            GraphRuleSummary(
                id="R-single",
                name="身旺",
                summary="只说明身旺。",
                aliases=(),
                concepts=(),
                outcomes=(),
            ),
        )
    )
    capability = GraphOrganizerCapability(organizer_context(store))
    search_tool = {tool.name: tool for tool in capability.tools()}["search_rule_graph"]

    result = await search_tool.execute(
        search_tool.input_model(queries=["身旺 财星得地"])
    )

    assert [rule.id for rule in result.root[0].rules] == ["R-complete", "R-single"]
    assert result.root[0].rules[0].score == 135
    assert result.root[0].rules[1].score == 125
    assert [
        (match.kind, match.name, match.match)
        for match in result.root[0].rules[0].matched_on
        if match.kind == "Condition"
    ] == [
        ("Condition", "财星得地", "exact"),
        ("Condition", "身旺", "exact"),
    ]


@pytest.mark.asyncio
async def test_submit_rejects_excerpt_outside_current_section_before_writing() -> None:
    store = FakeLiveGraphStore()
    capability = GraphOrganizerCapability(organizer_context(store))
    submit_tool = {tool.name: tool for tool in capability.tools()}["submit_rule_graph"]
    invalid = GraphExtractionOutput(
        rules=[extracted_rule(source_excerpt="这句话不在当前原文中")]
    )

    with pytest.raises(AgentToolInputError, match="逐字来自当前原文段"):
        await submit_tool.execute(invalid)

    assert store.list_calls == 0
    assert store.apply_calls == []


@pytest.mark.asyncio
async def test_chat_completion_submit_tool_ends_run_without_a_final_answer() -> None:
    requests: list[dict] = []
    extraction = GraphExtractionOutput(rules=[extracted_rule()])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert [tool["function"]["name"] for tool in body["tools"]] == [
                "search_rule_graph",
                "submit_rule_graph",
            ]
            assert "response_format" not in body
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "submit-1",
                                        "type": "function",
                                        "function": {
                                            "name": "submit_rule_graph",
                                            "arguments": extraction.model_dump_json(),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                },
            )
        raise AssertionError("提交工具成功后不应再次请求最终回答")

    section = DocumentSection(
        index=0,
        start=0,
        end=len("财星得地，财有根基。"),
        text="财星得地，财有根基。",
    )
    store = FakeLiveGraphStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await extract_graph_section(
            context=organizer_context(store, section),
            api_protocol="chat_completions",
            model="test-model",
            base_url="https://example.test/v1",
            api_key="test-key",
            client=client,
        )

    assert len(requests) == 1
    assert result.extraction == extraction
    assert result.apply_result.rules_created == 1
    assert result.input_tokens == 20
    assert result.output_tokens == 10
    assert len(store.apply_calls) == 1


@pytest.mark.asyncio
async def test_terminal_submit_is_rejected_before_writing_when_called_with_search() -> None:
    extraction = GraphExtractionOutput(rules=[extracted_rule()])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "search-1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_rule_graph",
                                        "arguments": '{"queries":["财星得地"]}',
                                    },
                                },
                                {
                                    "id": "submit-1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_rule_graph",
                                        "arguments": extraction.model_dump_json(),
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    section = DocumentSection(
        index=0,
        start=0,
        end=len("财星得地，财有根基。"),
        text="财星得地，财有根基。",
    )
    store = FakeLiveGraphStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GraphOrganizerModelError, match="终止工具必须单独调用"):
            await extract_graph_section(
                context=organizer_context(store, section),
                api_protocol="chat_completions",
                model="test-model",
                base_url="https://example.test/v1",
                api_key="test-key",
                client=client,
            )

    assert store.list_calls == 0
    assert store.apply_calls == []
