from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import get_current_user
from app.graph_store import (
    GRAPH_CONSTRAINTS,
    GRAPH_NODE_LABELS,
    GRAPH_STATS_QUERY,
    RULE_NEIGHBORHOODS_QUERY,
    RULE_SUMMARIES_QUERY,
    SNAPSHOT_NODES_QUERY,
    SNAPSHOT_RELATIONSHIPS_QUERY,
    GraphApplyResult,
    GraphConditionGroup,
    GraphNeighborhoodNode,
    GraphNeighborhoodRelationship,
    GraphRuleMutation,
    GraphRuleNeighborhood,
    GraphRuleSummary,
    GraphSnapshot,
    GraphSnapshotNode,
    GraphSnapshotRelationship,
    GraphSourceExcerpt,
    GraphStats,
    GraphStore,
    GraphStoreUnavailable,
    get_graph_store,
)
from app.main import app
from app.models import User


class FakeResult:
    def __init__(self, record: dict[str, int] | None = None) -> None:
        self.record = record
        self.consumed = False

    async def consume(self) -> None:
        self.consumed = True

    async def single(self, *, strict: bool = False) -> dict[str, int] | None:
        assert strict is True
        return self.record


class FakeSession:
    def __init__(self, queries: list[str]) -> None:
        self.queries = queries
        self.results: list[FakeResult] = []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def run(self, query: str) -> FakeResult:
        self.queries.append(query)
        record = (
            {"node_count": 7, "relationship_count": 11}
            if query == GRAPH_STATS_QUERY
            else None
        )
        result = FakeResult(record)
        self.results.append(result)
        return result


class FakeDriver:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.verified = False
        self.closed = False
        self.databases: list[str] = []
        self.queries: list[str] = []
        self.sessions: list[FakeSession] = []

    async def verify_connectivity(self) -> None:
        if self.unavailable:
            raise OSError("offline")
        self.verified = True

    def session(self, *, database: str) -> FakeSession:
        self.databases.append(database)
        session = FakeSession(self.queries)
        self.sessions.append(session)
        return session

    async def close(self) -> None:
        self.closed = True


def make_store(driver: Any) -> GraphStore:
    return GraphStore("bolt://unused", "neo4j", "password", "neo4j", driver=driver)


@pytest.mark.asyncio
async def test_graph_store_initializes_constraints_and_reads_real_counts() -> None:
    driver = FakeDriver()
    store = make_store(driver)

    await store.start()
    stats = await store.stats()
    await store.close()

    assert driver.verified is True
    assert driver.databases == ["neo4j", "neo4j"]
    assert driver.queries == [*GRAPH_CONSTRAINTS, GRAPH_STATS_QUERY]
    assert all(result.consumed for result in driver.sessions[0].results)
    assert stats == GraphStats(node_count=7, relationship_count=11)
    assert driver.closed is True


@pytest.mark.asyncio
async def test_graph_store_reports_initialization_failure() -> None:
    store = make_store(FakeDriver(unavailable=True))

    with pytest.raises(GraphStoreUnavailable, match="无法连接或初始化"):
        await store.start()


class FakeStatusStore:
    database = "neo4j"

    async def stats(self) -> GraphStats:
        return GraphStats(node_count=3, relationship_count=4)


class FakeUnavailableStatusStore:
    database = "neo4j"

    async def stats(self) -> GraphStats:
        raise GraphStoreUnavailable("offline")


@pytest.fixture
def admin_user() -> User:
    return User(
        id=uuid4(),
        username="graph-admin",
        display_name="Graph Admin",
        password_hash="unused",
        role="admin",
        status="active",
        must_change_password=False,
    )


@pytest.mark.asyncio
async def test_graph_status_endpoint_returns_neo4j_counts(admin_user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_graph_store] = lambda: FakeStatusStore()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/graph/status")
    finally:
        app.dependency_overrides.pop(get_graph_store, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "database": "neo4j",
        "node_count": 3,
        "relationship_count": 4,
    }


@pytest.mark.asyncio
async def test_graph_status_endpoint_reports_unavailable_store(admin_user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_graph_store] = lambda: FakeUnavailableStatusStore()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/graph/status")
    finally:
        app.dependency_overrides.pop(get_graph_store, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "database": "neo4j",
        "node_count": 0,
        "relationship_count": 0,
    }


