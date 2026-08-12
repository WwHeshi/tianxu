import json
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import routes
from app.auth import get_current_user
from app.bazi.engine import calculate_chart
from app.credentials import LOCAL_CREDENTIAL_SCOPE, get_credential_repository
from app.main import app
from app.models import ModelCredential, User
from app.reports import (
    MAX_REACT_MODEL_CALLS,
    ModelOutputFormatError,
    ModelProviderError,
    ReportGenerationResult,
    ReportModelCall,
    ReportToolExecution,
    generate_structured_report,
)
from app.reports import (
    test_model_connection as probe_model_connection,
)
from app.schemas import BaziReport, BirthInput
from app.security import SecretCipher, SecretEncryptionError

MASTER_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def valid_payload() -> dict[str, object]:
    return {
        "beijing_datetime": "1990-01-01T12:00:00",
        "birthplace": {"location_id": "CN:440106"},
        "gender": "male",
    }


def sample_report() -> BaziReport:
    return BaziReport(
        chart_overview="命盘概览内容",
        temperament="性情特点内容",
        career="能力与事业内容",
        finance="财务倾向内容",
        relationships="关系模式内容",
        current_fortune="当前运势内容",
        recommendations="综合建议内容",
        limitations="传统文化视角，仅供参考。",
    )


def authenticated_user(role: str = "admin") -> User:
    return User(
        id=uuid4(),
        username=f"report-test-{role}",
        display_name=f"Report Test {role}",
        password_hash="unused",
        role=role,
        status="active",
        must_change_password=False,
    )


class FakeCredentialRepository:
    def __init__(self, credential: ModelCredential | None = None) -> None:
        self.credential = credential

    async def get(self, scope: str = LOCAL_CREDENTIAL_SCOPE) -> ModelCredential | None:
        assert scope == LOCAL_CREDENTIAL_SCOPE
        return self.credential

    async def upsert(self, **values: str) -> ModelCredential:
        self.credential = ModelCredential(
            id=1,
            scope=LOCAL_CREDENTIAL_SCOPE,
            user_id=None,
            **values,
        )
        return self.credential

    async def delete(self, scope: str = LOCAL_CREDENTIAL_SCOPE) -> bool:
        assert scope == LOCAL_CREDENTIAL_SCOPE
        existed = self.credential is not None
        self.credential = None
        return existed


