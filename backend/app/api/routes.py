"""Versioned HTTP routes."""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..auth import AdminUserDependency, ReadyUserDependency
from ..bazi.engine import ENGINE_VERSION, ChartCalculationError, calculate_chart
from ..bazi.locations import LocationDataError, validate_location_data
from ..config import app_environment
from ..credentials import (
    LOCAL_CREDENTIAL_SCOPE,
    ModelCredentialRepository,
    get_credential_repository,
)
from ..knowledge import KnowledgeRepositoryDependency
from ..knowledge_capability import KnowledgeCapability
from ..knowledge_tools import KnowledgeToolSession
from ..models import ModelCredential
from ..reports import (
    PROMPT_VERSION,
    REPORT_SCHEMA_VERSION,
    ModelOutputFormatError,
    ModelProviderError,
    ReportGenerationResult,
    generate_structured_report,
    test_model_connection,
)
from ..schemas import (
    AgentDebugTrace,
    AgentModelCallDebug,
    AgentRequestDebug,
    AgentToolExecutionDebug,
    BirthInput,
    ChartPreviewResponse,
    HealthResponse,
    ModelConnectionTestRequest,
    ModelConnectionTestResponse,
    ModelSettingsResponse,
    ModelSettingsUpdate,
    ReportGenerationResponse,
    ReportMetadata,
)
from ..security import CREDENTIAL_ENCRYPTION_VERSION, SecretCipher, SecretEncryptionError

router = APIRouter(prefix="/api/v1")
CredentialRepositoryDependency = Annotated[
    ModelCredentialRepository, Depends(get_credential_repository)
]


def _report_model_calls(
    execution: ReportGenerationResult | ModelOutputFormatError,
) -> list[AgentModelCallDebug]:
    calls = execution.model_calls
    if not calls:
        return [
            AgentModelCallDebug(
                sequence=1,
                stage="final_answer",
                request_body=execution.request_body,
                response_body=execution.raw_response,
                duration_ms=execution.model_latency_ms,
            )
        ]
    return [
        AgentModelCallDebug(
            sequence=index,
            stage=call.stage,
            request_body=call.request_body,
            response_body=call.raw_response,
            duration_ms=call.latency_ms,
            tool_call_count=call.tool_call_count,
        )
        for index, call in enumerate(calls, start=1)
    ]


def _report_tool_executions(
    execution: ReportGenerationResult | ModelOutputFormatError,
) -> list[AgentToolExecutionDebug]:
    return [
        AgentToolExecutionDebug(
            sequence=index,
            name=item.name,
            input=item.input,
            output=item.output,
            duration_ms=item.duration_ms,
        )
        for index, item in enumerate(execution.tool_executions, start=1)
    ]


def _report_debug_trace(
    *,
    execution: ReportGenerationResult | ModelOutputFormatError,
    credential: ModelCredential,
) -> AgentDebugTrace:
    model_calls = _report_model_calls(execution)
    tool_executions = _report_tool_executions(execution)
    return AgentDebugTrace(
        system_prompt=execution.system_prompt,
        user_prompt=execution.user_prompt,
        request=AgentRequestDebug(
            method="POST",
            endpoint=execution.endpoint,
            provider=credential.provider,
            api_protocol=credential.api_protocol,
            model=credential.model,
            request_count=len(model_calls),
            body=execution.request_body,
        ),
        raw_response=execution.raw_response,
        model_calls=model_calls,
        tool_executions=tool_executions,
        redacted=["API 密钥", "Authorization 请求头", "模型内部推理文本"],
    )


def _model_settings_response(
    credential: ModelCredential | None,
) -> ModelSettingsResponse:
    if credential is None:
        return ModelSettingsResponse(configured=False)
    return ModelSettingsResponse(
        configured=True,
        provider=credential.provider,
        api_protocol=credential.api_protocol,
        model=credential.model,
        base_url=credential.base_url,
        api_key_masked=f"••••{credential.api_key_last_four}",
    )


def _validate_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Base URL 必须是有效的 HTTP(S) 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="Base URL 不能包含认证信息、查询参数或片段")
    if parsed.scheme == "http":
        local_hosts = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
        if (
            app_environment() not in {"development", "local", "test"}
            or parsed.hostname.lower() not in local_hosts
        ):
            raise HTTPException(status_code=422, detail="HTTP Base URL 只允许指向本地开发服务")
    if parsed.hostname.lower() in {"169.254.169.254", "metadata.google.internal"}:
        raise HTTPException(status_code=422, detail="不允许使用该 Base URL")


def _normalize_base_url(base_url: str, api_protocol: str) -> str:
    normalized = base_url.rstrip("/")
    suffixes = {
        "responses": "/responses",
        "chat_completions": "/chat/completions",
    }
    for protocol, suffix in suffixes.items():
        if not normalized.endswith(suffix):
            continue
        if protocol != api_protocol:
            raise HTTPException(status_code=422, detail="API 地址与所选协议不一致")
        normalized = normalized[: -len(suffix)]
        break
    _validate_base_url(normalized)
    return normalized


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    try:
        validate_location_data()
    except LocationDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return HealthResponse(status="ok", service="bazi-backend", engine_version=ENGINE_VERSION)


