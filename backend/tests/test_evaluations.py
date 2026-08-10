from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import evaluation_routes
from app.auth import AuthRepository, hash_password
from app.credentials import LOCAL_CREDENTIAL_SCOPE
from app.database import get_session
from app.evals.mingli_bench import worker
from app.evals.mingli_bench.context import (
    build_evaluation_prompt,
    chart_for_question,
    target_years,
)
from app.evals.mingli_bench.dataset import load_dataset
from app.evals.mingli_bench.model_client import (
    EvaluationAnswer,
    EvaluationModelResult,
    request_evaluation_answer,
)
from app.evals.mingli_bench.repository import EvaluationRepository
from app.main import app
from app.models import Base, EvaluationRun, ModelCredential


@pytest_asyncio.fixture
async def evaluation_client() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, session_factory
    app.dependency_overrides.pop(get_session, None)
    await engine.dispose()


async def _create_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await AuthRepository(session).create_user(
            username="eval-admin",
            display_name="评测管理员",
            password_hash=hash_password("admin-password"),
            role="admin",
            must_change_password=False,
        )


async def _configure_model(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            ModelCredential(
                scope=LOCAL_CREDENTIAL_SCOPE,
                provider="openai",
                api_protocol="responses",
                model="test-model",
                base_url="https://example.test/v1",
                encrypted_api_key="encrypted",
                api_key_last_four="test",
                encryption_key_version="v1",
            )
        )
        await session.commit()


def test_dataset_adapter_separates_questions_from_labels() -> None:
    dataset = load_dataset()

    assert len(dataset.questions) == 160
    assert len(dataset.labels) == 160
    assert len({question.case_id for question in dataset.questions}) == 32
    assert not hasattr(dataset.questions[0], "answer")
    assert [
        len(dataset.select_questions(scope="year", benchmark_year=year))
        for year in (2022, 2023, 2024, 2025)
    ] == [40, 40, 40, 40]
    assert len(dataset.select_questions(scope="quick", benchmark_year=None)) == 5


def test_evaluation_prompt_has_no_label_and_includes_target_fortune() -> None:
    dataset = load_dataset()
    question = dataset.get_question("ftb_0001")
    chart = chart_for_question(question)
    prompt, context, prompt_hash = build_evaluation_prompt(question, chart)

    assert target_years(question) == (1996,)
    assert context["tianxu_chart"]["fortune"]["target_years"]["1996"]
    assert dataset.answer_for(question.id) not in {"", None}
    assert "correct_answer" not in prompt
    assert "has_answer" not in prompt
    assert "正确答案" not in prompt
    assert len(prompt_hash) == 64


def test_tianxu_evaluation_chart_uses_sect_one_and_lichun_year() -> None:
    dataset = load_dataset()
    expected = {
        "case_9": ("丙午", "戊戌", "辛亥", "戊子"),
        "case_28": ("癸酉", "丙辰", "庚申", "丙子"),
        "case_31": ("戊辰", "甲寅", "庚子", "甲申"),
    }
    for case_id, values in expected.items():
        question = next(item for item in dataset.questions if item.case_id == case_id)
        chart = chart_for_question(question).chart.pillars
        assert (
            chart.year.gan_zhi,
            chart.month.gan_zhi,
            chart.day.gan_zhi,
            chart.hour.gan_zhi,
        ) == values


