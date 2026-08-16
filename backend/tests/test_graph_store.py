from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import READ_ACCESS, Query

from app.auth import get_current_user
from app.graph_store import (
    GRAPH_CONSTRAINTS,
    GRAPH_NODE_LABELS,
    GRAPH_READ_QUERY_MAX_ROWS,
    GRAPH_STATS_QUERY,
    RULE_NEIGHBORHOODS_QUERY,
    RULE_SUMMARIES_QUERY,
    SNAPSHOT_NODES_QUERY,
    SNAPSHOT_RELATIONSHIPS_QUERY,
    GraphApplyResult,
    GraphConditionGroup,
    GraphNeighborhoodNode,
    GraphNeighborhoodRelationship,
    GraphReadQueryError,
    GraphRuleMutation,
    GraphRuleNeighborhood,
    GraphRuleSummary,
    GraphSnapshot,
    GraphSnapshotNode,
    GraphSnapshotRelationship,
    GraphSourceSection,
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


class ReadQueryResult(AsyncRecordsResult):
    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        *,
        query_type: str = "r",
    ) -> None:
        super().__init__(records)
        self.query_type = query_type

    async def consume(self):
        self.consumed = True
        return SimpleNamespace(query_type=self.query_type)


class ReadQuerySession:
    def __init__(self, *, query_type: str = "r", records: list[dict[str, Any]] | None = None):
        self.query_type = query_type
        self.records = records or []
        self.queries: list[Query] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query: Query):
        assert isinstance(query, Query)
        self.queries.append(query)
        if query.text.startswith("EXPLAIN"):
            return ReadQueryResult(query_type=self.query_type)
        return ReadQueryResult(self.records)


class ReadQueryDriver:
    def __init__(self, *, query_type: str = "r", records: list[dict[str, Any]] | None = None):
        self.session_instance = ReadQuerySession(query_type=query_type, records=records)
        self.session_calls: list[dict[str, Any]] = []

    def session(self, **kwargs):
        self.session_calls.append(kwargs)
        return self.session_instance


@pytest.mark.asyncio
async def test_graph_store_executes_bounded_read_only_cypher() -> None:
    driver = ReadQueryDriver(
        records=[
            {
                "rule": "财格败条件",
                "conditions": ["财轻比重", "财透七煞"],
                "count": 2,
            }
        ]
    )
    store = make_store(driver)

    rows = await store.execute_read_query(
        "MATCH (rule:Rule)-[*1..2]->(node) RETURN rule.name AS rule, "
        "collect(node.name) AS conditions, count(node) AS count;"
    )

    assert rows == (
        {
            "rule": "财格败条件",
            "conditions": ["财轻比重", "财透七煞"],
            "count": 2,
        },
    )
    assert driver.session_calls == [
        {"database": "neo4j", "default_access_mode": READ_ACCESS}
    ]
    planned, executed = driver.session_instance.queries
    assert planned.text.startswith("EXPLAIN\nMATCH")
    assert executed.text.startswith("CALL () {\nMATCH")
    assert executed.text.endswith(f"LIMIT {GRAPH_READ_QUERY_MAX_ROWS + 1}")
    assert planned.timeout == executed.timeout


@pytest.mark.asyncio
async def test_graph_store_rejects_queries_that_neo4j_marks_as_writing() -> None:
    driver = ReadQueryDriver(query_type="rw")
    store = make_store(driver)

    with pytest.raises(GraphReadQueryError, match="只允许执行读取"):
        await store.execute_read_query("MATCH (node) DELETE node")

    assert len(driver.session_instance.queries) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cypher", "message"),
    [
        ("LOAD CSV FROM 'https://example.com/a.csv' AS row RETURN row", "LOAD CSV"),
        ("CALL db.labels() YIELD label RETURN label", "数据库过程"),
        ("PROFILE MATCH (node) RETURN node", "EXPLAIN 或 PROFILE"),
    ],
)
async def test_graph_store_rejects_unsafe_read_query_forms(
    cypher: str,
    message: str,
) -> None:
    driver = ReadQueryDriver()
    store = make_store(driver)

    with pytest.raises(GraphReadQueryError, match=message):
        await store.execute_read_query(cypher)

    assert driver.session_calls == []


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
        refines_ids=("R-old",) if detailed else (),
        exception_to_ids=("R-old",) if detailed else (),
        conflicts_with_ids=("R-old",) if detailed else (),
        source_sections=(GraphSourceSection(start=2, end=8),),
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
        relations_written=16,
        conflicts_written=1,
    )
    assert len(driver.sessions) == 1
    calls = driver.sessions[0].transaction.calls
    assert len(calls) == 17
    assert all("DROP" not in query.upper() for query, _parameters in calls)
    rule_parameters = next(
        parameters for query, parameters in calls if "SOURCED_FROM" in query
    )
    assert rule_parameters["section_ranges"] == [
        '{"job_id":"job-1","start":2,"end":8}'
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
