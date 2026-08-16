"""Neo4j-backed storage for the administrator-maintained rule graph."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Any
from unicodedata import normalize

from fastapi import Depends
from neo4j import READ_ACCESS, AsyncDriver, AsyncGraphDatabase, Query
from neo4j.exceptions import Neo4jError
from neo4j.graph import Node, Path, Relationship

from .config import neo4j_database, neo4j_password, neo4j_uri, neo4j_username

GRAPH_CONSTRAINTS = (
    "CREATE CONSTRAINT rule_id_unique IF NOT EXISTS FOR (node:Rule) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT condition_group_id_unique IF NOT EXISTS "
    "FOR (node:ConditionGroup) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT condition_id_unique IF NOT EXISTS "
    "FOR (node:Condition) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT concept_id_unique IF NOT EXISTS "
    "FOR (node:Concept) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT outcome_id_unique IF NOT EXISTS "
    "FOR (node:Outcome) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT source_id_unique IF NOT EXISTS FOR (node:Source) REQUIRE node.id IS UNIQUE",
)

GRAPH_STATS_QUERY = """
MATCH (node)
WITH count(node) AS node_count
OPTIONAL MATCH ()-[relationship]->()
RETURN node_count, count(relationship) AS relationship_count
""".strip()


class GraphStoreUnavailable(RuntimeError):
    """Raised when Neo4j cannot serve a storage operation."""


class GraphReadQueryError(RuntimeError):
    """Raised when a model-supplied graph query is invalid or not read-only."""


GRAPH_READ_QUERY_MAX_ROWS = 100
GRAPH_READ_QUERY_MAX_BYTES = 100_000
GRAPH_READ_QUERY_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class GraphStats:
    node_count: int
    relationship_count: int


@dataclass(frozen=True)
class GraphRuleSummary:
    id: str
    name: str
    summary: str
    aliases: tuple[str, ...]
    concepts: tuple[str, ...]
    outcomes: tuple[str, ...]
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphNeighborhoodNode:
    id: str
    kind: str
    name: str
    summary: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class GraphNeighborhoodRelationship:
    kind: str
    source_id: str
    target_id: str


@dataclass(frozen=True)
class GraphRuleNeighborhood:
    rule_id: str
    nodes: tuple[GraphNeighborhoodNode, ...]
    relationships: tuple[GraphNeighborhoodRelationship, ...]


@dataclass(frozen=True)
class GraphSourceSection:
    start: int
    end: int


@dataclass(frozen=True)
class GraphConditionGroup:
    all_of: tuple[str, ...]
    none_of: tuple[str, ...]


@dataclass(frozen=True)
class GraphRuleMutation:
    id: str
    name: str
    summary: str
    aliases: tuple[str, ...]
    concepts: tuple[str, ...]
    condition_groups: tuple[GraphConditionGroup, ...]
    strengthened_by: tuple[str, ...]
    weakened_by: tuple[str, ...]
    outcomes: tuple[str, ...]
    does_not_prove: tuple[str, ...]
    equivalent_to_ids: tuple[str, ...]
    refines_ids: tuple[str, ...]
    exception_to_ids: tuple[str, ...]
    conflicts_with_ids: tuple[str, ...]
    source_sections: tuple[GraphSourceSection, ...]


@dataclass(frozen=True)
class GraphApplyResult:
    rules_created: int
    rules_merged: int
    conditions_written: int
    relations_written: int
    conflicts_written: int


@dataclass(frozen=True)
class GraphSnapshotNode:
    id: str
    label: str
    kind: str


@dataclass(frozen=True)
class GraphSnapshotRelationship:
    id: str
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: tuple[GraphSnapshotNode, ...]
    relationships: tuple[GraphSnapshotRelationship, ...]


RULE_SUMMARIES_QUERY = """
MATCH (rule:Rule)
CALL (rule) {
    OPTIONAL MATCH (rule)-[:RELATES_TO]->(concept:Concept)
    RETURN collect(DISTINCT concept.name) AS concepts
}
CALL (rule) {
    OPTIONAL MATCH (rule)-[relationship]->(outcome:Outcome)
    WHERE type(relationship) IN ['PRODUCES', 'DOES_NOT_PROVE']
    RETURN collect(DISTINCT outcome.name) AS outcomes
}
CALL (rule) {
    OPTIONAL MATCH (rule)-[:HAS_CONDITION_GROUP]->(:ConditionGroup)
          -[relationship]->(condition:Condition)
    WHERE type(relationship) IN ['REQUIRES', 'EXCLUDES']
    RETURN collect(DISTINCT condition.name) AS group_conditions
}
CALL (rule) {
    OPTIONAL MATCH (condition:Condition)-[relationship]->(rule)
    WHERE type(relationship) IN ['STRENGTHENS', 'WEAKENS']
    RETURN collect(DISTINCT condition.name) AS modifier_conditions
}
RETURN rule.id AS id,
       rule.name AS name,
       coalesce(rule.summary, '') AS summary,
       coalesce(rule.aliases, []) AS aliases,
       concepts,
       outcomes,
       group_conditions + modifier_conditions AS conditions
