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
from app.knowledge import get_knowledge_repository
from app.knowledge_capability import KnowledgeCapability
from app.knowledge_tools import KnowledgeToolSession
from app.main import app
from app.models import KnowledgeDocument, ModelCredential, User
from app.reports import (
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
from app.rule_graph_capability import RuleGraphReadCapability
from app.schemas import BaziReport, BirthInput
from app.security import SecretCipher, SecretEncryptionError
from app.tool_calling_agent import model_response_history_items

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


def test_responses_history_preserves_reason_without_tool_call() -> None:
    reasoning = {
        "type": "reasoning",
        "id": "reason_1",
        "summary": [{"type": "summary_text", "text": "reason summary"}],
        "encrypted_content": "encrypted-reason",
    }
    message = {
        "type": "message",
        "id": "message_1",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "final answer"}],
    }

    history = model_response_history_items(
        {"output": [reasoning, message]},
        "responses",
    )

    assert history == (reasoning, message)
    assert history[0] is not reasoning
    assert history[1] is not message


@pytest.mark.parametrize("reasoning_key", ["reasoning_content", "reasoning", "thinking"])
def test_chat_history_preserves_reason_without_tool_call(reasoning_key: str) -> None:
    history = model_response_history_items(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "final answer",
                        reasoning_key: "reason text",
                    }
                }
            ]
        },
        "chat_completions",
    )

    assert history == (
        {
            "role": "assistant",
            "content": "final answer",
            reasoning_key: "reason text",
        },
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


class FakeKnowledgeRepository:
    async def list_agent_documents(self) -> list:
        return []


@pytest_asyncio.fixture
async def api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, FakeCredentialRepository]]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", MASTER_KEY)
    repository = FakeCredentialRepository()
    knowledge_repository = FakeKnowledgeRepository()
    app.dependency_overrides[get_credential_repository] = lambda: repository
    app.dependency_overrides[get_knowledge_repository] = lambda: knowledge_repository
    test_admin = authenticated_user()
    app.dependency_overrides[get_current_user] = lambda: test_admin
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repository
    app.dependency_overrides.pop(get_credential_repository, None)
    app.dependency_overrides.pop(get_knowledge_repository, None)
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
    assert data["metadata"]["prompt_version"] == "bazi-report-v21-rule-graph"
    assert "knowledge_version" not in data["metadata"]
    assert "citations" not in data
    assert data["chart"]["chart"]["pillars"]["day"]["gan_zhi"] == "丙寅"
    assert "steps" not in data["debug_trace"]
    assert data["debug_trace"]["request"]["request_count"] == 2
    assert data["debug_trace"]["tool_executions"][0]["name"] == "calculate_bazi_chart"
    assert data["debug_trace"]["system_prompt"] == "system prompt"
    assert "sk-test-super-secret-6789" not in response.text
    assert captured["api_key"] == "sk-test-super-secret-6789"
    capabilities = captured["capabilities"]
    assert isinstance(capabilities, tuple)
    assert len(capabilities) == 2
    assert isinstance(capabilities[0], KnowledgeCapability)
    assert isinstance(capabilities[1], RuleGraphReadCapability)
    assert [tool.name for tool in capabilities[1].tools()] == [
        "search_rule_graph",
        "query_rule_graph",
    ]

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
    assert "steps" not in trace
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
            ReportModelCall(
                "action_selection",
                {"round": 1},
                {"action": 1},
                5,
                tool_call_count=2,
            ),
            ReportModelCall("final_answer", {"round": 2}, {"final": True}, 7),
        ),
        tool_executions=(
            ReportToolExecution("calculate_bazi_chart", {"round": 1}, {"chart": 1}, 1),
            ReportToolExecution("calculate_bazi_chart", {"round": 2}, {"chart": 2}, 1),
        ),
    )

    trace = routes._report_debug_trace(
        execution=execution,
        credential=credential,
    )

    assert trace.request.request_count == 2
    assert len(trace.model_calls) == 2
    assert len(trace.tool_executions) == 2
    assert not hasattr(trace, "steps")


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
    assert "calendar" not in execution.context
    assert "element_distribution" not in execution.context
    assert "calculation_policy" not in execution.context
    assert "engine" not in execution.context
    assert "limitations" not in execution.context
    assert "day_master" not in execution.context
    assert "chart_reliability_warnings" not in execution.context
    assert len(requests) == 2
    first_body = json.loads(requests[0].content)
    second_body = json.loads(requests[1].content)
    assert first_body["tool_choice"] == "auto"
    assert first_body["include"] == ["reasoning.encrypted_content"]
    assert "max_output_tokens" not in first_body
    assert first_body["tools"][0]["name"] == "calculate_bazi_chart"
    assert first_body["tools"][1]["name"] == "calculate_fortune_at"
    assert "pillars" not in first_body["input"][0]["content"]
    user_content = first_body["input"][0]["content"]
    assert "性别：male" in user_content
    assert "真太阳出生时间：1990-01-01T11:30:33" in user_content
    assert "报告基准时间（北京时间）：" in user_content
    assert "当前大运：" not in user_content
    assert "当前流年：" not in user_content
    assert "当前流月：" not in user_content
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
async def test_report_agent_searches_and_reads_knowledge() -> None:
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
    text = "卷一\n财多身弱，富屋贫人。\n其理仍须结合日主根气细察。"
    data = text.encode("utf-8")
    document = KnowledgeDocument(
        id=uuid4(),
        title="滴天髓阐微",
        original_filename="滴天髓阐微.txt",
        encoding="utf-8",
        byte_size=len(data),
        sha256="b" * 64,
        file_data=data,
    )
    knowledge_session = KnowledgeToolSession([document])
    requests: list[httpx.Request] = []
    read_cursor = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal read_cursor
        requests.append(request)
        body = json.loads(request.content)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_search_knowledge",
                            "name": "search_knowledge",
                            "arguments": json.dumps(
                                {
                                    "queries": ["财多身弱"],
                                    "source_ids": [],
                                }
                            ),
                        }
                    ]
                },
            )
        if len(requests) == 2:
            search_output = json.loads(body["input"][-1]["output"])
            read_cursor = search_output[0]["read_cursor"]
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_read_knowledge",
                            "name": "read_knowledge",
                            "arguments": json.dumps(
                                {"cursor": read_cursor}
                            ),
                        }
                    ]
                },
            )
        report = sample_report().model_copy(
            update={"career": "传统命理解释需要结合承载能力。"}
        )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": report.model_dump_json()}],
                    }
                ]
            },
        )

    execution = await generate_structured_report(
        chart=chart,
        credential=credential,
        api_key="sk-test",
        capabilities=(KnowledgeCapability(knowledge_session),),
        transport=httpx.MockTransport(handler),
    )

    first_body = json.loads(requests[0].content)
    assert [tool["name"] for tool in first_body["tools"]] == [
        "calculate_bazi_chart",
        "calculate_fortune_at",
        "search_knowledge",
        "read_knowledge",
    ]
    assert "D001《滴天髓阐微》" in first_body["instructions"]
    assert [item.name for item in execution.tool_executions] == [
        "search_knowledge",
        "read_knowledge",
    ]
    assert "知识库版本" not in first_body["instructions"]
    assert "〔" not in execution.report.career