@pytest_asyncio.fixture
async def api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, FakeCredentialRepository]]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", MASTER_KEY)
    repository = FakeCredentialRepository()
    app.dependency_overrides[get_credential_repository] = lambda: repository
    test_admin = authenticated_user()
    app.dependency_overrides[get_current_user] = lambda: test_admin
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repository
    app.dependency_overrides.pop(get_credential_repository, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_model_settings_store_only_ciphertext_and_return_mask(
    api_client: tuple[AsyncClient, FakeCredentialRepository],
) -> None:
    client, repository = api_client
    plaintext = "sk-test-super-secret-6789"

    response = await client.put(
        "/api/v1/model-settings",
        json={
            "provider": "openai",
            "api_protocol": "responses",
            "model": "test-model",
            "base_url": "https://api.openai.com/v1",
            "api_key": plaintext,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "provider": "openai",
        "api_protocol": "responses",
        "model": "test-model",
        "base_url": "https://api.openai.com/v1",
        "api_key_masked": "••••6789",
    }
    assert repository.credential is not None
    assert plaintext not in repository.credential.encrypted_api_key
    decrypted = SecretCipher.from_environment().decrypt(
        repository.credential.encrypted_api_key,
        scope=repository.credential.scope,
        key_version=repository.credential.encryption_key_version,
    )
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_report_requires_model_settings(
    api_client: tuple[AsyncClient, FakeCredentialRepository],
) -> None:
    client, _ = api_client

    response = await client.post("/api/v1/reports/generate", json=valid_payload())

    assert response.status_code == 409
    assert "管理员配置" in response.json()["detail"]


@pytest.mark.asyncio
async def test_connection_uses_current_form_without_saving(
    api_client: tuple[AsyncClient, FakeCredentialRepository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repository = api_client
    captured: dict[str, str] = {}

    async def fake_test(**values: str) -> None:
        captured.update(values)

    monkeypatch.setattr(routes, "test_model_connection", fake_test)

    response = await client.post(
        "/api/v1/model-settings/test",
        json={
            "provider": "openai",
            "api_protocol": "chat_completions",
            "model": "test-model",
            "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            "api_key": "sk-current-form-1234",
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "连接成功，API 密钥和模型均可用。"
    assert captured == {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "test-model",
        "api_key": "sk-current-form-1234",
        "api_protocol": "chat_completions",
    }
    assert repository.credential is None


@pytest.mark.asyncio
async def test_report_recalculates_chart_server_side_and_returns_metadata(
    api_client: tuple[AsyncClient, FakeCredentialRepository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repository = api_client
    encrypted = SecretCipher.from_environment().encrypt(
        "sk-test-super-secret-6789",
        scope=LOCAL_CREDENTIAL_SCOPE,
        key_version="v1",
    )
    repository.credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key=encrypted,
        api_key_last_four="6789",
        encryption_key_version="v1",
    )
    captured: dict[str, object] = {}

    async def fake_generate(**values: object) -> ReportGenerationResult:
        captured.update(values)
        return ReportGenerationResult(
            report=sample_report(),
            context={"context_version": "test"},
            system_prompt="system prompt",
            user_prompt="user prompt",
            endpoint="https://api.openai.com/v1/responses",
            request_body={"model": "test-model", "input": "user prompt"},
            raw_response={"output_text": sample_report().model_dump_json()},
            model_latency_ms=12,
            model_calls=(
                ReportModelCall(
                    stage="action_selection",
                    request_body={"input": "user prompt", "tools": [{}]},
                    raw_response={"output": [{"type": "function_call"}]},
                    latency_ms=5,
                ),
                ReportModelCall(
                    stage="final_answer",
                    request_body={"input": [{"type": "function_call_output"}]},
                    raw_response={"output_text": sample_report().model_dump_json()},
                    latency_ms=7,
                ),
            ),
            tool_executions=(
                ReportToolExecution(
                    name="calculate_bazi_chart",
                    input={
                        "gender": "male",
                        "true_solar_datetime": "1990-01-01T11:30:33",
                    },
                    output={"pillars": {"day": {"gan_zhi": "丙寅"}}},
                    duration_ms=1,
                ),
            ),
        )

    monkeypatch.setattr(routes, "generate_structured_report", fake_generate)

    response = await client.post("/api/v1/reports/generate", json=valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["model"] == "test-model"
    assert data["metadata"]["api_protocol"] == "responses"
    assert data["metadata"]["prompt_version"] == "bazi-report-v15-react-text"
    assert data["chart"]["chart"]["pillars"]["day"]["gan_zhi"] == "丙寅"
    assert [step["id"] for step in data["debug_trace"]["steps"]] == [
        "normalize",
        "prompt",
        "action_1",
        "tool_1",
        "observation_1",
        "final_2",
        "validation",
    ]
    assert data["debug_trace"]["request"]["request_count"] == 2
    assert data["debug_trace"]["tool_executions"][0]["name"] == "calculate_bazi_chart"
    assert data["debug_trace"]["system_prompt"] == "system prompt"
    assert "sk-test-super-secret-6789" not in response.text
    assert captured["api_key"] == "sk-test-super-secret-6789"

    app.dependency_overrides[get_current_user] = lambda: authenticated_user("user")
    user_response = await client.post("/api/v1/reports/generate", json=valid_payload())
    assert user_response.status_code == 200
    assert user_response.json()["debug_trace"] is None


@pytest.mark.asyncio
async def test_report_format_error_returns_failed_debug_trace(
    api_client: tuple[AsyncClient, FakeCredentialRepository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repository = api_client
    encrypted = SecretCipher.from_environment().encrypt(
        "sk-test-super-secret-6789",
        scope=LOCAL_CREDENTIAL_SCOPE,
        key_version="v1",
    )
    repository.credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key=encrypted,
        api_key_last_four="6789",
        encryption_key_version="v1",
    )

    async def fake_generate(**_: object) -> ReportGenerationResult:
        raise ModelOutputFormatError(
            "模型返回的报告结构不符合约定，请重试。",
            system_prompt="system prompt",
            user_prompt="user prompt",
            endpoint="https://api.openai.com/v1/responses",
            request_body={"model": "test-model", "input": "user prompt"},
            raw_response={"output": [{"text": "invalid report"}]},
            model_latency_ms=17,
        )

    monkeypatch.setattr(routes, "generate_structured_report", fake_generate)

    response = await client.post("/api/v1/reports/generate", json=valid_payload())

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["message"] == "模型返回的报告结构不符合约定，请重试。"
    trace = detail["debug_trace"]
    assert trace["steps"][-1]["id"] == "validation"
    assert trace["steps"][-1]["status"] == "failed"
    assert trace["raw_response"] == {"output": [{"text": "invalid report"}]}
    assert "sk-test-super-secret-6789" not in response.text

    app.dependency_overrides[get_current_user] = lambda: authenticated_user("user")
    user_response = await client.post("/api/v1/reports/generate", json=valid_payload())
    assert user_response.status_code == 502
    assert user_response.json()["detail"] == "模型返回的报告结构不符合约定，请重试。"
    assert "debug_trace" not in user_response.text


def test_agent_debug_trace_uses_actual_react_response_count() -> None:
    credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="unused",
        api_key_last_four="6789",
        encryption_key_version="v1",
    )
    execution = ReportGenerationResult(
        report=sample_report(),
        context={"pillars": {}},
        system_prompt="system",
        user_prompt="user",
        endpoint="https://api.openai.com/v1/responses",
        request_body={"round": 3},
        raw_response={"output_text": sample_report().model_dump_json()},
        model_latency_ms=18,
        model_calls=(
            ReportModelCall("action_selection", {"round": 1}, {"action": 1}, 5),
            ReportModelCall("action_selection", {"round": 2}, {"action": 2}, 6),
            ReportModelCall("final_answer", {"round": 3}, {"final": True}, 7),
        ),
        tool_executions=(
            ReportToolExecution("calculate_bazi_chart", {"round": 1}, {"chart": 1}, 1),
            ReportToolExecution("calculate_bazi_chart", {"round": 2}, {"chart": 2}, 1),
        ),
    )

    trace = routes._report_debug_trace(
        execution=execution,
        credential=credential,
        normalization_duration_ms=2,
    )

    assert trace.request.request_count == 3
    assert len(trace.model_calls) == 3
    assert len(trace.tool_executions) == 2
    assert [step.id for step in trace.steps] == [
        "normalize",
        "prompt",
        "action_1",
        "tool_1",
        "observation_1",
        "action_2",
        "tool_2",
        "observation_2",
        "final_3",
        "validation",
    ]


@pytest.mark.asyncio
async def test_responses_report_uses_react_tool_loop() -> None:
    chart = calculate_chart(BirthInput.model_validate(valid_payload()))
    credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="unused",
        api_key_last_four="6789",
        encryption_key_version="v1",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_chart_1",
                            "name": "calculate_bazi_chart",
                            "arguments": json.dumps(
                                {
                                    "gender": "male",
                                    "true_solar_datetime": "1990-01-01T11:30:33",
                                }
                            ),
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": sample_report().model_dump_json()}
                        ],
                    }
                ]
            },
        )

    execution = await generate_structured_report(
        chart=chart,
        credential=credential,
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    assert execution.report.chart_overview == "命盘概览内容"
    assert "output" in execution.raw_response
    assert "normalized_input" not in execution.context
    assert "calendar" in execution.context
    assert "element_distribution" in execution.context
    assert "calculation_policy" not in execution.context
    assert "engine" not in execution.context
    assert "limitations" not in execution.context
    assert "day_master" in execution.context
    assert "chart_reliability_warnings" not in execution.context
    assert len(requests) == 2
    first_body = json.loads(requests[0].content)
    second_body = json.loads(requests[1].content)
    assert first_body["tool_choice"] == "auto"
    assert first_body["tools"][0]["name"] == "calculate_bazi_chart"
    assert "pillars" not in first_body["input"][0]["content"]
    user_content = first_body["input"][0]["content"]
    assert "性别：male" in user_content
    assert "真太阳出生时间：1990-01-01T11:30:33" in user_content
    assert "当前大运：" in user_content
    assert "当前流年：" in user_content
    assert "当前流月：" in user_content
    assert "{" not in user_content
    assert "}" not in user_content
    assert "calculate_bazi_chart" not in user_content
    assert "ReAct" not in user_content
    assert "Observation" not in user_content
    assert second_body["tool_choice"] == "auto"
    assert second_body["text"]["format"]["type"] == "json_schema"
    assert second_body["input"][-1]["type"] == "function_call_output"
    assert "big_luck_periods" not in second_body["input"][-1]["output"]
    assert "current_fortune" not in second_body["input"][-1]["output"]
    assert len(execution.model_calls) == 2
    assert execution.tool_executions[0].input == {
        "gender": "male",
        "true_solar_datetime": "1990-01-01T11:30:33",
    }


@pytest.mark.asyncio
async def test_react_agent_accepts_direct_final_without_tool_call() -> None:
    chart = calculate_chart(BirthInput.model_validate(valid_payload()))
    credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="unused",
        api_key_last_four="6789",
        encryption_key_version="v1",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": sample_report().model_dump_json()}
                        ],
                    }
                ]
            },
        )

    execution = await generate_structured_report(
        chart=chart,
        credential=credential,
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    assert len(requests) == 1
    assert [call.stage for call in execution.model_calls] == ["final_answer"]
    assert execution.tool_executions == ()
    assert execution.context == {"birth": {
        "gender": "male",
        "true_solar_datetime": "1990-01-01T11:30:33",
    }}
    request_body = json.loads(requests[0].content)
    assert request_body["tool_choice"] == "auto"
    assert "标准化出生资料" in request_body["input"][0]["content"]
    assert "当前大运：" in request_body["input"][0]["content"]
    assert "当前流年：" in request_body["input"][0]["content"]
    assert "当前流月：" in request_body["input"][0]["content"]
    assert "calculate_bazi_chart" not in request_body["input"][0]["content"]
    assert "{" not in request_body["input"][0]["content"]


@pytest.mark.asyncio
async def test_react_loop_tracks_each_model_response_dynamically() -> None:
    chart = calculate_chart(BirthInput.model_validate(valid_payload()))
    credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="unused",
        api_key_last_four="6789",
        encryption_key_version="v1",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) <= 2:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": f"call_chart_{len(requests)}",
                            "name": "calculate_bazi_chart",
                            "arguments": json.dumps(
                                {
                                    "gender": "male",
                                    "true_solar_datetime": "1990-01-01T11:30:33",
                                }
                            ),
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": sample_report().model_dump_json()}
                        ],
                    }
                ]
            },
        )

    execution = await generate_structured_report(
        chart=chart,
        credential=credential,
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    assert len(requests) == 3
    assert [call.stage for call in execution.model_calls] == [
        "action_selection",
        "action_selection",
        "final_answer",
    ]
    assert len(execution.tool_executions) == 2
    third_body = json.loads(requests[2].content)
    assert sum(item.get("type") == "function_call_output" for item in third_body["input"]) == 2


@pytest.mark.asyncio
async def test_react_loop_stops_after_safety_limit() -> None:
    chart = calculate_chart(BirthInput.model_validate(valid_payload()))
    credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="unused",
        api_key_last_four="6789",
        encryption_key_version="v1",
    )
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"call_chart_loop_{request_count}",
                        "name": "calculate_bazi_chart",
                        "arguments": json.dumps(
                            {
                                "gender": "male",
                                "true_solar_datetime": "1990-01-01T11:30:33",
                            }
                        ),
                    }
                ]
            },
        )

    with pytest.raises(ModelOutputFormatError, match="安全终止") as captured:
        await generate_structured_report(
            chart=chart,
            credential=credential,
            api_key="sk-test",
            transport=httpx.MockTransport(handler),
        )

    assert request_count == MAX_REACT_MODEL_CALLS
    assert len(captured.value.model_calls) == MAX_REACT_MODEL_CALLS
    assert len(captured.value.tool_executions) == MAX_REACT_MODEL_CALLS


