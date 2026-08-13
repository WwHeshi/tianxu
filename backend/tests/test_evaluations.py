import json
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
    SYSTEM_PROMPT,
    build_evaluation_prompt,
    chart_for_question,
    chart_tool_input_for_question,
    target_years,
)
from app.evals.mingli_bench.dataset import load_dataset
from app.evals.mingli_bench.model_client import (
    EvaluationAnswer,
    EvaluationModelError,
    EvaluationModelResult,
    request_evaluation_answer,
)
from app.evals.mingli_bench.repository import EvaluationRepository
from app.main import app
from app.models import Base, EvaluationItem, EvaluationRun, ModelCredential


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
    assert dataset.path.name == "data_tianxu.json"
    anchored_question = dataset.get_question("ftb_0012")
    assert "截至2022年" in anchored_question.question
    assert target_years(anchored_question) == (2022,)


def test_tianxu_dataset_has_no_relative_time_wording() -> None:
    dataset = load_dataset()
    relative_markers = (
        "目前",
        "现在",
        "現時",
        "當前",
        "当前",
        "至今",
        "如今",
        "现今",
        "現今",
    )
    model_text = "\n".join(
        text
        for question in dataset.questions
        for text in (question.question, *(option.text for option in question.options))
    )
    assert not any(marker in model_text for marker in relative_markers)


def test_evaluation_prompt_is_natural_text_without_chart_or_label() -> None:
    dataset = load_dataset()
    question = dataset.get_question("ftb_0001")
    prompt, context, prompt_hash = build_evaluation_prompt(question)

    assert target_years(question) == (1996,)
    assert context["birth"]["raw"] == question.birth_info["raw"]
    assert context["birth"]["gender"] == "male"
    assert context["birth"]["true_solar_datetime"] == "1974-04-28T16:40:00"
    assert prompt.startswith("请完成以下天序命理选择题：\n原始出生资料：")
    assert "性别：male\n真太阳出生时间：1974-04-28T16:40:00\n题目：" in prompt
    assert "\n选项：\nA. " in prompt
    assert "benchmark" not in prompt.lower()
    assert "tianxu_chart" not in prompt
    assert '"birth"' not in prompt
    assert dataset.answer_for(question.id) not in {"", None}
    assert "correct_answer" not in prompt
    assert "has_answer" not in prompt
    assert "正确答案" not in prompt
    assert len(prompt_hash) == 64


def test_evaluation_tool_observation_contains_only_natal_chart() -> None:
    dataset = load_dataset()
    question = dataset.get_question("ftb_0001")
    observation = chart_for_question(question).model_dump(mode="json")

    assert "fortune" not in observation
    assert "calculation_policy" not in observation
    assert set(observation) == {"年柱", "月柱", "日柱", "时柱"}
    year_pillar = observation["年柱"]
    assert "name" not in year_pillar
    assert "主星" in year_pillar
    assert "星运" in year_pillar
    assert "自坐" in year_pillar
    assert isinstance(year_pillar["空亡"], list)
    assert "本气五行" in year_pillar["地支"]
    assert "五行" in year_pillar["天干"]
    assert year_pillar["天干"]["阴阳"] in {"阳", "阴"}
    assert "副星" in year_pillar["藏干"][0]


def test_tianxu_evaluation_chart_uses_sect_one_and_lichun_year() -> None:
    dataset = load_dataset()
    expected = {
        "case_9": ("丙午", "戊戌", "辛亥", "戊子"),
        "case_28": ("癸酉", "丙辰", "庚申", "丙子"),
        "case_31": ("戊辰", "甲寅", "庚子", "甲申"),
    }
    for case_id, values in expected.items():
        question = next(item for item in dataset.questions if item.case_id == case_id)
        chart = chart_for_question(question)
        assert (
            chart.year.heavenly_stem.symbol + chart.year.earthly_branch.symbol,
            chart.month.heavenly_stem.symbol + chart.month.earthly_branch.symbol,
            chart.day.heavenly_stem.symbol + chart.day.earthly_branch.symbol,
            chart.hour.heavenly_stem.symbol + chart.hour.earthly_branch.symbol,
        ) == values


