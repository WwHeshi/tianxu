import json
from datetime import datetime

import httpx
import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_tools import AgentTool, AgentToolRegistry
from app.auth import AuthRepository, hash_password
from app.bazi.tool import BaziChartToolInput
from app.chat_agent import ChatAgentResult, run_chat_agent
from app.conversations import ConversationRepository
from app.models import ModelCredential
from app.tool_calling_agent import (
    ToolCallingModelCall,
    ToolCallingResult,
    ToolCallingStreamEvent,
    run_tool_calling_agent,
)


class StreamToolInput(BaseModel):
    pass


class StreamToolOutput(BaseModel):
    found: bool


def stream_test_tool() -> AgentTool:
    return AgentTool(
        name="lookup",
        description="测试查询",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        input_model=StreamToolInput,
        execute=lambda _: StreamToolOutput(found=True),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_protocol", ["responses", "chat_completions"])
async def test_tool_calling_agent_replays_normalized_conversation_history(
    api_protocol: str,
) -> None:
    observed: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        if api_protocol == "responses":
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "\n  继续回答"}],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "\n  继续回答"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_tool_calling_agent(
            api_protocol=api_protocol,
            model="test-model",
            base_url="https://example.test/v1",
            api_key="sk-test",
            system_prompt="系统提示词",
            user_prompt="第二轮问题",
            conversation_history=(
                {"role": "user", "content": "第一轮问题"},
                {"role": "assistant", "content": "第一轮回答"},
            ),
            output_schema_name=None,
            output_schema=None,
            client=client,
        )

    expected = [
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "第一轮回答"},
        {"role": "user", "content": "第二轮问题"},
    ]
    if api_protocol == "responses":
        assert observed["input"] == expected
    else:
        assert observed["messages"] == [
            {"role": "system", "content": "系统提示词"},
            *expected,
        ]
    assert result.output_text == "继续回答"


def event_stream(*events: dict) -> str:
    return "".join(
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        for event in events
    ) + "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_responses_stream_resets_temporary_text_around_tool_calls() -> None:
    request_count = 0
    events: list[ToolCallingStreamEvent] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            body = event_stream(
                {"type": "response.output_text.delta", "output_index": 0, "delta": "临时文字"},
                {
                    "type": "response.completed",
                    "response": {
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_lookup",
                                "name": "lookup",
                                "arguments": "{}",
                            }
                        ]
                    },
                },
            )
        else:
            body = event_stream(
                {"type": "response.output_text.delta", "output_index": 0, "delta": "\n\t"},
                {"type": "response.output_text.delta", "output_index": 0, "delta": "  最终"},
                {"type": "response.output_text.delta", "output_index": 0, "delta": "回答"},
                {
                    "type": "response.completed",
                    "response": {
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {"type": "output_text", "text": "\n\t  最终回答"}
                                ],
                            }
                        ]
                    },
                },
            )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_tool_calling_agent(
            api_protocol="responses",
            model="test-model",
            base_url="https://example.test/v1",
            api_key="sk-test",
            system_prompt="提示词",
            user_prompt="问题",
            output_schema_name=None,
            output_schema=None,
            client=client,
            tool_registry=AgentToolRegistry((stream_test_tool(),)),
            stream_callback=events.append,
        )

    assert result.output_text == "最终回答"
    assert [event.type for event in events] == [
        "output_delta",
        "output_reset",
        "tool_started",
        "tool_completed",
        "output_delta",
        "output_delta",
    ]
    assert [event.text for event in events if event.type == "output_delta"] == [
        "临时文字",
        "最终",
        "回答",
    ]


@pytest.mark.asyncio
async def test_chat_completions_stream_assembles_tool_arguments_and_final_text() -> None:
    request_count = 0
    events: list[ToolCallingStreamEvent] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            body = event_stream(
                {
                    "choices": [
                        {"index": 0, "delta": {"role": "assistant", "content": "临时文字"}}
                    ]
                },
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_lookup",
                                        "type": "function",
                                        "function": {"name": "lookup", "arguments": "{}"},
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )
        else:
            body = event_stream(
                {
                    "choices": [
                        {"index": 0, "delta": {"role": "assistant", "content": "\n\t"}}
                    ]
                },
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "  最终"}}
                    ]
                },
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "回答"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_tool_calling_agent(
            api_protocol="chat_completions",
            model="test-model",
            base_url="https://example.test/v1",
            api_key="sk-test",
            system_prompt="提示词",
            user_prompt="问题",
            output_schema_name=None,
            output_schema=None,
            client=client,
            tool_registry=AgentToolRegistry((stream_test_tool(),)),
            stream_callback=events.append,
        )

    assert result.output_text == "最终回答"
    assert [event.type for event in events] == [
        "output_delta",
        "output_reset",
        "tool_started",
        "tool_completed",
        "output_delta",
        "output_delta",
    ]