@pytest.mark.asyncio
async def test_report_agent_calls_fortune_tool_for_prompt_baseline() -> None:
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
            user_content = json.loads(request.content)["input"][0]["content"]
            report_time = user_content.split("报告基准时间（北京时间）：", 1)[1]
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_fortune_1",
                            "name": "calculate_fortune_at",
                            "arguments": json.dumps(
                                {
                                    "gender": "male",
                                    "true_solar_datetime": "1990-01-01T11:30:33",
                                    "as_of_datetime": report_time,
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

    assert len(requests) == 2
    assert execution.tool_executions[0].name == "calculate_fortune_at"
    assert execution.tool_executions[0].input["gender"] == "male"
    assert execution.tool_executions[0].input["true_solar_datetime"] == (
        "1990-01-01T11:30:33"
    )
    assert set(execution.tool_executions[0].output) == {"大运", "流年", "流月"}
    assert "虚岁" in execution.tool_executions[0].output["流年"]
    second_body = json.loads(requests[1].content)
    observation = json.loads(second_body["input"][-1]["output"])
    assert observation == execution.tool_executions[0].output


@pytest.mark.asyncio
async def test_report_agent_accepts_multiple_tool_calls_in_one_response() -> None:
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
                            "call_id": "call_chart_parallel",
                            "name": "calculate_bazi_chart",
                            "arguments": json.dumps(
                                {
                                    "gender": "male",
                                    "true_solar_datetime": "1990-01-01T11:30:33",
                                }
                            ),
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_fortune_parallel",
                            "name": "calculate_fortune_at",
                            "arguments": json.dumps(
                                {
                                    "gender": "male",
                                    "true_solar_datetime": "1990-01-01T11:30:33",
                                    "as_of_datetime": "2026-08-14T12:00:00",
                                }
                            ),
                        },
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

    assert len(requests) == 2
    assert json.loads(requests[0].content)["parallel_tool_calls"] is True
    assert execution.model_calls[0].tool_call_count == 2
    assert [item.name for item in execution.tool_executions] == [
        "calculate_bazi_chart",
        "calculate_fortune_at",
    ]
    second_input = json.loads(requests[1].content)["input"]
    observations = [
        item for item in second_input if item.get("type") == "function_call_output"
    ]
    assert [item["call_id"] for item in observations] == [
        "call_chart_parallel",
        "call_fortune_parallel",
    ]


@pytest.mark.asyncio
async def test_tool_calling_agent_accepts_direct_final_without_tool_call() -> None:
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
    assert "报告基准时间（北京时间）：" in request_body["input"][0]["content"]
    assert "当前大运：" not in request_body["input"][0]["content"]
    assert "当前流年：" not in request_body["input"][0]["content"]
    assert "当前流月：" not in request_body["input"][0]["content"]
    assert request_body["tools"][1]["name"] == "calculate_fortune_at"
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
async def test_react_loop_continues_beyond_the_previous_response_limit() -> None:
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
        if request_count <= 12:
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

    assert request_count == 13
    assert len(execution.model_calls) == 13
    assert len(execution.tool_executions) == 12
    assert execution.model_calls[-1].stage == "final_answer"


@pytest.mark.asyncio
async def test_tool_calling_agent_rejects_modified_tool_arguments() -> None:
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
                                "reasoning_content": "先获取命盘与当前运势。",
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
                                    },
                                    {
                                        "id": "call_fortune_chat_1",
                                        "type": "function",
                                        "function": {
                                            "name": "calculate_fortune_at",
                                            "arguments": json.dumps(
                                                {
                                                    "gender": "male",
                                                    "true_solar_datetime": (
                                                        "1990-01-01T11:30:33"
                                                    ),
                                                    "as_of_datetime": (
                                                        "2026-08-14T12:00:00"
                                                    ),
                                                }
                                            ),
                                        },
                                    },
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
    assert first_body["parallel_tool_calls"] is True
    assert "max_tokens" not in first_body
    assert first_body["tools"][0]["function"]["name"] == "calculate_bazi_chart"
    assert "必须且只能包含以下 8 个字段" in first_body["messages"][0]["content"]
    assert "chart_overview：命盘整体概述" in first_body["messages"][0]["content"]
    assert "不要输出 Markdown 代码块" in first_body["messages"][0]["content"]
    assert "神煞仅作辅助参考" in first_body["messages"][0]["content"]
    assert second_body["tool_choice"] == "auto"
    assert second_body["messages"][-3]["reasoning_content"] == "先获取命盘与当前运势。"
    assert [item["role"] for item in second_body["messages"][-3:]] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert [item["tool_call_id"] for item in second_body["messages"][-2:]] == [
        "call_chart_chat_1",
        "call_fortune_chat_1",
    ]
    assert all(
        "big_luck_periods" not in item["content"]
        for item in second_body["messages"][-2:]
    )
    assert execution.model_calls[0].tool_call_count == 2
    assert len(execution.tool_executions) == 2


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