ORDER BY rule.name
""".strip()

RULE_NEIGHBORHOODS_QUERY = """
UNWIND $rule_ids AS root_id
MATCH (root:Rule {id: root_id})
CALL (root) {
    MATCH (root)-[relationship]-(neighbor)
    WHERE any(label IN labels(neighbor) WHERE label IN $labels)
    RETURN neighbor, relationship
    UNION ALL
    MATCH (root)-[:HAS_CONDITION_GROUP]->(group:ConditionGroup)
          -[relationship]-(neighbor:Condition)
    WHERE type(relationship) IN ['REQUIRES', 'EXCLUDES']
    RETURN neighbor, relationship
}
RETURN root_id,
       neighbor.id AS node_id,
       head([label IN labels(neighbor) WHERE label IN $labels]) AS node_kind,
       coalesce(neighbor.name, neighbor.title, neighbor.id) AS node_name,
       coalesce(neighbor.summary, '') AS node_summary,
       coalesce(neighbor.aliases, []) AS node_aliases,
       startNode(relationship).id AS source_id,
       endNode(relationship).id AS target_id,
       type(relationship) AS relationship_kind
ORDER BY root_id, node_kind, node_name, relationship_kind, source_id, target_id
""".strip()

SNAPSHOT_NODES_QUERY = """
MATCH (node)
WHERE any(label IN labels(node) WHERE label IN $labels)
RETURN node.id AS id,
       coalesce(node.name, node.title, node.id) AS label,
       head([label IN labels(node) WHERE label IN $labels]) AS kind
ORDER BY kind, label
""".strip()

SNAPSHOT_RELATIONSHIPS_QUERY = """
MATCH (source)-[relationship]->(target)
WHERE source.id IS NOT NULL AND target.id IS NOT NULL
RETURN elementId(relationship) AS id,
       source.id AS source,
       target.id AS target,
       type(relationship) AS kind