@pytest.mark.asyncio
async def test_general_chat_registers_chart_and_fortune_tools() -> None:
    observed_tools: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        observed_tools.extend(tool["name"] for tool in body["tools"])
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "一般知识回答"}],
                    }
                ]
            },
        )

    credential = ModelCredential(
        scope="test",
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://example.test/v1",
        encrypted_api_key="unused",
        api_key_last_four="test",
        encryption_key_version="test",
    )
    answer = await run_chat_agent(
        credential=credential,
        api_key="sk-test",
        user_message="什么是财星？",
        history=(),
        chart_input=None,
        capabilities=(),
        transport=httpx.MockTransport(handler),
    )

    assert observed_tools == ["calculate_bazi_chart", "calculate_fortune_at"]
    assert answer.output_text == "一般知识回答"


@pytest.mark.asyncio
async def test_bound_chat_prompt_anchors_the_authoritative_chart() -> None:
    observed: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "命盘回答"}],
                    }
                ]
            },
        )

    credential = ModelCredential(
        scope="test",
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="test-model",
        base_url="https://example.test/v1",
        encrypted_api_key="unused",
        api_key_last_four="test",
        encryption_key_version="test",
    )
    chart_input = BaziChartToolInput(
        gender="male",
        true_solar_datetime=datetime(1974, 4, 28, 16, 40),
    )
    await run_chat_agent(
        credential=credential,
        api_key="sk-test",
        user_message="看看事业",
        history=(),
        chart_input=chart_input,
        capabilities=(),
        transport=httpx.MockTransport(handler),
    )

    prompt = observed["instructions"]
    assert "【本会话已绑定命盘】" in prompt
    assert "固定对象：男命；四柱索引：" in prompt
    assert "日主：" in prompt
    assert "gender=male" in prompt
    assert "true_solar_datetime=1974-04-28T16:40:00" in prompt
    assert "不要再次询问已经绑定的" in prompt
    assert "分析另一张命盘" in prompt