@pytest.mark.asyncio
async def test_model_client_requests_strict_answer_json() -> None:
    observed: dict = {}
    observed_headers: dict[str, str] = {}
    question = load_dataset().get_question("ftb_0001")

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
            question=question,
            client=client,
        )

    assert result.answer.answer == "B"
    assert result.input_tokens == 120
    assert observed_headers["authorization"] == "Bearer secret"
    assert set(result.agent_trace) == {
        "initial_request_body",
        "model_calls",
        "tool_executions",
    }
    assert "secret" not in __import__("json").dumps(result.agent_trace)
    assert observed["text"]["format"]["strict"] is True
    assert observed["instructions"] == SYSTEM_PROMPT
    assert observed["tool_choice"] == "auto"
    assert observed["parallel_tool_calls"] is True
    assert observed["tools"][0]["name"] == "calculate_bazi_chart"
    assert observed["text"]["format"]["schema"]["properties"]["answer"]["enum"] == [
        "A",
        "B",
        "C",
        "D",
    ]
    assert "max_output_tokens" not in observed
    assert observed["text"]["format"]["schema"]["properties"]["reasoning_summary"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 120,
    }


@pytest.mark.asyncio
async def test_model_client_runs_action_observation_final_loop() -> None:
    question = load_dataset().get_question("ftb_0001")
    expected_input = chart_tool_input_for_question(question).model_dump(mode="json")
    observed_bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_bodies.append(json.loads(request.content))
        if len(observed_bodies) == 1:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_chart_1",
                            "name": "calculate_bazi_chart",
                            "arguments": json.dumps(expected_input),
                        }
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                },
            )
        return httpx.Response(
            200,
            json={
                "output_text": (
                    '{"answer":"C","confidence":82,'
                    '"reasoning_summary":"命盘与目标年份信息相符"}'
                ),
                "usage": {"input_tokens": 180, "output_tokens": 20},
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
            question=question,
            client=client,
            chart_cache={},
        )

    assert result.answer.answer == "C"
    assert result.input_tokens == 280
    assert result.output_tokens == 30
    assert len(observed_bodies) == 2
    assert len(result.agent_trace["model_calls"]) == 2
    assert [call["stage"] for call in result.agent_trace["model_calls"]] == [
        "action_selection",
        "final_answer",
    ]
    assert "request_body" not in result.agent_trace["model_calls"][0]
    assert "status_code" not in result.agent_trace["model_calls"][0]
    assert len(result.agent_trace["tool_executions"]) == 1
    assert result.agent_trace["tool_executions"][0]["input"] == expected_input
    second_input = observed_bodies[1]["input"]
    assert any(item.get("type") == "function_call" for item in second_input)
    observation = next(
        item for item in second_input if item.get("type") == "function_call_output"
    )
    assert observation["call_id"] == "call_chart_1"
    assert set(json.loads(observation["output"])) == {"年柱", "月柱", "日柱", "时柱"}


@pytest.mark.asyncio
async def test_chat_completion_reports_reasoning_length_exhaustion() -> None:
    observed: dict = {}
    question = load_dataset().get_question("ftb_0001")

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "index": 0,
                        "message": {
                            "content": "",
                            "reasoning_content": "尚未完成的内部推理",
                            "role": "assistant",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 2048},
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
        api_protocol="chat_completions",
        model="reasoning-model",
        base_url="https://example.test/v1",
        prompt_version="test",
        engine_version="test",
        calculation_policy_version="v2",
        total_questions=1,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            EvaluationModelError,
            match="推理耗尽输出长度限制",
        ) as exc_info:
            await request_evaluation_answer(
                run=run,
                api_key="secret",
                user_prompt="question without answer",
                question=question,
                client=client,
            )

    assert "max_tokens" not in observed
    assert observed["messages"][0]["content"] == SYSTEM_PROMPT
    assert observed["tool_choice"] == "auto"
    assert observed["tools"][0]["function"]["name"] == "calculate_bazi_chart"
    assert exc_info.value.input_tokens == 100
    assert exc_info.value.output_tokens == 2048
    assert len(exc_info.value.agent_trace["model_calls"]) == 1


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
        saved_items[0].agent_trace = {
            "initial_request_body": {
                "model": "test-model",
                "instructions": "system prompt",
                "input": [{"role": "user", "content": "answer-free prompt"}],
            },
            "model_calls": [
                {
                    "sequence": 1,
                    "stage": "action_selection",
                    "response_body": {
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_chart",
                                "name": "calculate_bazi_chart",
                                "arguments": "{}",
                            }
                        ]
                    },
                    "duration_ms": 6,
                    "tool_call_count": 1,
                },
                {
                    "sequence": 2,
                    "stage": "final_answer",
                    "response_body": {"output_text": '{"answer":"A"}'},
                    "duration_ms": 7,
                    "tool_call_count": 0,
                },
            ],
            "tool_executions": [
                {
                    "sequence": 1,
                    "name": "calculate_bazi_chart",
                    "input": {
                        "gender": "male",
                        "true_solar_datetime": "1974-04-28T16:40:00",
                    },
                    "output": {"pillars": {}},
                    "duration_ms": 2,
                }
            ],
        }
        await session.commit()
    trace = await client.get(
        f"/api/v1/admin/evaluations/runs/{started.json()['id']}/items/"
        f"{saved_items[0].id}/trace"
    )
    assert trace.status_code == 200
    assert trace.json()["system_prompt"] == "system prompt"
    assert trace.json()["user_prompt"] == "answer-free prompt"
    assert trace.json()["model"] == "test-model"
    assert trace.json()["endpoint"] == "https://example.test/v1/responses"
    assert [call["stage"] for call in trace.json()["model_calls"]] == [
        "action_selection",
        "final_answer",
    ]
    assert len(trace.json()["tool_executions"]) == 1
    assert "steps" not in trace.json()
    second_request = trace.json()["model_calls"][1]["request_body"]
    assert [value["type"] for value in second_request["input"][-2:]] == [
        "function_call",
        "function_call_output",
    ]
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

    active_delete = await client.delete(
        f"/api/v1/admin/evaluations/runs/{started.json()['id']}"
    )
    assert active_delete.status_code == 409
    async with session_factory() as session:
        saved_run = await session.get(EvaluationRun, UUID(started.json()["id"]))
        assert saved_run is not None
        saved_run.status = "completed"
        await session.commit()
    deleted = await client.delete(
        f"/api/v1/admin/evaluations/runs/{started.json()['id']}"
    )
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/v1/admin/evaluations/runs/{started.json()['id']}")
    ).status_code == 404
    async with session_factory() as session:
        assert await session.get(EvaluationRun, UUID(started.json()["id"])) is None
        assert await EvaluationRepository(session).all_items(UUID(started.json()["id"])) == []


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
            prompt_version="mingli-eval-v9-tool-text",
            engine_version="test",
            calculation_policy_version="v2",
            total_questions=1,
        )
        await EvaluationRepository(session).create_run(
            run=run,
            questions=(question,),
            dataset=dataset,
        )
        created_items = await EvaluationRepository(session).all_items(run.id)
        created_item_id = created_items[0].id
        created_items[0].status = "running"
        await session.commit()

    class FakeCipher:
        @classmethod
        def from_environment(cls):
            return cls()

        def decrypt(self, *_args, **_kwargs):
            return "test-key"

    statuses_during_model_call: list[str] = []

    async def fake_model_call(**_kwargs) -> EvaluationModelResult:
        async with session_factory() as session:
            active_item = await session.get(EvaluationItem, created_item_id)
            assert active_item is not None
            statuses_during_model_call.append(active_item.status)
        return EvaluationModelResult(
            answer=EvaluationAnswer(
                answer=dataset.answer_for(question.id),
                confidence=80,
                reasoning_summary="测试依据",
            ),
            agent_trace={
                "initial_request_body": {"model": "test-model"},
                "model_calls": [],
                "tool_executions": [],
            },
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
    assert statuses_during_model_call == ["running"]
    assert items[0].is_correct is True
    assert items[0].predicted_answer == dataset.answer_for(question.id)
    assert items[0].agent_trace == {
        "initial_request_body": {"model": "test-model"},
        "model_calls": [],
        "tool_executions": [],
    }