@pytest.mark.asyncio
async def test_model_client_requests_strict_answer_json() -> None:
    observed: dict = {}
    observed_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(__import__("json").loads(request.content))
        observed_headers.update(request.headers)
        return httpx.Response(
            200,
            json={
                "output_text": '{"answer":"B","confidence":73,"reasoning_summary":"简要依据"}',
                "usage": {"input_tokens": 120, "output_tokens": 20},
            },
        )

    run = EvaluationRun(
        dataset_name="test",
        dataset_sha256="0" * 64,
        dataset_question_count=1,
        scope="quick",
        mode="tianxu_fortune",
        max_concurrency=1,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://example.test/v1",
        prompt_version="test",
        engine_version="test",
        calculation_policy_version="v2",
        total_questions=1,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await request_evaluation_answer(
            run=run,
            api_key="secret",
            user_prompt="question without answer",
            client=client,
        )

    assert result.answer.answer == "B"
    assert result.input_tokens == 120
    assert result.response_status_code == 200
    assert observed_headers["authorization"] == "Bearer secret"
    assert result.request_snapshot["headers"]["Authorization"] == "Bearer [REDACTED]"
    assert "secret" not in __import__("json").dumps(result.request_snapshot)
    assert observed["text"]["format"]["strict"] is True
    assert observed["text"]["format"]["schema"]["properties"]["answer"]["enum"] == [
        "A",
        "B",
        "C",
        "D",
    ]


@pytest.mark.asyncio
async def test_admin_can_create_quick_run_without_starting_real_model(
    evaluation_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory = evaluation_client
    unauthorized = await client.get("/api/v1/admin/evaluations/overview")
    assert unauthorized.status_code == 401
    await _create_admin(session_factory)
    await _configure_model(session_factory)
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "eval-admin", "password": "admin-password"},
    )
    assert login.status_code == 200
    queued: list = []

    async def fake_enqueue(run_id) -> None:
        queued.append(run_id)

    monkeypatch.setattr(evaluation_routes.evaluation_task_manager, "enqueue", fake_enqueue)
    overview = await client.get("/api/v1/admin/evaluations/overview")
    wrong_confirmation = await client.post(
        "/api/v1/admin/evaluations/runs",
        json={
            "scope": "quick",
            "benchmark_year": None,
            "mode": "tianxu_fortune",
            "max_concurrency": 2,
            "confirmed_request_count": 4,
        },
    )
    started = await client.post(
        "/api/v1/admin/evaluations/runs",
        json={
            "scope": "quick",
            "benchmark_year": None,
            "mode": "tianxu_fortune",
            "max_concurrency": 2,
            "confirmed_request_count": 5,
        },
    )

    assert overview.status_code == 200
    assert overview.json()["dataset"]["question_count"] == 160
    assert wrong_confirmation.status_code == 409
    assert started.status_code == 201
    assert started.json()["total_questions"] == 5
    assert started.json()["status"] == "queued"
    assert [str(run_id) for run_id in queued] == [started.json()["id"]]
    items = await client.get(
        f"/api/v1/admin/evaluations/runs/{started.json()['id']}/items"
    )
    assert items.status_code == 200
    assert items.json()["total"] == 5
    assert all(item["status"] == "pending" for item in items.json()["items"])
    async with session_factory() as session:
        saved_items = await EvaluationRepository(session).all_items(UUID(started.json()["id"]))
        saved_items[0].status = "completed"
        saved_items[0].predicted_answer = saved_items[0].correct_answer
        saved_items[0].is_correct = True
        saved_items[0].prompt_sha256 = "a" * 64
        saved_items[0].request_snapshot = {
            "method": "POST",
            "endpoint": "https://example.test/v1/responses",
            "provider": "openai",
            "api_protocol": "responses",
            "model": "test-model",
            "headers": {
                "Authorization": "Bearer [REDACTED]",
                "Content-Type": "application/json",
            },
            "body": {"model": "test-model", "input": "answer-free prompt"},
        }
        saved_items[0].response_status_code = 200
        saved_items[0].raw_response = {"id": "response-test"}
        await session.commit()
    trace = await client.get(
        f"/api/v1/admin/evaluations/runs/{started.json()['id']}/items/"
        f"{saved_items[0].id}/trace"
    )
    assert trace.status_code == 200
    assert trace.json()["request"]["headers"]["Authorization"] == "Bearer [REDACTED]"
    assert trace.json()["response"] == {
        "status_code": 200,
        "body": {"id": "response-test"},
    }
    exported_json = await client.get(
        f"/api/v1/admin/evaluations/runs/{started.json()['id']}/export?format=json"
    )
    exported_csv = await client.get(
        f"/api/v1/admin/evaluations/runs/{started.json()['id']}/export?format=csv"
    )
    assert exported_json.status_code == 200
    assert len(exported_json.json()["items"]) == 5
    assert exported_csv.status_code == 200
    assert exported_csv.content.startswith(b"\xef\xbb\xbfquestion_id,")


@pytest.mark.asyncio
async def test_worker_persists_score_and_progress(
    evaluation_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_factory = evaluation_client
    await _configure_model(session_factory)
    dataset = load_dataset()
    question = dataset.get_question("ftb_0001")
    async with session_factory() as session:
        run = EvaluationRun(
            dataset_name="test",
            dataset_sha256=dataset.sha256,
            dataset_question_count=160,
            scope="quick",
            mode="tianxu_fortune",
            max_concurrency=1,
            provider="openai",
            api_protocol="responses",
            model="test-model",
            base_url="https://example.test/v1",
            prompt_version="mingli-eval-v1",
            engine_version="test",
            calculation_policy_version="v2",
            total_questions=1,
        )
        await EvaluationRepository(session).create_run(
            run=run,
            questions=(question,),
            dataset=dataset,
        )

    class FakeCipher:
        @classmethod
        def from_environment(cls):
            return cls()

        def decrypt(self, *_args, **_kwargs):
            return "test-key"

    async def fake_model_call(**_kwargs) -> EvaluationModelResult:
        return EvaluationModelResult(
            answer=EvaluationAnswer(
                answer=dataset.answer_for(question.id),
                confidence=80,
                reasoning_summary="测试依据",
            ),
            request_snapshot={
                "method": "POST",
                "endpoint": "https://example.test/v1/responses",
                "provider": "openai",
                "api_protocol": "responses",
                "model": "test-model",
                "headers": {"Authorization": "Bearer [REDACTED]"},
                "body": {"model": "test-model"},
            },
            response_status_code=200,
            raw_response={"id": "response-test"},
            latency_ms=12,
            input_tokens=100,
            output_tokens=10,
        )

    monkeypatch.setattr(worker, "SessionFactory", session_factory)
    monkeypatch.setattr(worker, "SecretCipher", FakeCipher)
    monkeypatch.setattr(worker, "request_evaluation_answer", fake_model_call)
    await worker.execute_evaluation_run(run.id)

    async with session_factory() as session:
        saved_run = await session.get(EvaluationRun, run.id)
        items = await EvaluationRepository(session).all_items(run.id)
    assert saved_run is not None
    assert saved_run.status == "completed"
    assert saved_run.completed_questions == 1
    assert saved_run.correct_answers == 1
    assert saved_run.input_tokens == 100
    assert items[0].is_correct is True
    assert items[0].predicted_answer == dataset.answer_for(question.id)
    assert items[0].request_snapshot["headers"]["Authorization"] == "Bearer [REDACTED]"
    assert items[0].response_status_code == 200
    assert items[0].raw_response == {"id": "response-test"}