@pytest.mark.asyncio
async def test_conversation_repository_keeps_compact_turns_and_updates_title(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_factory = database_client
    async with session_factory() as session:
        user = await AuthRepository(session).create_user(
            username="chat-user",
            display_name="对话用户",
            password_hash=hash_password("chat-password"),
            role="user",
        )
        repository = ConversationRepository(session)
        conversation = await repository.create(user_id=user.id, birth_input=None)
        user_message, assistant_message = await repository.add_turn(
            conversation=conversation,
            user_content="请分析一下什么是财星",
            assistant_content="财星是日主所克的五行。",
        )

        messages = await repository.list_messages(conversation.id)

    assert conversation.title == "请分析一下什么是财星"
    assert [(item.role, item.content) for item in messages] == [
        ("user", "请分析一下什么是财星"),
        ("assistant", "财星是日主所克的五行。"),
    ]
    assert user_message.id < assistant_message.id


@pytest.mark.asyncio
async def test_chat_conversations_are_owned_by_the_logged_in_user(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client
    async with session_factory() as session:
        for username in ("first-user", "second-user"):
            await AuthRepository(session).create_user(
                username=username,
                display_name=username,
                password_hash=hash_password("chat-password"),
                role="user",
            )

    await client.post(
        "/api/v1/auth/login",
        json={"username": "first-user", "password": "chat-password"},
    )
    created = await client.post(
        "/api/v1/chat/conversations",
        json={
            "birth_input": {
                "beijing_datetime": "1990-01-01T12:00:00",
                "gender": "male",
            }
        },
    )
    conversation_id = created.json()["id"]
    listed = await client.get("/api/v1/chat/conversations")
    await client.post("/api/v1/auth/logout")

    await client.post(
        "/api/v1/auth/login",
        json={"username": "second-user", "password": "chat-password"},
    )
    hidden = await client.get(f"/api/v1/chat/conversations/{conversation_id}")
    delete_hidden = await client.delete(f"/api/v1/chat/conversations/{conversation_id}")

    assert created.status_code == 201
    assert created.json()["has_chart"] is True
    assert created.json()["chart"]["day_pillar"]
    assert listed.json()["items"][0]["id"] == conversation_id
    assert hidden.status_code == 404
    assert delete_hidden.status_code == 404


@pytest.mark.asyncio
async def test_streamed_chat_persists_trace_visible_only_to_admin(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import chat_routes

    client, session_factory = database_client
    async with session_factory() as session:
        for username, role in (("trace-admin", "admin"), ("trace-user", "user")):
            await AuthRepository(session).create_user(
                username=username,
                display_name=username,
                password_hash=hash_password("chat-password"),
                role=role,
            )

    await client.post(
        "/api/v1/auth/login",
        json={"username": "trace-admin", "password": "chat-password"},
    )
    created = await client.post(
        "/api/v1/chat/conversations",
        json={"birth_input": None},
    )
    conversation_id = created.json()["id"]
    credential = ModelCredential(
        scope="test",
        user_id=None,
        provider="openai",
        api_protocol="responses",
        model="trace-model",
        base_url="https://example.test/v1",
        encrypted_api_key="unused",
        api_key_last_four="test",
        encryption_key_version="test",
    )

    async def fake_prepare_chat_run(**kwargs) -> chat_routes.PreparedChatRun:
        conversation = await kwargs["repository"].get_for_user(
            kwargs["conversation_id"],
            kwargs["user_id"],
        )
        assert conversation is not None
        return chat_routes.PreparedChatRun(
            conversation=conversation,
            credential=credential,
            api_key="sk-test",
            history=[],
            chart_input=None,
            capabilities=(),
        )

    async def fake_run_chat_agent(**kwargs) -> ChatAgentResult:
        callback = kwargs["stream_callback"]
        await callback(ToolCallingStreamEvent(type="output_delta", text="流式"))
        await callback(ToolCallingStreamEvent(type="output_delta", text="回答"))
        request_body = {
            "model": "trace-model",
            "instructions": "系统提示词",
            "input": [{"role": "user", "content": "测试问题"}],
            "stream": True,
        }
        raw_response = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "流式回答"}],
                }
            ]
        }
        execution = ToolCallingResult(
            output_text="流式回答",
            system_prompt="系统提示词",
            endpoint="https://example.test/v1/responses",
            request_body=request_body,
            raw_response=raw_response,
            response_status_code=200,
            model_latency_ms=10,
            input_tokens=2,
            output_tokens=3,
            model_calls=(
                ToolCallingModelCall(
                    stage="final_answer",
                    request_body=request_body,
                    raw_response=raw_response,
                    latency_ms=10,
                    status_code=200,
                ),
            ),
            tool_executions=(),
        )
        return ChatAgentResult(output_text="流式回答", execution=execution)

    monkeypatch.setattr(chat_routes, "_prepare_chat_run", fake_prepare_chat_run)
    monkeypatch.setattr(chat_routes, "run_chat_agent", fake_run_chat_agent)
    streamed = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "测试问题"},
    )
    events = [json.loads(line) for line in streamed.text.splitlines() if line]
    complete = next(event for event in events if event["type"] == "complete")
    assistant = complete["turn"]["assistant_message"]
    trace = await client.get(
        f"/api/v1/chat/conversations/{conversation_id}/messages/{assistant['id']}/trace"
    )

    await client.post("/api/v1/auth/logout")
    await client.post(
        "/api/v1/auth/login",
        json={"username": "trace-user", "password": "chat-password"},
    )
    forbidden = await client.get(
        f"/api/v1/chat/conversations/{conversation_id}/messages/{assistant['id']}/trace"
    )

    assert streamed.status_code == 200
    assert [event["content"] for event in events if event["type"] == "delta"] == [
        "流式",
        "回答",
    ]
    assert assistant["content"] == "流式回答"
    assert assistant["trace_available"] is True
    assert trace.status_code == 200
    assert trace.json()["model_calls"][0]["stage"] == "final_answer"
    assert forbidden.status_code == 403