ORDER BY kind, source, target
""".strip()

GRAPH_NODE_LABELS = ("Rule", "ConditionGroup", "Condition", "Concept", "Outcome", "Source")


def normalize_graph_key(value: str) -> str:
    return "".join(
        character for character in normalize("NFKC", value).casefold() if character.isalnum()
    )


def stable_graph_node_id(prefix: str, value: str) -> str:
    key = normalize_graph_key(value)
    return f"{prefix}-{sha256(key.encode('utf-8')).hexdigest()[:20]}"


def _cypher_code_only(value: str) -> str:
    """Mask literals, quoted identifiers and comments before clause inspection."""

    result: list[str] = []
    index = 0
    state = "code"
    quote = ""
    while index < len(value):
        current = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if state == "code":
            if current == "/" and following == "/":
                result.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if current == "/" and following == "*":
                result.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            if current in {"'", '"', "`"}:
                result.append(" ")
                index += 1
                state = "quoted"
                quote = current
                continue
            result.append(current)
            index += 1
            continue
        if state == "line_comment":
            result.append("\n" if current == "\n" else " ")
            index += 1
            if current == "\n":
                state = "code"
            continue
        if state == "block_comment":
            if current == "*" and following == "/":
                result.extend((" ", " "))
                index += 2
                state = "code"
            else:
                result.append("\n" if current == "\n" else " ")
                index += 1
            continue

        result.append(" ")
        index += 1
        if current == "\\" and index < len(value):
            result.append(" ")
            index += 1
        elif current == quote:
            if index < len(value) and value[index] == quote:
                result.append(" ")
                index += 1
            else:
                state = "code"
                quote = ""
    return "".join(result)


def _validate_free_read_cypher(value: str) -> str:
    cypher = value.strip()
    if cypher.endswith(";"):
        cypher = cypher[:-1].rstrip()
    if not cypher:
        raise GraphReadQueryError("cypher 不能为空")

    code = _cypher_code_only(cypher)
    if re.search(r"\bLOAD\s+CSV\b", code, re.IGNORECASE):
        raise GraphReadQueryError("只读查询不允许使用 LOAD CSV")
    if re.match(r"\s*(?:EXPLAIN|PROFILE)\b", code, re.IGNORECASE):
        raise GraphReadQueryError("请直接提交查询语句，不要添加 EXPLAIN 或 PROFILE")
    for match in re.finditer(r"\bCALL\b", code, re.IGNORECASE):
        remainder = code[match.end() :].lstrip()
        if remainder.startswith("{"):
            continue
        if remainder.startswith("("):
            closing = remainder.find(")")
            if closing >= 0 and remainder[closing + 1 :].lstrip().startswith("{"):
                continue
        raise GraphReadQueryError("只读查询不允许调用数据库过程")
    return cypher


def _graph_value_to_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Node):
        return {
            "element_id": value.element_id,
            "labels": sorted(value.labels),
            "properties": {
                str(key): _graph_value_to_json(item) for key, item in value.items()
            },
        }
    if isinstance(value, Relationship):
        start_node = getattr(value, "start_node", None)
        end_node = getattr(value, "end_node", None)
        return {
            "element_id": value.element_id,
            "type": value.type,
            "start_node_element_id": getattr(start_node, "element_id", None),
            "end_node_element_id": getattr(end_node, "element_id", None),
            "properties": {
                str(key): _graph_value_to_json(item) for key, item in value.items()
            },
        }
    if isinstance(value, Path):
        return {
            "nodes": [_graph_value_to_json(node) for node in value.nodes],
            "relationships": [
                _graph_value_to_json(relationship) for relationship in value.relationships
            ],
        }
    if isinstance(value, Mapping):
        return {str(key): _graph_value_to_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_graph_value_to_json(item) for item in value]
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        return iso_format()
    return str(value)


class GraphStore:
    """Own the Neo4j driver and initialize the graph schema."""

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str,
        *,
        driver: AsyncDriver | None = None,
    ) -> None:
        self.database = database
        self._driver = driver or AsyncGraphDatabase.driver(
            uri,
            auth=(username, password),
            connection_timeout=5,
        )

    async def start(self) -> None:
        """Verify connectivity and create idempotent uniqueness constraints."""

        try:
            await self._driver.verify_connectivity()
            async with self._driver.session(database=self.database) as session:
                for query in GRAPH_CONSTRAINTS:
                    result = await session.run(query)
                    await result.consume()
        except Exception as exc:
            raise GraphStoreUnavailable("无法连接或初始化 Neo4j 规则图谱") from exc

    async def close(self) -> None:
        await self._driver.close()

    async def stats(self) -> GraphStats:
        """Return counts from the real graph without creating placeholder data."""

        try:
            async with self._driver.session(database=self.database) as session:
                result = await session.run(GRAPH_STATS_QUERY)
                record = await result.single(strict=True)
        except Exception as exc:
            raise GraphStoreUnavailable("Neo4j 规则图谱当前不可用") from exc
        return GraphStats(
            node_count=int(record["node_count"]),
            relationship_count=int(record["relationship_count"]),
        )

    async def list_rule_summaries(self) -> tuple[GraphRuleSummary, ...]:
        try:
            async with self._driver.session(database=self.database) as session:
                result = await session.run(RULE_SUMMARIES_QUERY)
                records = [record async for record in result]
        except Exception as exc:
            raise GraphStoreUnavailable("无法读取 Neo4j 现有规则") from exc
        return tuple(
            GraphRuleSummary(
                id=str(record["id"]),
                name=str(record["name"]),
                summary=str(record["summary"]),
                aliases=tuple(str(value) for value in record["aliases"] if value),
                concepts=tuple(str(value) for value in record["concepts"] if value),
                outcomes=tuple(str(value) for value in record["outcomes"] if value),
                conditions=tuple(str(value) for value in record["conditions"] if value),
            )
            for record in records
        )

    async def get_rule_neighborhoods(
        self,
        rule_ids: tuple[str, ...],
    ) -> tuple[GraphRuleNeighborhood, ...]:
        unique_ids = tuple(dict.fromkeys(rule_ids))
        if not unique_ids:
            return ()
        try:
            async with self._driver.session(database=self.database) as session:
                result = await session.run(
                    RULE_NEIGHBORHOODS_QUERY,
                    rule_ids=list(unique_ids),
                    labels=list(GRAPH_NODE_LABELS),
                )
                records = [record async for record in result]
        except Exception as exc:
            raise GraphStoreUnavailable("无法读取 Neo4j 规则邻域") from exc

        nodes_by_rule: dict[str, dict[str, GraphNeighborhoodNode]] = {
            rule_id: {} for rule_id in unique_ids
        }
        relationships_by_rule: dict[
            str,
            dict[tuple[str, str, str], GraphNeighborhoodRelationship],
        ] = {rule_id: {} for rule_id in unique_ids}
        for record in records:
            rule_id = str(record["root_id"])
            node = GraphNeighborhoodNode(
                id=str(record["node_id"]),
                kind=str(record["node_kind"]),
                name=str(record["node_name"]),
                summary=str(record["node_summary"]),
                aliases=tuple(str(value) for value in record["node_aliases"] if value),
            )
            nodes_by_rule[rule_id].setdefault(node.id, node)
            relationship = GraphNeighborhoodRelationship(
                kind=str(record["relationship_kind"]),
                source_id=str(record["source_id"]),
                target_id=str(record["target_id"]),
            )
            relationship_key = (
                relationship.kind,
                relationship.source_id,
                relationship.target_id,
            )
            relationships_by_rule[rule_id].setdefault(relationship_key, relationship)

        return tuple(
            GraphRuleNeighborhood(
                rule_id=rule_id,
                nodes=tuple(nodes_by_rule[rule_id].values()),
                relationships=tuple(relationships_by_rule[rule_id].values()),
            )
            for rule_id in unique_ids
        )

    async def execute_read_query(self, cypher: str) -> tuple[dict[str, Any], ...]:
        """Execute one model-supplied Cypher query after Neo4j confirms it is read-only."""

        validated_cypher = _validate_free_read_cypher(cypher)
        planned_query = Query(
            f"EXPLAIN\n{validated_cypher}",
            timeout=GRAPH_READ_QUERY_TIMEOUT_SECONDS,
        )
        bounded_query = Query(
            "CALL () {\n"
            f"{validated_cypher}\n"
            "}\n"
            "RETURN *\n"
            f"LIMIT {GRAPH_READ_QUERY_MAX_ROWS + 1}",
            timeout=GRAPH_READ_QUERY_TIMEOUT_SECONDS,
        )
        try:
            async with self._driver.session(
                database=self.database,
                default_access_mode=READ_ACCESS,
            ) as session:
                planned_result = await session.run(planned_query)
                plan_summary = await planned_result.consume()
                if plan_summary.query_type != "r":
                    raise GraphReadQueryError("只允许执行读取图谱的 Cypher 查询")

                result = await session.run(bounded_query)
                rows: list[dict[str, Any]] = []
                response_bytes = 2
                async for record in result:
                    if len(rows) >= GRAPH_READ_QUERY_MAX_ROWS:
                        raise GraphReadQueryError(
                            f"查询结果超过 {GRAPH_READ_QUERY_MAX_ROWS} 行，请使用 WHERE、"
                            "SKIP 或 LIMIT 缩小范围"
                        )
                    row = {
                        str(key): _graph_value_to_json(value)
                        for key, value in record.items()
                    }
                    response_bytes += len(
                        json.dumps(
                            row,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    )
                    if response_bytes > GRAPH_READ_QUERY_MAX_BYTES:
                        raise GraphReadQueryError(
                            "查询结果内容过大，请减少返回字段或缩小查询范围"
                        )
                    rows.append(row)
                await result.consume()
        except GraphReadQueryError:
            raise
        except Neo4jError as exc:
            detail = getattr(exc, "message", None) or str(exc)
            raise GraphReadQueryError(f"Cypher 查询失败：{detail}") from exc
        except Exception as exc:
            raise GraphStoreUnavailable("无法执行 Neo4j 只读查询") from exc
        return tuple(rows)

    async def snapshot(self) -> GraphSnapshot:
        try:
            async with self._driver.session(database=self.database) as session:
                node_result = await session.run(
                    SNAPSHOT_NODES_QUERY,
                    labels=list(GRAPH_NODE_LABELS),
                )
                node_records = [record async for record in node_result]
                relationship_result = await session.run(SNAPSHOT_RELATIONSHIPS_QUERY)
                relationship_records = [record async for record in relationship_result]
        except Exception as exc:
            raise GraphStoreUnavailable("无法读取 Neo4j 图谱") from exc
        node_ids = {str(record["id"]) for record in node_records}
        return GraphSnapshot(
            nodes=tuple(
                GraphSnapshotNode(
                    id=str(record["id"]),
                    label=str(record["label"]),
                    kind=str(record["kind"]),
                )
                for record in node_records
            ),
            relationships=tuple(
                GraphSnapshotRelationship(
                    id=str(record["id"]),
                    source=str(record["source"]),
                    target=str(record["target"]),
                    kind=str(record["kind"]),
                )
                for record in relationship_records
                if str(record["source"]) in node_ids and str(record["target"]) in node_ids
            ),
        )

    async def apply_rules(
        self,
        *,
        job_id: str,
        document_id: str,
        document_title: str,
        document_sha256: str,
        rules: tuple[GraphRuleMutation, ...],
    ) -> GraphApplyResult:
        """Atomically merge one validated document change set into Neo4j."""

        if not rules:
            return GraphApplyResult(
                rules_created=0,
                rules_merged=0,
                conditions_written=0,
                relations_written=0,
                conflicts_written=0,
            )

        rule_ids = [rule.id for rule in rules]
        try:
            async with self._driver.session(database=self.database) as session:
                existing_result = await session.run(
                    "MATCH (rule:Rule) WHERE rule.id IN $ids RETURN rule.id AS id",
                    ids=rule_ids,
                )
                existing_ids = {str(record["id"]) async for record in existing_result}

                async def write_change_set(transaction) -> None:
                    source_result = await transaction.run(
                        """
                        MERGE (source:Source {id: $document_id})
                        ON CREATE SET source.document_id = $document_id,
                                      source.title = $document_title,
                                      source.sha256 = $document_sha256,
                                      source.created_at = datetime()
                        SET source.title = $document_title,
                            source.sha256 = $document_sha256,
                            source.updated_at = datetime()
                        """,
                        document_id=document_id,
                        document_title=document_title,
                        document_sha256=document_sha256,
                    )
                    await source_result.consume()

                    for rule in rules:
                        rule_result = await transaction.run(
                            """
                            MERGE (rule:Rule {id: $id})
                            ON CREATE SET rule.name = $name,
                                          rule.summary = $summary,
                                          rule.aliases = $aliases,
                                          rule.created_at = datetime()
                            ON MATCH SET rule.aliases = reduce(
                                values = coalesce(rule.aliases, []), alias IN $aliases |
                                CASE WHEN alias IN values THEN values ELSE values + alias END
                            )
                            SET rule.condition_group_logic = 'ANY',
                                rule.updated_at = datetime()
                            WITH rule
                            MATCH (source:Source {id: $document_id})
                            MERGE (rule)-[relation:SOURCED_FROM]->(source)
                            SET relation.job_ids = reduce(
                                    values = coalesce(relation.job_ids, []), value IN [$job_id] |
                                    CASE WHEN value IN values THEN values ELSE values + value END
                                ),
                                relation.section_ranges = reduce(
                                    values = coalesce(relation.section_ranges, []),
                                    value IN $section_ranges |
                                    CASE WHEN value IN values THEN values ELSE values + value END
                                )
                            REMOVE relation.excerpts
                            """,
                            id=rule.id,
                            name=rule.name,
                            summary=rule.summary,
                            aliases=list(rule.aliases),
                            document_id=document_id,
                            job_id=job_id,
                            section_ranges=[
                                json.dumps(
                                    {
                                        "job_id": job_id,
                                        "start": item.start,
                                        "end": item.end,
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                                for item in rule.source_sections
                            ],
                        )
                        await rule_result.consume()
                        await self._write_named_relations(transaction, rule, document_id, job_id)

                await session.execute_write(write_change_set)
        except Exception as exc:
            raise GraphStoreUnavailable("自动变更集写入 Neo4j 失败") from exc

        condition_ids = {
            stable_graph_node_id("C", value)
            for rule in rules
            for value in (
                *(
                    value
                    for group in rule.condition_groups
                    for value in (*group.all_of, *group.none_of)
                ),
                *rule.strengthened_by,
                *rule.weakened_by,
            )
        }
        relation_count = sum(
            1
            + len(rule.concepts)
            + sum(
                1 + len(group.all_of) + len(group.none_of)
                for group in rule.condition_groups
            )
            + len(rule.strengthened_by)
            + len(rule.weakened_by)
            + len(rule.outcomes)
            + len(rule.does_not_prove)
            + len(rule.equivalent_to_ids)
            + len(rule.refines_ids)
            + len(rule.exception_to_ids)
            + len(rule.conflicts_with_ids)
            for rule in rules
        )
        return GraphApplyResult(
            rules_created=len(set(rule_ids) - existing_ids),
            rules_merged=len(set(rule_ids) & existing_ids),
            conditions_written=len(condition_ids),
            relations_written=relation_count,
            conflicts_written=sum(len(rule.conflicts_with_ids) for rule in rules),
        )

    async def _write_named_relations(
        self,
        transaction,
        rule: GraphRuleMutation,
        document_id: str,
        job_id: str,
    ) -> None:
        relation_groups = (
            ("Concept", "RELATES_TO", rule.concepts, "K", "out"),
            ("Condition", "STRENGTHENS", rule.strengthened_by, "C", "in"),
            ("Condition", "WEAKENS", rule.weakened_by, "C", "in"),
            ("Outcome", "PRODUCES", rule.outcomes, "O", "out"),
            ("Outcome", "DOES_NOT_PROVE", rule.does_not_prove, "O", "out"),
        )
        for label, relation_type, values, prefix, direction in relation_groups:
            relation_pattern = (
                f"(rule)-[relation:{relation_type}]->(node)"
                if direction == "out"
                else f"(node)-[relation:{relation_type}]->(rule)"
            )
            query = f"""
            MATCH (rule:Rule {{id: $rule_id}})
            MERGE (node:{label} {{id: $node_id}})
            ON CREATE SET node.name = $name, node.created_at = datetime()
            SET node.updated_at = datetime()
            MERGE {relation_pattern}
            SET relation.source_ids = reduce(
                    items = coalesce(relation.source_ids, []), value IN [$document_id] |
                    CASE WHEN value IN items THEN items ELSE items + value END
                ),
                relation.job_ids = reduce(
                    items = coalesce(relation.job_ids, []), value IN [$job_id] |
                    CASE WHEN value IN items THEN items ELSE items + value END
                )
            """
            for value in values:
                result = await transaction.run(
                    query,
                    rule_id=rule.id,
                    node_id=stable_graph_node_id(prefix, value),
                    name=value,
                    document_id=document_id,
                    job_id=job_id,
                )
                await result.consume()

        for group in rule.condition_groups:
            group_key = (
                f"{rule.id} all {' and '.join(group.all_of)} "
                f"none {' and '.join(group.none_of)}"
            )
            group_id = stable_graph_node_id("G", group_key)
            group_name_parts = ["且".join(group.all_of)] if group.all_of else []
            if group.none_of:
                group_name_parts.append(f"排除：{'、'.join(group.none_of)}")
            group_name = "；".join(group_name_parts)
            group_result = await transaction.run(
                """
                MATCH (rule:Rule {id: $rule_id})
                MERGE (group:ConditionGroup {id: $group_id})
                ON CREATE SET group.created_at = datetime()
                SET group.name = $group_name,
                    group.logic = 'ALL',
                    group.updated_at = datetime()
                MERGE (rule)-[relation:HAS_CONDITION_GROUP]->(group)
                SET relation.source_ids = reduce(
                        items = coalesce(relation.source_ids, []), value IN [$document_id] |
                        CASE WHEN value IN items THEN items ELSE items + value END
                    ),
                    relation.job_ids = reduce(
                        items = coalesce(relation.job_ids, []), value IN [$job_id] |
                        CASE WHEN value IN items THEN items ELSE items + value END
                    )
                """,
                rule_id=rule.id,
                group_id=group_id,
                group_name=group_name,
                document_id=document_id,
                job_id=job_id,
            )
            await group_result.consume()
            for relation_type, values in (
                ("REQUIRES", group.all_of),
                ("EXCLUDES", group.none_of),
            ):
                query = f"""
                MATCH (group:ConditionGroup {{id: $group_id}})
                MERGE (condition:Condition {{id: $condition_id}})
                ON CREATE SET condition.name = $name, condition.created_at = datetime()
                SET condition.updated_at = datetime()
                MERGE (group)-[relation:{relation_type}]->(condition)
                SET relation.source_ids = reduce(
                        items = coalesce(relation.source_ids, []), value IN [$document_id] |
                        CASE WHEN value IN items THEN items ELSE items + value END
                    ),
                    relation.job_ids = reduce(
                        items = coalesce(relation.job_ids, []), value IN [$job_id] |
                        CASE WHEN value IN items THEN items ELSE items + value END
                    )
                """
                for value in values:
                    result = await transaction.run(
                        query,
                        group_id=group_id,
                        condition_id=stable_graph_node_id("C", value),
                        name=value,
                        document_id=document_id,
                        job_id=job_id,
                    )
                    await result.consume()

        rule_relations = (
            ("EQUIVALENT_TO", rule.equivalent_to_ids),
            ("REFINES", rule.refines_ids),
            ("EXCEPTION_TO", rule.exception_to_ids),
            ("CONTRADICTS", rule.conflicts_with_ids),
        )
        for relation_type, other_ids in rule_relations:
            query = f"""
            MATCH (rule:Rule {{id: $rule_id}}), (other:Rule {{id: $other_id}})
            MERGE (rule)-[relation:{relation_type}]->(other)
            SET relation.source_ids = reduce(
                    items = coalesce(relation.source_ids, []), value IN [$document_id] |
                    CASE WHEN value IN items THEN items ELSE items + value END
                ),
                relation.job_ids = reduce(
                    items = coalesce(relation.job_ids, []), value IN [$job_id] |
                    CASE WHEN value IN items THEN items ELSE items + value END
                )
            """
            for other_id in other_ids:
                result = await transaction.run(
                    query,
                    rule_id=rule.id,
                    other_id=other_id,
                    document_id=document_id,
                    job_id=job_id,
                )
                await result.consume()


graph_store = GraphStore(
    neo4j_uri(),
    neo4j_username(),
    neo4j_password(),
    neo4j_database(),
)


def get_graph_store() -> GraphStore:
    return graph_store


GraphStoreDependency = Annotated[GraphStore, Depends(get_graph_store)]
