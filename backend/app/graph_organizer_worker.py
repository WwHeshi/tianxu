"""Single durable background queue for automatic TXT-to-graph organizing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

import httpx
from sqlalchemy import func, select

from .auth import utc_now
from .credentials import LOCAL_CREDENTIAL_SCOPE, ModelCredentialRepository
from .database import SessionFactory
from .graph_organizer import (
    GraphOrganizerContext,
    GraphOrganizerModelError,
    extract_graph_section,
    split_document_sections,
)
from .graph_organizer_repository import (
    RUNNABLE_GRAPH_JOB_STATUSES,
    UNFINISHED_GRAPH_JOB_STATUSES,
)
from .graph_store import GraphApplyResult, GraphStore, graph_store
from .knowledge import KnowledgeRepository, decode_stored_txt
from .models import GraphOrganizingJob, GraphOrganizingTrace, KnowledgeDocument
from .security import SecretCipher


@dataclass(frozen=True)
class GraphJobInputs:
    job_id: UUID
    document: KnowledgeDocument
    api_key: str


async def _load_job_inputs(job_id: UUID) -> GraphJobInputs | None:
    async with SessionFactory() as session:
        job = await session.get(GraphOrganizingJob, job_id)
        if job is None or job.status not in RUNNABLE_GRAPH_JOB_STATUSES:
            return None
        document = await KnowledgeRepository(session).get_document(
            job.document_id,
            include_data=True,
        )
        if document is None:
            raise RuntimeError("待整理的知识库资料不存在")
        credential = await ModelCredentialRepository(session).get(LOCAL_CREDENTIAL_SCOPE)
        if credential is None:
            raise RuntimeError("模型 API 尚未由管理员配置")
        try:
            api_key = SecretCipher.from_environment().decrypt(
                credential.encrypted_api_key,
                scope=credential.scope,
                key_version=credential.encryption_key_version,
            )
        except Exception as exc:
            raise RuntimeError("模型 API 密钥无法解密") from exc
        return GraphJobInputs(job_id=job_id, document=document, api_key=api_key)


async def _job_snapshot(job_id: UUID) -> GraphOrganizingJob | None:
    async with SessionFactory() as session:
        return await session.get(GraphOrganizingJob, job_id)


async def _begin_analysis(job_id: UUID, total_sections: int) -> bool:
    async with SessionFactory() as session:
        job = await session.scalar(
            select(GraphOrganizingJob)
            .where(GraphOrganizingJob.id == job_id)
            .with_for_update()
        )
        if job is None or job.status not in RUNNABLE_GRAPH_JOB_STATUSES:
            return False
        if job.started_at is None:
            job.processed_sections = 0
            job.current_offset = 0
            job.rules_extracted = 0
            job.rules_created = 0
            job.rules_merged = 0
            job.conditions_written = 0
            job.relations_written = 0
            job.conflicts_written = 0
            job.ignored_sections = 0
            job.input_tokens = 0
            job.output_tokens = 0
        job.status = "analyzing"
        job.total_sections = total_sections
        job.failure_message = None
        job.started_at = job.started_at or utc_now()
        job.finished_at = None
        await session.commit()
        return True


async def _pause_at_section_boundary(job_id: UUID) -> bool:
    async with SessionFactory() as session:
        job = await session.scalar(
            select(GraphOrganizingJob)
            .where(GraphOrganizingJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            return True
        if job.status == "pause_requested":
            job.status = "paused"
            await session.commit()
            return True
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.finished_at = utc_now()
            await session.commit()
            return True
        return job.status != "analyzing"


async def _record_section(
    job_id: UUID,
    *,
    processed_sections: int,
    current_offset: int,
    extracted_rules: int,
    ignored: bool,
    input_tokens: int,
    output_tokens: int,
    apply_result: GraphApplyResult,
) -> None:
    async with SessionFactory() as session:
        job = await session.get(GraphOrganizingJob, job_id)
        if job is None:
            return
        job.processed_sections = processed_sections
        job.current_offset = current_offset
        job.rules_extracted += extracted_rules
        job.ignored_sections += int(ignored)
        job.input_tokens += input_tokens
        job.output_tokens += output_tokens
        job.rules_created += apply_result.rules_created
        job.rules_merged += apply_result.rules_merged
        job.conditions_written += apply_result.conditions_written
        job.relations_written += apply_result.relations_written
        job.conflicts_written += apply_result.conflicts_written
        await session.commit()


async def _record_failed_tokens(job_id: UUID, input_tokens: int, output_tokens: int) -> None:
    if input_tokens == 0 and output_tokens == 0:
        return
    async with SessionFactory() as session:
        job = await session.get(GraphOrganizingJob, job_id)
        if job is None:
            return
        job.input_tokens += input_tokens
        job.output_tokens += output_tokens
        await session.commit()


async def _record_trace_attempt(
    job_id: UUID,
    *,
    section,
    status: str,
    rules_extracted: int,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    agent_trace: dict | None,
    error_message: str | None = None,
) -> None:
    async with SessionFactory() as session:
        last_attempt = await session.scalar(
            select(func.max(GraphOrganizingTrace.attempt)).where(
                GraphOrganizingTrace.job_id == job_id,
                GraphOrganizingTrace.section_index == section.index,
            )
        )
        session.add(
            GraphOrganizingTrace(
                job_id=job_id,
                section_index=section.index,
                attempt=(last_attempt or 0) + 1,
                start_offset=section.start,
                end_offset=section.end,
                status=status,
                rules_extracted=rules_extracted,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                agent_trace=agent_trace,
                error_message=error_message[:2000] if error_message else None,
            )
        )
        await session.commit()


async def _mark_applied(job_id: UUID) -> None:
    async with SessionFactory() as session:
        job = await session.scalar(
            select(GraphOrganizingJob)
            .where(GraphOrganizingJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            return
        if job.status == "pause_requested":
            job.status = "paused"
            await session.commit()
            return
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.finished_at = utc_now()
            await session.commit()
            return
        if job.status != "analyzing":
            return
        job.status = "applied"
        job.finished_at = utc_now()
        await session.commit()


async def _mark_failed(job_id: UUID, message: str) -> None:
    async with SessionFactory() as session:
        job = await session.scalar(
            select(GraphOrganizingJob)
            .where(GraphOrganizingJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            return
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.finished_at = utc_now()
            await session.commit()
            return
        job.status = "failed"
        job.failure_message = message[:2000]
        job.finished_at = utc_now()
        await session.commit()


async def _mark_after_task_cancel(job_id: UUID) -> None:
    async with SessionFactory() as session:
        job = await session.get(GraphOrganizingJob, job_id)
        if job is None:
            return
        if job.status == "analyzing":
            job.status = "queued"
        elif job.status == "pause_requested":
            job.status = "paused"
        elif job.status == "cancel_requested":
            job.status = "cancelled"
            job.finished_at = utc_now()
        else:
            return
        await session.commit()


async def _extract_with_retries(
    *,
    job: GraphOrganizingJob,
    context: GraphOrganizerContext,
    api_key: str,
    client: httpx.AsyncClient,
):
    input_tokens = 0
    output_tokens = 0
    last_error: GraphOrganizerModelError | None = None
    for attempt in range(3):
        started = perf_counter()
        try:
            result = await extract_graph_section(
                context=context,
                api_protocol=job.api_protocol,
                model=job.model,
                base_url=job.base_url,
                api_key=api_key,
                client=client,
            )
            await _record_trace_attempt(
                job.id,
                section=context.section,
                status="completed",
                rules_extracted=len(result.extraction.rules),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_ms=round((perf_counter() - started) * 1000),
                agent_trace=result.agent_trace,
            )
            return result, input_tokens, output_tokens
        except GraphOrganizerModelError as exc:
            await _record_trace_attempt(
                job.id,
                section=context.section,
                status="failed",
                rules_extracted=0,
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                duration_ms=round((perf_counter() - started) * 1000),
                agent_trace=exc.agent_trace,
                error_message=str(exc),
            )
            last_error = exc
            input_tokens += exc.input_tokens
            output_tokens += exc.output_tokens
            if exc.fatal or not exc.retryable or attempt == 2:
                break
            await asyncio.sleep(2**attempt)
    assert last_error is not None
    raise GraphOrganizerModelError(
        str(last_error),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        retryable=False,
        fatal=last_error.fatal,
        agent_trace=last_error.agent_trace,
    ) from last_error


async def execute_graph_organizing_job(
    job_id: UUID,
    *,
    store: GraphStore = graph_store,
) -> None:
    try:
        inputs = await _load_job_inputs(job_id)
        if inputs is None:
            return
        job = await _job_snapshot(job_id)
        if job is None:
            return

        text = decode_stored_txt(inputs.document.file_data, inputs.document.encoding)
        sections = split_document_sections(text)
        resume_offset = job.current_offset
        if not await _begin_analysis(job_id, len(sections)):
            return
        if await _pause_at_section_boundary(job_id):
            return
        timeout = httpx.Timeout(None, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for section in sections:
                if section.end <= resume_offset:
                    continue
                context = GraphOrganizerContext(
                    store=store,
                    job_id=str(job_id),
                    document_id=str(inputs.document.id),
                    document_title=inputs.document.title,
                    document_sha256=inputs.document.sha256,
                    section=section,
                )
                try:
                    result, prior_input, prior_output = await _extract_with_retries(
                        job=job,
                        context=context,
                        api_key=inputs.api_key,
                        client=client,
                    )
                except GraphOrganizerModelError as exc:
                    await _record_failed_tokens(
                        job_id,
                        exc.input_tokens,
                        exc.output_tokens,
                    )
                    raise
                await _record_section(
                    job_id,
                    processed_sections=section.index + 1,
                    current_offset=section.end,
                    extracted_rules=len(result.extraction.rules),
                    ignored=not result.extraction.rules,
                    input_tokens=prior_input + result.input_tokens,
                    output_tokens=prior_output + result.output_tokens,
                    apply_result=result.apply_result,
                )
                if await _pause_at_section_boundary(job_id):
                    return
        await _mark_applied(job_id)
    except asyncio.CancelledError:
        await _mark_after_task_cancel(job_id)
        raise
    except Exception as exc:
        await _mark_failed(job_id, f"自动整理失败：{exc}")


class GraphOrganizerTaskManager:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[UUID] = asyncio.Queue()
        self._queued: set[UUID] = set()
        self._worker_task: asyncio.Task[None] | None = None
        self._current_job_id: UUID | None = None

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def start(self) -> None:
        self._ensure_worker()
        async with SessionFactory() as session:
            result = await session.execute(
                select(GraphOrganizingJob)
                .where(GraphOrganizingJob.status.in_(UNFINISHED_GRAPH_JOB_STATUSES))
                .order_by(GraphOrganizingJob.created_at)
            )
            jobs = list(result.scalars())
            for job in jobs:
                if job.status == "analyzing":
                    job.status = "queued"
                elif job.status == "pause_requested":
                    job.status = "paused"
                elif job.status == "cancel_requested":
                    job.status = "cancelled"
                    job.finished_at = utc_now()
            await session.commit()
        for job in jobs:
            if job.status == "queued":
                await self.enqueue(job.id)

    async def enqueue(self, job_id: UUID) -> None:
        self._ensure_worker()
        if job_id in self._queued:
            return
        self._queued.add(job_id)
        await self._queue.put(job_id)

    async def cancel(self, job_id: UUID) -> bool:
        task = self._worker_task
        if self._current_job_id != job_id or task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if self._worker_task is task:
            self._worker_task = None
        self._ensure_worker()
        return True

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        self._current_job_id = None
        self._queue = asyncio.Queue()
        self._queued.clear()

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            self._queued.discard(job_id)
            self._current_job_id = job_id
            try:
                await execute_graph_organizing_job(job_id)
            except Exception as exc:
                await _mark_failed(job_id, f"后台自动整理失败：{exc}")
            finally:
                self._current_job_id = None
                self._queue.task_done()


graph_organizer_task_manager = GraphOrganizerTaskManager()