class AsyncRecordsResult:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []
        self.consumed = False

    def __aiter__(self):
        async def iterate():
            for record in self.records:
                yield record

        return iterate()

    async def consume(self) -> None:
        self.consumed = True


class SnapshotSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query: str, **parameters):
        if query == SNAPSHOT_NODES_QUERY:
            assert parameters == {"labels": list(GRAPH_NODE_LABELS)}
            return AsyncRecordsResult(
                [
                    {"id": "R-1", "label": "身旺任财", "kind": "Rule"},
                    {"id": "S-1", "label": "来源", "kind": "Source"},
                ]
            )
        assert query == SNAPSHOT_RELATIONSHIPS_QUERY
        return AsyncRecordsResult(
            [
                {
                    "id": "rel-valid",
                    "source": "R-1",
                    "target": "S-1",
                    "kind": "SOURCED_FROM",
                },
                {
                    "id": "rel-hidden",
                    "source": "R-1",
                    "target": "outside",
                    "kind": "OTHER",
                },
            ]
        )


class SnapshotDriver:
    def session(self, *, database: str):
        assert database == "neo4j"
        return SnapshotSession()


@pytest.mark.asyncio
async def test_graph_store_snapshot_filters_relationships_to_visible_nodes() -> None:
    store = make_store(SnapshotDriver())

    snapshot = await store.snapshot()

    assert snapshot == GraphSnapshot(
        nodes=(
            GraphSnapshotNode(id="R-1", label="身旺任财", kind="Rule"),
            GraphSnapshotNode(id="S-1", label="来源", kind="Source"),
        ),
        relationships=(
            GraphSnapshotRelationship(
                id="rel-valid",
                source="R-1",
                target="S-1",
                kind="SOURCED_FROM",
            ),
        ),
    )


class NeighborhoodSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query: str, **parameters):
        assert query == RULE_NEIGHBORHOODS_QUERY
        assert parameters == {
            "rule_ids": ["R-1", "R-empty"],
            "labels": list(GRAPH_NODE_LABELS),
        }
        return AsyncRecordsResult(
            [
                {
                    "root_id": "R-1",
                    "node_id": "G-1",
                    "node_kind": "ConditionGroup",
                    "node_name": "身旺",
                    "node_summary": "",
                    "node_aliases": [],
                    "source_id": "R-1",
                    "target_id": "G-1",
                    "relationship_kind": "HAS_CONDITION_GROUP",
                },
                {
                    "root_id": "R-1",
                    "node_id": "C-1",
                    "node_kind": "Condition",
                    "node_name": "身旺",
                    "node_summary": "",
                    "node_aliases": [],
                    "source_id": "G-1",
                    "target_id": "C-1",
                    "relationship_kind": "REQUIRES",
                },
            ]
        )


class NeighborhoodDriver:
    def session(self, *, database: str):
        assert database == "neo4j"
        return NeighborhoodSession()


@pytest.mark.asyncio
async def test_graph_store_returns_complete_rule_neighborhoods() -> None:
    store = make_store(NeighborhoodDriver())

    neighborhoods = await store.get_rule_neighborhoods(("R-1", "R-empty", "R-1"))

    assert neighborhoods == (
        GraphRuleNeighborhood(
            rule_id="R-1",
            nodes=(
                GraphNeighborhoodNode("G-1", "ConditionGroup", "身旺", "", ()),
                GraphNeighborhoodNode("C-1", "Condition", "身旺", "", ()),
            ),
            relationships=(
                GraphNeighborhoodRelationship("HAS_CONDITION_GROUP", "R-1", "G-1"),
                GraphNeighborhoodRelationship("REQUIRES", "G-1", "C-1"),
            ),
        ),
        GraphRuleNeighborhood(rule_id="R-empty", nodes=(), relationships=()),
    )


class RuleSummarySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query: str, **parameters):
        assert query == RULE_SUMMARIES_QUERY
        assert parameters == {}
        return AsyncRecordsResult(
            [
                {
                    "id": "R-1",
                    "name": "身旺任财",
                    "summary": "身旺时较能任财。",
                    "aliases": ["身强任财"],
                    "concepts": ["财星"],
                    "outcomes": ["任财"],
                    "conditions": ["日主身旺", "财星得地"],
                }
            ]
        )


