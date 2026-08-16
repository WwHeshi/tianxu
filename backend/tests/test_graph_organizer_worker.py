import asyncio
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.graph_organizer import (
    DocumentSection,
    ExtractedGraphRule,
    GraphExtractionOutput,
    GraphOrganizerModelError,
    GraphSectionResult,
)
from app.graph_organizer_worker import GraphOrganizerTaskManager, execute_graph_organizing_job
from app.graph_store import GraphApplyResult
from app.models import (
    GraphOrganizingJob,
    GraphOrganizingTrace,
    KnowledgeDocument,
    ModelCredential,
)


class FakeCipher:
    @classmethod
    def from_environment(cls):
        return cls()

    def decrypt(self, *_args, **_kwargs) -> str:
        return "test-api-key"


class FakeGraphStore:
    def __init__(self) -> None:
        self.apply_calls = []

    async def list_rule_summaries(self):
        return ()

    async def apply_rules(self, **kwargs):
        self.apply_calls.append(kwargs)
        return GraphApplyResult(
            rules_created=len(kwargs["rules"]),
            rules_merged=0,
            conditions_written=1,
            relations_written=3,
            conflicts_written=0,
        )


async def create_job(session_factory, text: str) -> GraphOrganizingJob:
    data = text.encode("utf-8")
    async with session_factory() as session:
        document = KnowledgeDocument(
            title="测试资料",
            original_filename="test.txt",
            encoding="utf-8",
            byte_size=len(data),
            sha256=sha256(data).hexdigest(),
            file_data=data,
        )
        session.add(document)
        await session.flush()
        session.add(
            ModelCredential(
                scope="local-default",
                user_id=None,
                provider="openai",
                api_protocol="responses",
                model="test-model",
                base_url="https://example.test/v1",
                encrypted_api_key="encrypted",
                api_key_last_four="test",
                encryption_key_version="v1",
            )
        )
        job = GraphOrganizingJob(
            document_id=document.id,
            document_title=document.title,
            created_by_user_id=None,
            provider="openai",
            api_protocol="responses",
            model="test-model",
            base_url="https://example.test/v1",
            prompt_version="test",
            status="queued",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


def one_rule() -> ExtractedGraphRule:
    return ExtractedGraphRule(
        name="身旺任财",
        summary="身旺时较能任财。",
        aliases=[],
        concepts=["身旺", "财星"],
        condition_groups=[{"all_of": ["身旺"], "none_of": []}],
        strengthened_by=[],
        weakened_by=[],
        outcomes=["任财"],
        does_not_prove=[],
        existing_rule_id="",
        rule_links=[],
    )


@pytest.mark.asyncio
async def test_worker_records_each_section_after_submit_has_written_it(
    database_client,
    monkeypatch,
) -> None:
    _, session_factory = database_client
    text = "古法云：身旺方能任财。"
    job = await create_job(session_factory, text)
    store = FakeGraphStore()

    async def fake_extract_graph_section(**kwargs):
        assert kwargs["context"].section.text == text
        assert kwargs["context"].store is store
        assert kwargs["api_key"] == "test-api-key"
        return GraphSectionResult(
            extraction=GraphExtractionOutput(rules=[one_rule()]),
            apply_result=GraphApplyResult(
                rules_created=1,
                rules_merged=0,
                conditions_written=1,
                relations_written=3,
                conflicts_written=0,
            ),
            input_tokens=30,
            output_tokens=12,
        )

    monkeypatch.setattr("app.graph_organizer_worker.SessionFactory", session_factory)
    monkeypatch.setattr("app.graph_organizer_worker.SecretCipher", FakeCipher)
    monkeypatch.setattr(
        "app.graph_organizer_worker.extract_graph_section",
        fake_extract_graph_section,
    )

    await execute_graph_organizing_job(job.id, store=store)

    async with session_factory() as session:
        saved = await session.get(GraphOrganizingJob, job.id)
        assert saved is not None
        assert saved.status == "applied"
        assert saved.total_sections == 1
        assert saved.processed_sections == 1
        assert saved.rules_extracted == 1
        assert saved.rules_created == 1
        assert saved.relations_written == 3
        assert saved.input_tokens == 30
        assert saved.output_tokens == 12
        assert saved.failure_message is None
        traces = list(
            (
                await session.scalars(
                    select(GraphOrganizingTrace).where(GraphOrganizingTrace.job_id == job.id)
                )
            ).all()
        )
        assert len(traces) == 1
        assert traces[0].status == "completed"
        assert traces[0].section_index == 0
        assert traces[0].rules_extracted == 1
    assert store.apply_calls == []


@pytest.mark.asyncio
async def test_worker_marks_failure_when_first_section_submit_fails(
    database_client,
    monkeypatch,
) -> None:
    _, session_factory = database_client
    job = await create_job(session_factory, "这是一段资料。")
    store = FakeGraphStore()

    async def failing_extract(**_kwargs):
        raise GraphOrganizerModelError(
            "输出结构错误",
            input_tokens=4,
            output_tokens=2,
            retryable=False,
        )

    monkeypatch.setattr("app.graph_organizer_worker.SessionFactory", session_factory)
    monkeypatch.setattr("app.graph_organizer_worker.SecretCipher", FakeCipher)
    monkeypatch.setattr("app.graph_organizer_worker.extract_graph_section", failing_extract)

    await execute_graph_organizing_job(job.id, store=store)

    async with session_factory() as session:
        saved = await session.get(GraphOrganizingJob, job.id)
        assert saved is not None
        assert saved.status == "failed"
        assert saved.processed_sections == 0
        assert saved.input_tokens == 4
        assert saved.output_tokens == 2
        assert "输出结构错误" in (saved.failure_message or "")
        traces = list(
            (
                await session.scalars(
                    select(GraphOrganizingTrace).where(GraphOrganizingTrace.job_id == job.id)
                )
            ).all()
        )
        assert len(traces) == 1
        assert traces[0].status == "failed"
        assert traces[0].error_message == "输出结构错误"
    assert store.apply_calls == []


@pytest.mark.asyncio
async def test_worker_keeps_completed_section_when_later_submit_fails(
    database_client,
    monkeypatch,
) -> None:
    _, session_factory = database_client
    job = await create_job(session_factory, "第一段。第二段。")
    store = FakeGraphStore()
    sections = (
        DocumentSection(index=0, start=0, end=4, text="第一段。"),
        DocumentSection(index=1, start=4, end=8, text="第二段。"),
    )

    async def partial_extract(**kwargs):
        if kwargs["context"].section.index == 1:
            raise GraphOrganizerModelError(
                "第二段失败",
                input_tokens=4,
                output_tokens=2,
                retryable=False,
            )
        return GraphSectionResult(
            extraction=GraphExtractionOutput(rules=[one_rule()]),
            apply_result=GraphApplyResult(1, 0, 1, 3, 0),
            input_tokens=10,
            output_tokens=5,
        )

    monkeypatch.setattr("app.graph_organizer_worker.SessionFactory", session_factory)
    monkeypatch.setattr("app.graph_organizer_worker.SecretCipher", FakeCipher)
    monkeypatch.setattr(
        "app.graph_organizer_worker.split_document_sections",
        lambda _text: sections,
    )
    monkeypatch.setattr(
        "app.graph_organizer_worker.extract_graph_section",
        partial_extract,
    )

    await execute_graph_organizing_job(job.id, store=store)

    async with session_factory() as session:
        saved = await session.get(GraphOrganizingJob, job.id)
        assert saved is not None
        assert saved.status == "failed"
        assert saved.processed_sections == 1
        assert saved.current_offset == 4
        assert saved.rules_created == 1
        assert saved.relations_written == 3
        assert saved.input_tokens == 14
        assert saved.output_tokens == 7
        traces = list(
            (
                await session.scalars(
                    select(GraphOrganizingTrace).where(GraphOrganizingTrace.job_id == job.id)
                )
            ).all()
        )
        assert [trace.status for trace in traces] == ["completed", "failed"]


@pytest.mark.asyncio
async def test_worker_pauses_at_section_boundary_and_resumes_from_saved_offset(
    database_client,
    monkeypatch,
) -> None:
    _, session_factory = database_client
    job = await create_job(session_factory, "第一段。第二段。")
    store = FakeGraphStore()
    sections = (
        DocumentSection(index=0, start=0, end=4, text="第一段。"),
        DocumentSection(index=1, start=4, end=8, text="第二段。"),
    )
    extracted_sections = []

    async def pausing_extract(**kwargs):
        section = kwargs["context"].section
        extracted_sections.append(section.index)
        if section.index == 0:
            async with session_factory() as session:
                stored = await session.get(GraphOrganizingJob, job.id)
                assert stored is not None
                stored.status = "pause_requested"
                await session.commit()
        return GraphSectionResult(
            extraction=GraphExtractionOutput(rules=[one_rule()]),
            apply_result=GraphApplyResult(1, 0, 1, 3, 0),
            input_tokens=10,
            output_tokens=5,
        )

    monkeypatch.setattr("app.graph_organizer_worker.SessionFactory", session_factory)
    monkeypatch.setattr("app.graph_organizer_worker.SecretCipher", FakeCipher)
    monkeypatch.setattr(
        "app.graph_organizer_worker.split_document_sections",
        lambda _text: sections,
    )
    monkeypatch.setattr(
        "app.graph_organizer_worker.extract_graph_section",
        pausing_extract,
    )

    await execute_graph_organizing_job(job.id, store=store)

    async with session_factory() as session:
        paused = await session.get(GraphOrganizingJob, job.id)
        assert paused is not None
        assert paused.status == "paused"
        assert paused.processed_sections == 1
        assert paused.current_offset == 4
        paused.status = "queued"
        await session.commit()

    await execute_graph_organizing_job(job.id, store=store)

    async with session_factory() as session:
        applied = await session.get(GraphOrganizingJob, job.id)
        assert applied is not None
        assert applied.status == "applied"
        assert applied.processed_sections == 2
        assert applied.current_offset == 8
        assert applied.rules_created == 2
    assert extracted_sections == [0, 1]


@pytest.mark.asyncio
async def test_worker_cancellation_marks_requested_job_cancelled(
    database_client,
    monkeypatch,
) -> None:
    _, session_factory = database_client
    job = await create_job(session_factory, "正在分析的资料。")
    started = asyncio.Event()

    async def blocking_extract(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.graph_organizer_worker.SessionFactory", session_factory)
    monkeypatch.setattr("app.graph_organizer_worker.SecretCipher", FakeCipher)
    monkeypatch.setattr("app.graph_organizer_worker.extract_graph_section", blocking_extract)

    task = asyncio.create_task(execute_graph_organizing_job(job.id, store=FakeGraphStore()))
    await asyncio.wait_for(started.wait(), timeout=1)
    async with session_factory() as session:
        stored = await session.get(GraphOrganizingJob, job.id)
        assert stored is not None
        stored.status = "cancel_requested"
        await session.commit()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with session_factory() as session:
        cancelled = await session.get(GraphOrganizingJob, job.id)
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert cancelled.finished_at is not None
        assert cancelled.processed_sections == 0


@pytest.mark.asyncio
async def test_task_manager_cancel_interrupts_current_job_and_restarts_worker(
    monkeypatch,
) -> None:
    manager = GraphOrganizerTaskManager()
    job_id = uuid4()
    started = asyncio.Event()
    interrupted = asyncio.Event()

    async def blocking_job(_job_id):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.set()

    monkeypatch.setattr("app.graph_organizer_worker.execute_graph_organizing_job", blocking_job)
    try:
        await manager.enqueue(job_id)
        await asyncio.wait_for(started.wait(), timeout=1)
        assert await manager.cancel(job_id) is True
        await asyncio.wait_for(interrupted.wait(), timeout=1)
        assert manager._worker_task is not None
        assert not manager._worker_task.done()
    finally:
        await manager.stop()
