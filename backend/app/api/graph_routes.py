"""Administrator-only rule graph endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..agent_trace import StoredAgentTrace, trace_model_calls, trace_prompts
from ..auth import AdminUserDependency, AuthRepositoryDependency, request_ip
from ..credentials import ModelCredentialRepository, get_credential_repository
from ..graph_organizer import GRAPH_ORGANIZER_PROMPT_VERSION
from ..graph_organizer_repository import GraphOrganizerRepositoryDependency
from ..graph_organizer_worker import graph_organizer_task_manager
from ..graph_store import GraphStoreDependency, GraphStoreUnavailable
from ..knowledge import KnowledgeRepositoryDependency
from ..models import GraphOrganizingJob, GraphOrganizingTrace
from ..schemas import (
    GraphOrganizingJobListResponse,
    GraphOrganizingJobResponse,
    GraphOrganizingStartRequest,
    GraphOrganizingTraceListResponse,
    GraphOrganizingTraceResponse,
    GraphOrganizingTraceSummaryResponse,
    KnowledgeGraphNodeResponse,
    KnowledgeGraphRelationshipResponse,
    KnowledgeGraphSnapshotResponse,
    KnowledgeGraphStatusResponse,
)

router = APIRouter(prefix="/api/v1/admin/graph", tags=["admin-graph"])
CredentialRepositoryDependency = Annotated[
    ModelCredentialRepository,
    Depends(get_credential_repository),
]


def _job_response(job: GraphOrganizingJob) -> GraphOrganizingJobResponse:
    return GraphOrganizingJobResponse(
        id=job.id,
        document_id=job.document_id,
        document_title=job.document_title,
        model=job.model,
        status=job.status,
        total_sections=job.total_sections,
        processed_sections=job.processed_sections,
        current_offset=job.current_offset,
        rules_extracted=job.rules_extracted,
        rules_created=job.rules_created,
        rules_merged=job.rules_merged,
        conditions_written=job.conditions_written,
        relations_written=job.relations_written,
        conflicts_written=job.conflicts_written,
        ignored_sections=job.ignored_sections,
        input_tokens=job.input_tokens,
        output_tokens=job.output_tokens,
        failure_message=job.failure_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _trace_summary(trace: GraphOrganizingTrace) -> GraphOrganizingTraceSummaryResponse:
    return GraphOrganizingTraceSummaryResponse(
        id=trace.id,
        section_index=trace.section_index,
        attempt=trace.attempt,
        start_offset=trace.start_offset,
        end_offset=trace.end_offset,
        status=trace.status,
        rules_extracted=trace.rules_extracted,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        duration_ms=trace.duration_ms,
        error_message=trace.error_message,
        created_at=trace.created_at,
    )


@router.get("/status", response_model=KnowledgeGraphStatusResponse)
async def graph_status(
    store: GraphStoreDependency,
    _admin: AdminUserDependency,
) -> KnowledgeGraphStatusResponse:
    try:
        stats = await store.stats()
    except GraphStoreUnavailable:
        return KnowledgeGraphStatusResponse(
            connected=False,
            database=store.database,
            node_count=0,
            relationship_count=0,
        )
    return KnowledgeGraphStatusResponse(
        connected=True,
        database=store.database,
        node_count=stats.node_count,
        relationship_count=stats.relationship_count,
    )


@router.get("", response_model=KnowledgeGraphSnapshotResponse)
async def graph_snapshot(
    store: GraphStoreDependency,
    _admin: AdminUserDependency,
) -> KnowledgeGraphSnapshotResponse:
    try:
        snapshot = await store.snapshot()
    except GraphStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="Neo4j 规则图谱当前不可用") from exc
    return KnowledgeGraphSnapshotResponse(
        nodes=[
            KnowledgeGraphNodeResponse(id=node.id, label=node.label, kind=node.kind)
            for node in snapshot.nodes
        ],
        relationships=[
            KnowledgeGraphRelationshipResponse(
                id=relationship.id,
                source=relationship.source,
                target=relationship.target,
                kind=relationship.kind,
            )
            for relationship in snapshot.relationships
        ],
    )


@router.get("/jobs", response_model=GraphOrganizingJobListResponse)
async def list_graph_organizing_jobs(
    _admin: AdminUserDependency,
    repository: GraphOrganizerRepositoryDependency,
) -> GraphOrganizingJobListResponse:
    jobs = await repository.list_recent()
    return GraphOrganizingJobListResponse(items=[_job_response(job) for job in jobs])


@router.get(
    "/jobs/{job_id}/traces",
    response_model=GraphOrganizingTraceListResponse,
)
async def list_graph_organizing_traces(
    job_id: UUID,
    _admin: AdminUserDependency,
    repository: GraphOrganizerRepositoryDependency,
) -> GraphOrganizingTraceListResponse:
    job = await repository.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="整理任务不存在")
    traces = await repository.list_traces(job.id)
    return GraphOrganizingTraceListResponse(items=[_trace_summary(trace) for trace in traces])


@router.get(
    "/jobs/{job_id}/traces/{trace_id}",
    response_model=GraphOrganizingTraceResponse,
)
async def get_graph_organizing_trace(
    job_id: UUID,
    trace_id: int,
    _admin: AdminUserDependency,
    repository: GraphOrganizerRepositoryDependency,
) -> GraphOrganizingTraceResponse:
    job = await repository.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="整理任务不存在")
    stored = await repository.get_trace(job.id, trace_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="整理轨迹不存在")
    trace = (
        StoredAgentTrace.model_validate(stored.agent_trace)
        if stored.agent_trace is not None
        else None
    )
    model_calls = (
        trace_model_calls(trace, api_protocol=job.api_protocol) if trace is not None else []
    )
    tool_executions = list(trace.tool_executions) if trace is not None else []
    system_prompt, user_prompt = (
        trace_prompts(trace.initial_request_body, job.api_protocol)
        if trace is not None
        else (None, None)
    )
    endpoint = (
        f"{job.base_url.rstrip('/')}/responses"
        if job.api_protocol == "responses"
        else f"{job.base_url.rstrip('/')}/chat/completions"
    )
    return GraphOrganizingTraceResponse(
        **_trace_summary(stored).model_dump(),
        document_title=job.document_title,
        api_protocol=job.api_protocol,
        model=job.model,
        endpoint=endpoint,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_calls=model_calls,
        tool_executions=tool_executions,
        redacted=["API 密钥", "Authorization 请求头", "模型内部推理文本"],
    )


@router.post(
    "/jobs",
    response_model=GraphOrganizingJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_graph_organizing_job(
    payload: GraphOrganizingStartRequest,
    request: Request,
    admin: AdminUserDependency,
    repository: GraphOrganizerRepositoryDependency,
    knowledge_repository: KnowledgeRepositoryDependency,
    credential_repository: CredentialRepositoryDependency,
    auth_repository: AuthRepositoryDependency,
    store: GraphStoreDependency,
) -> GraphOrganizingJobResponse:
    document = await knowledge_repository.get_document(payload.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="知识库资料不存在")
    if await repository.active_for_document(document.id) is not None:
        raise HTTPException(status_code=409, detail="这份资料已有自动整理任务正在运行")
    credential = await credential_repository.get()
    if credential is None:
        raise HTTPException(status_code=409, detail="请先配置并测试模型 API")
    try:
        await store.stats()
    except GraphStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="Neo4j 规则图谱当前不可用") from exc

    job = GraphOrganizingJob(
        document_id=document.id,
        document_title=document.title,
        created_by_user_id=admin.id,
        provider=credential.provider,
        api_protocol=credential.api_protocol,
        model=credential.model,
        base_url=credential.base_url,
        prompt_version=GRAPH_ORGANIZER_PROMPT_VERSION,
        status="queued",
    )
    await repository.create(job)
    await auth_repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=None,
        action="admin.graph_organizing_started",
        details={"job_id": str(job.id), "document_id": str(document.id)},
        ip_address=request_ip(request),
    )
    await graph_organizer_task_manager.enqueue(job.id)
    return _job_response(job)