class RuleSummaryDriver:
    def session(self, *, database: str):
        assert database == "neo4j"
        return RuleSummarySession()


@pytest.mark.asyncio
async def test_graph_store_indexes_rule_concepts_conditions_and_outcomes() -> None:
    store = make_store(RuleSummaryDriver())

    summaries = await store.list_rule_summaries()

    assert summaries == (
        GraphRuleSummary(
            id="R-1",
            name="身旺任财",
            summary="身旺时较能任财。",
            aliases=("身强任财",),
            concepts=("财星",),
            outcomes=("任财",),
            conditions=("日主身旺", "财星得地"),
        ),
    )


class ApplyTransaction:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(self, query: str, **parameters):
        self.calls.append((query, parameters))
        return AsyncRecordsResult()


class ApplySession:
    def __init__(self) -> None:
        self.transaction = ApplyTransaction()
        self.existing_ids: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query: str, **parameters):
        assert query.startswith("MATCH (rule:Rule) WHERE rule.id IN")
        assert parameters["ids"] == ["R-old", "R-new"]
        return AsyncRecordsResult([{"id": "R-old"}])

    async def execute_write(self, callback):
        return await callback(self.transaction)


class ApplyDriver:
    def __init__(self) -> None:
        self.sessions: list[ApplySession] = []

    def session(self, *, database: str):
        assert database == "neo4j"
        session = ApplySession()
        self.sessions.append(session)
        return session


def graph_mutation(rule_id: str, *, detailed: bool) -> GraphRuleMutation:
    return GraphRuleMutation(
        id=rule_id,
        name="详细规则" if detailed else "既有规则",
        summary="摘要",
        aliases=("别名",),
        concepts=("财星",) if detailed else (),
        condition_groups=(
            (
                GraphConditionGroup(
                    all_of=("身旺", "财星得地"),
                    none_of=("比劫夺财",),
                ),
                GraphConditionGroup(all_of=("食神生财",), none_of=()),
            )
            if detailed
            else ()
        ),
        strengthened_by=("得令",) if detailed else (),
        weakened_by=("受制",) if detailed else (),
        outcomes=("财运",) if detailed else (),
        does_not_prove=("暴富",) if detailed else (),
        equivalent_to_ids=("R-old",) if detailed else (),
        refines_ids=("R-old",) if detailed else (),
        exception_to_ids=("R-old",) if detailed else (),
        conflicts_with_ids=("R-old",) if detailed else (),
        excerpts=(GraphSourceExcerpt(text="身旺方能任财", start=2, end=8),),
    )


@pytest.mark.asyncio
async def test_graph_store_applies_validated_rules_in_one_write_transaction() -> None:
    driver = ApplyDriver()
    store = make_store(driver)

    result = await store.apply_rules(
        job_id="job-1",
        document_id="doc-1",
        document_title="测试资料",
        document_sha256="abc",
        rules=(graph_mutation("R-old", detailed=False), graph_mutation("R-new", detailed=True)),
    )

    assert result == GraphApplyResult(
        rules_created=1,
        rules_merged=1,
        conditions_written=6,
        relations_written=17,
        conflicts_written=1,
    )
    assert len(driver.sessions) == 1
    calls = driver.sessions[0].transaction.calls
    assert len(calls) == 18
    assert all("DROP" not in query.upper() for query, _parameters in calls)
    rule_parameters = next(
        parameters for query, parameters in calls if "SOURCED_FROM" in query
    )
    assert rule_parameters["excerpts"] == [
        '{"text":"身旺方能任财","start":2,"end":8}'
    ]


@pytest.mark.asyncio
async def test_graph_store_does_not_create_source_for_empty_change_set() -> None:
    driver = ApplyDriver()
    store = make_store(driver)

    result = await store.apply_rules(
        job_id="job-empty",
        document_id="doc-empty",
        document_title="空资料",
        document_sha256="empty",
        rules=(),
    )

    assert result == GraphApplyResult(0, 0, 0, 0, 0)
    assert driver.sessions == []
