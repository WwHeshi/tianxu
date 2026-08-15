from hashlib import sha256
from uuid import UUID, uuid4

import pytest

from app.api import graph_routes
from app.auth import get_current_user
from app.graph_store import (
    GraphSnapshot,
    GraphSnapshotNode,
    GraphSnapshotRelationship,
    GraphStats,
    get_graph_store,
)
from app.main import app
from app.models import (
    AuditLog,
    GraphOrganizingJob,
    GraphOrganizingTrace,
    KnowledgeDocument,
    ModelCredential,
    User,
)


class FakeGraphApiStore:
    database = "neo4j"

    async def stats(self) -> GraphStats:
        return GraphStats(node_count=2, relationship_count=1)

    async def snapshot(self) -> GraphSnapshot:
        return GraphSnapshot(
            nodes=(
                GraphSnapshotNode(id="R-1", label="身旺任财", kind="Rule"),
                GraphSnapshotNode(id="S-1", label="测试资料", kind="Source"),
            ),
            relationships=(
                GraphSnapshotRelationship(
                    id="rel-1",
                    source="R-1",
                    target="S-1",
                    kind="SOURCED_FROM",
                ),
            ),
        )


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


async def add_document_and_credential(session_factory, *, credential: bool = True):
    data = "身旺方能任财。".encode()
    async with session_factory() as session:
        document = KnowledgeDocument(
            title="命理测试资料",
            original_filename="rules.txt",
            encoding="utf-8",
            byte_size=len(data),
            sha256=sha256(data).hexdigest(),
            file_data=data,
        )
        session.add(document)
        if credential:
            session.add(
                ModelCredential(
                    scope="local-default",
                    user_id=None,
                    provider="openai",
                    api_protocol="responses",
                    model="gpt-test",
                    base_url="https://example.test/v1",
                    encrypted_api_key="encrypted",
                    api_key_last_four="test",
                    encryption_key_version="v1",
                )
            )
        await session.commit()
        await session.refresh(document)
        return document


@pytest.mark.asyncio
async def test_graph_snapshot_endpoint_returns_only_store_data(database_client) -> None:
    client, _ = database_client
    app.dependency_overrides[get_current_user] = admin_user
    app.dependency_overrides[get_graph_store] = FakeGraphApiStore
    try:
        response = await client.get("/api/v1/admin/graph")
    finally:
        app.dependency_overrides.pop(get_graph_store, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json() == {
        "nodes": [
            {"id": "R-1", "label": "身旺任财", "kind": "Rule"},
            {"id": "S-1", "label": "测试资料", "kind": "Source"},
        ],
        "relationships": [
            {
                "id": "rel-1",
                "source": "R-1",
                "target": "S-1",
                "kind": "SOURCED_FROM",
            }
        ],
    }


@pytest.mark.asyncio
async def test_start_graph_job_creates_queue_item_and_rejects_duplicate(
    database_client,
    monkeypatch,
) -> None:
    client, session_factory = database_client
    document = await add_document_and_credential(session_factory)
    queued = []

    async def fake_enqueue(job_id):
        queued.append(job_id)

    monkeypatch.setattr(graph_routes.graph_organizer_task_manager, "enqueue", fake_enqueue)
    app.dependency_overrides[get_current_user] = admin_user
    app.dependency_overrides[get_graph_store] = FakeGraphApiStore
    try:
        response = await client.post(
            "/api/v1/admin/graph/jobs",
            json={"document_id": str(document.id)},
        )
        duplicate = await client.post(
            "/api/v1/admin/graph/jobs",
            json={"document_id": str(document.id)},
        )
    finally:
        app.dependency_overrides.pop(get_graph_store, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert response.json()["document_title"] == "命理测试资料"
    assert response.json()["model"] == "gpt-test"
    assert [str(job_id) for job_id in queued] == [response.json()["id"]]
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "这份资料已有自动整理任务正在运行"
    async with session_factory() as session:
        audit = await session.get(AuditLog, 1)
        assert audit is not None
        assert audit.action == "admin.graph_organizing_started"


@pytest.mark.asyncio
async def test_start_graph_job_requires_model_configuration(database_client) -> None:
    client, session_factory = database_client
    document = await add_document_and_credential(session_factory, credential=False)
    app.dependency_overrides[get_current_user] = admin_user
    app.dependency_overrides[get_graph_store] = FakeGraphApiStore
    try:
        response = await client.post(
            "/api/v1/admin/graph/jobs",
            json={"document_id": str(document.id)},
        )
    finally:
        app.dependency_overrides.pop(get_graph_store, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 409
    assert response.json()["detail"] == "请先配置并测试模型 API"


@pytest.mark.asyncio
async def test_graph_trace_endpoints_reuse_agent_trace_shape(database_client) -> None:
    client, session_factory = database_client
    document = await add_document_and_credential(session_factory)
    async with session_factory() as session:
        job = GraphOrganizingJob(
            document_id=document.id,
            document_title=document.title,
            created_by_user_id=None,
            provider="openai",
            api_protocol="chat_completions",
            model="glm-test",
            base_url="https://example.test/v1",
            prompt_version="test",
            status="failed",
        )
        session.add(job)
        await session.flush()
        trace = GraphOrganizingTrace(
            job_id=job.id,
            section_index=0,
            attempt=1,
            start_offset=0,
            end_offset=8,
            status="completed",
            rules_extracted=1,
            input_tokens=20,
            output_tokens=10,
            duration_ms=123,
            agent_trace={
                "initial_request_body": {
                    "model": "glm-test",
                    "messages": [
                        {"role": "system", "content": "system prompt"},
                        {"role": "user", "content": "source section"},
                    ],
                },
                "model_calls": [
                    {
                        "sequence": 1,
                        "stage": "action_selection",
                        "response_body": {
                            "choices": [
                                {
                                    "message": {
                                        "tool_calls": [
                                            {
                                                "id": "submit-1",
                                                "function": {
                                                    "name": "submit_rule_graph",
                                                    "arguments": '{"rules":[]}',
                                                },
                                            }
                                        ]
                                    }
                                }
                            ]
                        },
                        "duration_ms": 123,
                        "tool_call_count": 1,
                    }
                ],
                "tool_executions": [
                    {
                        "sequence": 1,
                        "name": "submit_rule_graph",
                        "input": {"rules": []},
                        "output": {"created": 0, "merged": 0},
                        "duration_ms": 1,
                    }
                ],
            },
        )
        session.add(trace)
        await session.commit()
        await session.refresh(job)
        await session.refresh(trace)
        job_id = UUID(str(job.id))
        trace_id = trace.id

    app.dependency_overrides[get_current_user] = admin_user
    try:
        listed = await client.get(f"/api/v1/admin/graph/jobs/{job_id}/traces")
        detail = await client.get(f"/api/v1/admin/graph/jobs/{job_id}/traces/{trace_id}")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert listed.status_code == 200
    assert listed.json()["items"][0]["section_index"] == 0
    assert detail.status_code == 200
    assert detail.json()["system_prompt"] == "system prompt"
    assert detail.json()["user_prompt"] == "source section"
    assert detail.json()["model_calls"][0]["request_body"]["model"] == "glm-test"
    assert detail.json()["tool_executions"][0]["name"] == "submit_rule_graph"
