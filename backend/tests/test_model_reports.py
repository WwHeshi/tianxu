from collections.abc import AsyncIterator
from datetime import datetime
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
    ModelOutputFormatError,
    ModelProviderError,
    ReportGenerationResult,
    build_report_context,
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
        )

    monkeypatch.setattr(routes, "generate_structured_report", fake_generate)

    response = await client.post("/api/v1/reports/generate", json=valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["model"] == "test-model"
    assert data["metadata"]["api_protocol"] == "responses"
    assert data["metadata"]["prompt_version"] == "bazi-report-v10"
    assert data["chart"]["chart"]["pillars"]["day"]["gan_zhi"] == "丙寅"
    assert [step["id"] for step in data["debug_trace"]["steps"]] == [
        "chart",
        "context",
        "prompt",
        "model",
        "validation",
    ]
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


def test_report_context_omits_full_fortune_timeline() -> None:
    chart = calculate_chart(BirthInput.model_validate(valid_payload()))

    context = build_report_context(chart, now=datetime(2026, 8, 8, 12, 0))

    serialized = str(context)
    assert "big_luck_periods" not in serialized
    assert set(context) == {
        "birth",
        "pillars",
        "current_fortune",
    }
    assert context["birth"] == {
        "input_beijing_datetime": "1990-01-01T12:00:00",
        "effective_chart_datetime": "1990-01-01T11:30:33",
        "chart_time_basis": "真太阳时",
        "gender": "男",
    }
    year = context["pillars"]["year"]
    assert "name" not in year
    assert "growth_stage" not in year
    assert year["day_master_growth_stage"] == "临官"
    assert year["pillar_stem_growth_stage"] == "帝旺"
    assert year["heavenly_stem"]["yin_yang"] == "阴"
    assert "polarity" not in year["heavenly_stem"]
    assert year["earthly_branch"]["primary_element"] == "火"
    assert year["earthly_branch"]["hidden_stems"][0]["is_main_qi"] is True
    assert year["earthly_branch"]["hidden_stems"][0]["position"] == 1
    current = context["current_fortune"]
    assert isinstance(current, dict)
    assert current["big_luck_sequence_direction"] == "逆排"
    assert set(current["current_big_luck"]) == {
        "phase",
        "effective_from",
        "effective_until_exclusive",
        "pillar",
    }
    assert set(current["current_annual"]) == {
        "year",
        "nominal_age_sui",
        "effective_from",
        "effective_until_exclusive",
        "pillar",
    }
    assert "index" not in serialized
    assert "transition" not in serialized


@pytest.mark.asyncio
async def test_model_request_is_one_shot_structured_and_has_no_tools() -> None:
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
    assert len(requests) == 1
    body = requests[0].read().decode()
    assert '"type":"json_schema"' in body
    assert '"tools"' not in body
    assert "big_luck_periods" not in body


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

    def handler(_: httpx.Request) -> httpx.Response:
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
    assert len(requests) == 1
    assert requests[0].url.path == "/api/paas/v4/chat/completions"
    body = requests[0].read().decode()
    assert '"messages"' in body
    assert "必须且只能包含以下 8 个字段" in body
    assert "chart_overview：命盘整体概述" in body
    assert "不要输出 Markdown 代码块" in body
    assert "auxiliary_shen_sha 仅作辅助参考" in body
    assert '"tools"' not in body
    assert "big_luck_periods" not in body


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
