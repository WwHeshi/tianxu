from hashlib import sha256

import pytest
from sqlalchemy import select

from app.graph_organizer import (
    DocumentSection,
    ExtractedGraphRule,
    GraphExtractionOutput,
    GraphOrganizerModelError,
    GraphSectionResult,
)
from app.graph_organizer_worker import execute_graph_organizing_job
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


def one_rule(excerpt: str) -> ExtractedGraphRule:
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
        source_excerpt=excerpt,
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
            extraction=GraphExtractionOutput(rules=[one_rule("身旺方能任财")]),
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
            extraction=GraphExtractionOutput(rules=[one_rule("第一段")]),
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