@pytest.mark.asyncio
async def test_react_agent_rejects_modified_tool_arguments() -> None:
    chart = calculate_chart(BirthInput.model_validate(valid_payload()))
    credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="unused",
        api_key_last_four="6789",
        encryption_key_version="v1",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_chart_modified",
                        "name": "calculate_bazi_chart",
                        "arguments": json.dumps(
                            {
                                "gender": "female",
                                "true_solar_datetime": "1990-01-01T11:30:33",
                            }
                        ),
                    }
                ]
            },
        )

    with pytest.raises(ModelOutputFormatError, match="擅自修改") as captured:
        await generate_structured_report(
            chart=chart,
            credential=credential,
            api_key="sk-test",
            transport=httpx.MockTransport(handler),
        )

    assert len(captured.value.model_calls) == 1
    assert captured.value.tool_executions == ()


@pytest.mark.asyncio
async def test_invalid_model_report_preserves_response_for_debugging() -> None:
    chart = calculate_chart(BirthInput.model_validate(valid_payload()))
    credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://api.openai.com/v1",
        encrypted_api_key="unused",
        api_key_last_four="6789",
        encryption_key_version="v1",
    )
    raw_response = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"chart_overview":"不完整"}'}],
            }
        ]
    }

    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_chart_invalid_report",
                            "name": "calculate_bazi_chart",
                            "arguments": json.dumps(
                                {
                                    "gender": "male",
                                    "true_solar_datetime": "1990-01-01T11:30:33",
                                }
                            ),
                        }
                    ]
                },
            )
        return httpx.Response(200, json=raw_response)

    with pytest.raises(ModelOutputFormatError) as captured:
        await generate_structured_report(
            chart=chart,
            credential=credential,
            api_key="sk-test",
            transport=httpx.MockTransport(handler),
        )

    assert captured.value.raw_response == raw_response
    assert captured.value.model_latency_ms >= 0
    assert len(captured.value.model_calls) == 2
    assert len(captured.value.tool_executions) == 1


