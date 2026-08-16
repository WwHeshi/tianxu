"""Authenticated multi-turn MingLi chat routes."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from ..agent_capabilities import AgentCapability
from ..agent_trace import (
    StoredAgentTrace,
    snapshot_agent_trace,
    trace_model_calls,
    trace_prompts,
)
from ..auth import AdminUserDependency, ReadyUserDependency
from ..bazi.engine import ChartCalculationError, calculate_chart
from ..bazi.tool import BaziChartToolInput
from ..chat_agent import ChatAgentError, run_chat_agent
from ..conversations import ConversationRepositoryDependency
from ..credentials import ModelCredentialRepository, get_credential_repository
from ..graph_store import GraphStoreDependency
from ..knowledge import KnowledgeRepositoryDependency
from ..knowledge_capability import KnowledgeCapability
from ..knowledge_tools import KnowledgeToolSession
from ..models import AgentConversation, AgentConversationMessage, ModelCredential
from ..rule_graph_capability import RuleGraphReadCapability
from ..schemas import (
    AgentConversationChartResponse,
    AgentConversationCreate,
    AgentConversationDetailResponse,
    AgentConversationListResponse,
    AgentConversationMessageResponse,
    AgentConversationSend,
    AgentConversationSummaryResponse,
    AgentConversationTraceResponse,
    AgentConversationTurnResponse,
    BirthInput,
)
from ..security import SecretCipher, SecretEncryptionError
from ..tool_calling_agent import ToolCallingResult, ToolCallingStreamEvent

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
CredentialRepositoryDependency = Annotated[
    ModelCredentialRepository,
    Depends(get_credential_repository),
]


def _summary(conversation: AgentConversation) -> AgentConversationSummaryResponse:
    return AgentConversationSummaryResponse(
        id=conversation.id,
        title=conversation.title,
        has_chart=conversation.birth_input is not None,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message(
    message: AgentConversationMessage,
    *,
    trace_available: bool = False,
) -> AgentConversationMessageResponse:
    return AgentConversationMessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        trace_available=trace_available,
    )


def _stored_trace(
    execution: ToolCallingResult,
    credential: ModelCredential,
) -> dict[str, object]:
    return {
        "api_protocol": credential.api_protocol,
        "model": credential.model,
        "endpoint": execution.endpoint,
        "trace": snapshot_agent_trace(
            body=execution.request_body,
            model_calls=execution.model_calls,
            tool_executions=execution.tool_executions,
        ),
    }


def _birth_input(conversation: AgentConversation) -> BirthInput | None:
    if conversation.birth_input is None:
        return None
    return BirthInput.model_validate(conversation.birth_input)


def _chart_summary(birth_input: BirthInput | None) -> AgentConversationChartResponse | None:
    if birth_input is None:
        return None
    chart = calculate_chart(birth_input, include_fortune_cycles=False)
    normalized = chart.normalized_input
    pillars = chart.chart.pillars
    birthplace = None
    if normalized.birthplace is not None:
        birthplace = " · ".join(item.name for item in normalized.birthplace.division_path)
    return AgentConversationChartResponse(
        gender=normalized.gender,
        true_solar_datetime=normalized.true_solar_datetime,
        birthplace=birthplace,
        year_pillar=pillars.year.gan_zhi,
        month_pillar=pillars.month.gan_zhi,
        day_pillar=pillars.day.gan_zhi,
        hour_pillar=pillars.hour.gan_zhi,
        day_master=chart.chart.day_master.symbol,
    )


async def _detail(
    conversation: AgentConversation,
    repository: ConversationRepositoryDependency,
) -> AgentConversationDetailResponse:
    messages = await repository.list_messages_with_trace_state(conversation.id)
    summary = _summary(conversation)
    return AgentConversationDetailResponse(
        **summary.model_dump(),
        chart=_chart_summary(_birth_input(conversation)),
        messages=[
            _message(message, trace_available=trace_available)
            for message, trace_available in messages
        ],
    )


@dataclass(frozen=True)
class PreparedChatRun:
    conversation: AgentConversation
    credential: ModelCredential
    api_key: str
    history: list[dict[str, str]]
    chart_input: BaziChartToolInput | None
    capabilities: tuple[AgentCapability, ...]


async def _prepare_chat_run(
    *,
    conversation_id: UUID,
    user_id: UUID,
    repository: ConversationRepositoryDependency,
    credential_repository: CredentialRepositoryDependency,
    knowledge_repository: KnowledgeRepositoryDependency,
    graph_store: GraphStoreDependency,
) -> PreparedChatRun:
    conversation = await repository.get_for_user(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    credential = await credential_repository.get()
    if credential is None:
        raise HTTPException(status_code=409, detail="模型 API 尚未由管理员配置")

    birth_input = _birth_input(conversation)
    chart_input = None
    if birth_input is not None:
        chart = calculate_chart(birth_input, include_fortune_cycles=False)
        chart_input = BaziChartToolInput(
            gender=chart.normalized_input.gender,
            true_solar_datetime=chart.normalized_input.true_solar_datetime,
        )
    existing_messages = await repository.list_messages(conversation.id)
    history = [
        {"role": message.role, "content": message.content}
        for message in existing_messages
    ]
    knowledge_capability = KnowledgeCapability(
        KnowledgeToolSession(await knowledge_repository.list_agent_documents())
    )
    try:
        api_key = SecretCipher.from_environment().decrypt(
            credential.encrypted_api_key,
            scope=credential.scope,
            key_version=credential.encryption_key_version,
        )
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return PreparedChatRun(
        conversation=conversation,
        credential=credential,
        api_key=api_key,
        history=history,
        chart_input=chart_input,
        capabilities=(knowledge_capability, RuleGraphReadCapability(graph_store)),
    )


@router.get("/conversations", response_model=AgentConversationListResponse)
async def list_conversations(
    repository: ConversationRepositoryDependency,
    user: ReadyUserDependency,
) -> AgentConversationListResponse:
    conversations = await repository.list_for_user(user.id)
    return AgentConversationListResponse(items=[_summary(item) for item in conversations])


@router.post(
    "/conversations",
    response_model=AgentConversationDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: AgentConversationCreate,
    repository: ConversationRepositoryDependency,
    user: ReadyUserDependency,
) -> AgentConversationDetailResponse:
    if payload.birth_input is not None:
        try:
            calculate_chart(payload.birth_input, include_fortune_cycles=False)
        except ChartCalculationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    conversation = await repository.create(
        user_id=user.id,
        birth_input=(
            payload.birth_input.model_dump(mode="json")
            if payload.birth_input is not None
            else None
        ),
    )
    return await _detail(conversation, repository)


@router.get(
    "/conversations/{conversation_id}",
    response_model=AgentConversationDetailResponse,
)
async def get_conversation(
    conversation_id: UUID,
    repository: ConversationRepositoryDependency,
    user: ReadyUserDependency,
) -> AgentConversationDetailResponse:
    conversation = await repository.get_for_user(conversation_id, user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    return await _detail(conversation, repository)


@router.post("/conversations/{conversation_id}/messages")
async def stream_message(
    conversation_id: UUID,
    payload: AgentConversationSend,
    repository: ConversationRepositoryDependency,
    credential_repository: CredentialRepositoryDependency,
    knowledge_repository: KnowledgeRepositoryDependency,
    graph_store: GraphStoreDependency,
    user: ReadyUserDependency,
) -> StreamingResponse:
    prepared = await _prepare_chat_run(
        conversation_id=conversation_id,
        user_id=user.id,
        repository=repository,
        credential_repository=credential_repository,
        knowledge_repository=knowledge_repository,
        graph_store=graph_store,
    )
    queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

    async def on_agent_event(event: ToolCallingStreamEvent) -> None:
        if event.type == "output_delta":
            await queue.put({"type": "delta", "content": event.text or ""})
        elif event.type == "output_reset":
            await queue.put({"type": "reset"})
        else:
            await queue.put(
                {
                    "type": "tool",
                    "phase": "started" if event.type == "tool_started" else "completed",
                    "name": event.tool_name or "",
                }
            )

    async def run_and_persist() -> None:
        await queue.put({"type": "status", "state": "thinking"})
        try:
            result = await run_chat_agent(
                credential=prepared.credential,
                api_key=prepared.api_key,
                user_message=payload.content,
                history=prepared.history,
                chart_input=prepared.chart_input,
                capabilities=prepared.capabilities,
                stream_callback=on_agent_event,
            )
            capture_trace = user.role == "admin"
            user_message, assistant_message = await repository.add_turn(
                conversation=prepared.conversation,
                user_content=payload.content,
                assistant_content=result.output_text,
                agent_trace=(
                    _stored_trace(result.execution, prepared.credential)
                    if capture_trace
                    else None
                ),
            )
            turn = AgentConversationTurnResponse(
                title=prepared.conversation.title,
                updated_at=prepared.conversation.updated_at,
                user_message=_message(user_message),
                assistant_message=_message(
                    assistant_message,
                    trace_available=capture_trace,
                ),
            )
            await queue.put(
                {
                    "type": "complete",
                    "turn": turn.model_dump(mode="json"),
                }
            )
        except asyncio.CancelledError:
            raise
        except ChatAgentError as exc:
            await queue.put({"type": "error", "message": str(exc)})
        except Exception:
            await queue.put({"type": "error", "message": "回答生成后保存失败，请重试。"})
        finally:
            await queue.put(None)

    async def event_stream():
        task = asyncio.create_task(run_and_persist())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n"
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/trace",
    response_model=AgentConversationTraceResponse,
)
async def get_message_trace(
    conversation_id: UUID,
    message_id: int,
    repository: ConversationRepositoryDependency,
    admin: AdminUserDependency,
) -> AgentConversationTraceResponse:
    conversation = await repository.get_for_user(conversation_id, admin.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    message = await repository.get_message(conversation.id, message_id)
    if message is None or message.role != "assistant" or message.agent_trace is None:
        raise HTTPException(status_code=404, detail="该回答没有可用执行轨迹")

    stored = message.agent_trace
    try:
        api_protocol = str(stored["api_protocol"])
        model = str(stored["model"])
        endpoint = str(stored["endpoint"])
        trace = StoredAgentTrace.model_validate(stored["trace"])
    except (KeyError, TypeError, ValidationError) as exc:
        raise HTTPException(status_code=500, detail="执行轨迹无法读取") from exc
    system_prompt, user_prompt = trace_prompts(trace.initial_request_body, api_protocol)
    return AgentConversationTraceResponse(
        api_protocol=api_protocol,
        model=model,
        endpoint=endpoint,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_calls=trace_model_calls(trace, api_protocol=api_protocol),
        tool_executions=trace.tool_executions,
        redacted=["API 密钥", "Authorization 请求头", "模型内部推理文本"],
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conversation_id: UUID,
    repository: ConversationRepositoryDependency,
    user: ReadyUserDependency,
) -> Response:
    if not await repository.delete_for_user(conversation_id, user.id):
        raise HTTPException(status_code=404, detail="对话不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