@router.post(
    "/charts/preview",
    response_model=ChartPreviewResponse,
    status_code=status.HTTP_200_OK,
    tags=["charts"],
)
def preview_chart(payload: BirthInput, _user: ReadyUserDependency) -> ChartPreviewResponse:
    try:
        return calculate_chart(payload)
    except ChartCalculationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/model-settings",
    response_model=ModelSettingsResponse,
    tags=["model-settings"],
)
async def get_model_settings(
    repository: CredentialRepositoryDependency,
    _admin: AdminUserDependency,
) -> ModelSettingsResponse:
    return _model_settings_response(await repository.get())


@router.put(
    "/model-settings",
    response_model=ModelSettingsResponse,
    tags=["model-settings"],
)
async def put_model_settings(
    payload: ModelSettingsUpdate,
    repository: CredentialRepositoryDependency,
    _admin: AdminUserDependency,
) -> ModelSettingsResponse:
    base_url = _normalize_base_url(payload.base_url, payload.api_protocol)
    api_key = payload.api_key.get_secret_value().strip()
    if len(api_key) < 8:
        raise HTTPException(status_code=422, detail="API 密钥长度不足")
    key_version = CREDENTIAL_ENCRYPTION_VERSION
    try:
        encrypted = SecretCipher.from_environment().encrypt(
            api_key,
            scope=LOCAL_CREDENTIAL_SCOPE,
            key_version=key_version,
        )
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    credential = await repository.upsert(
        provider=payload.provider,
        api_protocol=payload.api_protocol,
        model=payload.model,
        base_url=base_url,
        encrypted_api_key=encrypted,
        api_key_last_four=api_key[-4:],
        encryption_key_version=key_version,
    )
    return _model_settings_response(credential)


@router.post(
    "/model-settings/test",
    response_model=ModelConnectionTestResponse,
    tags=["model-settings"],
)
async def test_model_settings_connection(
    payload: ModelConnectionTestRequest,
    repository: CredentialRepositoryDependency,
    _admin: AdminUserDependency,
) -> ModelConnectionTestResponse:
    base_url = _normalize_base_url(payload.base_url, payload.api_protocol)

    api_key = payload.api_key.get_secret_value().strip() if payload.api_key else ""
    if not api_key:
        credential = await repository.get()
        if credential is None:
            raise HTTPException(status_code=409, detail="请先输入 API 密钥")
        try:
            api_key = SecretCipher.from_environment().decrypt(
                credential.encrypted_api_key,
                scope=credential.scope,
                key_version=credential.encryption_key_version,
            )
        except SecretEncryptionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        await test_model_connection(
            base_url=base_url,
            model=payload.model,
            api_key=api_key,
            api_protocol=payload.api_protocol,
        )
    except ModelProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ModelConnectionTestResponse(
        ok=True,
        provider=payload.provider,
        api_protocol=payload.api_protocol,
        model=payload.model,
        message="连接成功，API 密钥和模型均可用。",
    )


@router.delete(
    "/model-settings",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["model-settings"],
)
async def delete_model_settings(
    repository: CredentialRepositoryDependency,
    _admin: AdminUserDependency,
) -> Response:
    await repository.delete()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/reports/generate",
    response_model=ReportGenerationResponse,
    tags=["reports"],
)
async def generate_report(
    payload: BirthInput,
    repository: CredentialRepositoryDependency,
    knowledge_repository: KnowledgeRepositoryDependency,
    user: ReadyUserDependency,
) -> ReportGenerationResponse:
    try:
        chart = calculate_chart(payload)
    except ChartCalculationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    credential = await repository.get()
    if credential is None:
        raise HTTPException(status_code=409, detail="模型 API 尚未由管理员配置")
    knowledge_capability = KnowledgeCapability(
        KnowledgeToolSession(await knowledge_repository.list_agent_documents())
    )
    try:
        api_key = SecretCipher.from_environment().decrypt(
            credential.encrypted_api_key,
            scope=credential.scope,
            key_version=credential.encryption_key_version,
        )
        execution = await generate_structured_report(
            chart=chart,
            credential=credential,
            api_key=api_key,
            capabilities=(knowledge_capability,),
        )
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelOutputFormatError as exc:
        if user.role != "admin":
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        debug_trace = _report_debug_trace(
            execution=exc,
            credential=credential,
        )
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "debug_trace": debug_trace.model_dump(mode="json")},
        ) from exc
    except ModelProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ReportGenerationResponse(
        chart=chart,
        report=execution.report,
        metadata=ReportMetadata(
            provider=credential.provider,
            api_protocol=credential.api_protocol,
            model=credential.model,
            prompt_version=PROMPT_VERSION,
            schema_version=REPORT_SCHEMA_VERSION,
            engine_version=ENGINE_VERSION,
        ),
        debug_trace=_report_debug_trace(
            execution=execution,
            credential=credential,
        )
        if user.role == "admin"
        else None,
    )