@pytest.mark.asyncio
async def test_chat_completions_report_uses_messages_and_accepts_json_fence() -> None:
    chart = calculate_chart(BirthInput.model_validate(valid_payload()))
    credential = ModelCredential(
        id=1,
        scope=LOCAL_CREDENTIAL_SCOPE,
        user_id=None,
        provider="openai",
        api_protocol="chat_completions",
        model="glm-test-model",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        encrypted_api_key="unused",
        api_key_last_four="6789",
        encryption_key_version="v1",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_chart_chat_1",
                                        "type": "function",
                                        "function": {
                                            "name": "calculate_bazi_chart",
                                            "arguments": json.dumps(
                                                {
                                                    "gender": "male",
                                                    "true_solar_datetime": (
                                                        "1990-01-01T11:30:33"
                                                    ),
                                                }
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"```json\n{sample_report().model_dump_json()}\n```",
                        }
                    }
                ]
            },
        )

    execution = await generate_structured_report(
        chart=chart,
        credential=credential,
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    assert execution.report.chart_overview == "命盘概览内容"
    assert "choices" in execution.raw_response
    assert len(requests) == 2
    assert requests[0].url.path == "/api/paas/v4/chat/completions"
    first_body = json.loads(requests[0].content)
    second_body = json.loads(requests[1].content)
    assert first_body["tool_choice"] == "auto"
    assert first_body["tools"][0]["function"]["name"] == "calculate_bazi_chart"
    assert "必须且只能包含以下 8 个字段" in first_body["messages"][0]["content"]
    assert "chart_overview：命盘整体概述" in first_body["messages"][0]["content"]
    assert "不要输出 Markdown 代码块" in first_body["messages"][0]["content"]
    assert "shen_sha 仅作辅助参考" in first_body["messages"][0]["content"]
    assert second_body["tool_choice"] == "auto"
    assert second_body["messages"][-1]["role"] == "tool"
    assert "big_luck_periods" not in second_body["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_connection_probe_uses_selected_chat_protocol() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    await probe_model_connection(
        base_url="https://api.openai.com/v1",
        model="test-model",
        api_key="sk-test",
        api_protocol="chat_completions",
        transport=httpx.MockTransport(handler),
    )

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer sk-test"
    assert b'"max_tokens":8' in requests[0].content
    assert b'"tools"' not in requests[0].content


@pytest.mark.asyncio
async def test_connection_probe_reports_missing_model() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(404, json={"error": {"message": "not found"}})
    )

    with pytest.raises(ModelProviderError, match="模型 ID"):
        await probe_model_connection(
            base_url="https://api.openai.com/v1",
            model="missing-model",
            api_key="sk-test",
            api_protocol="responses",
            transport=transport,
        )


def test_secret_cipher_detects_tampering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENCRYPTION_KEY", MASTER_KEY)
    cipher = SecretCipher.from_environment()
    token = cipher.encrypt("secret-value", scope=LOCAL_CREDENTIAL_SCOPE, key_version="v1")
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")

    with pytest.raises(SecretEncryptionError):
        cipher.decrypt(tampered, scope=LOCAL_CREDENTIAL_SCOPE, key_version="v1")
