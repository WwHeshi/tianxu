"""Persistence for compact, user-owned multi-turn Agent conversations."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, undefer

from .database import get_session
from .models import AgentConversation, AgentConversationMessage

DEFAULT_CONVERSATION_TITLES = {"新对话", "命盘对话"}


def title_from_message(content: str) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    return compact[:30] if compact else "新对话"


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        birth_input: dict[str, object] | None,
    ) -> AgentConversation:
        conversation = AgentConversation(
            user_id=user_id,
            title="命盘对话" if birth_input is not None else "新对话",
            birth_input=birth_input,
        )
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def list_for_user(self, user_id: UUID) -> list[AgentConversation]:
        result = await self.session.execute(
            select(AgentConversation)
            .where(AgentConversation.user_id == user_id)
            .order_by(AgentConversation.updated_at.desc(), AgentConversation.created_at.desc())
        )
        return list(result.scalars())

    async def get_for_user(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> AgentConversation | None:
        result = await self.session.execute(
            select(AgentConversation).where(
                AgentConversation.id == conversation_id,
                AgentConversation.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_messages(
        self,
        conversation_id: UUID,
    ) -> list[AgentConversationMessage]:
        result = await self.session.execute(
            select(AgentConversationMessage)
            .options(defer(AgentConversationMessage.agent_trace))
            .where(AgentConversationMessage.conversation_id == conversation_id)
            .order_by(AgentConversationMessage.id)
        )
        return list(result.scalars())

    async def list_messages_with_trace_state(
        self,
        conversation_id: UUID,
    ) -> list[tuple[AgentConversationMessage, bool]]:
        result = await self.session.execute(
            select(
                AgentConversationMessage,
                AgentConversationMessage.agent_trace.is_not(None),
            )
            .options(defer(AgentConversationMessage.agent_trace))
            .where(AgentConversationMessage.conversation_id == conversation_id)
            .order_by(AgentConversationMessage.id)
        )
        return [(row[0], bool(row[1])) for row in result.all()]

    async def get_message(
        self,
        conversation_id: UUID,
        message_id: int,
    ) -> AgentConversationMessage | None:
        result = await self.session.execute(
            select(AgentConversationMessage)
            .options(undefer(AgentConversationMessage.agent_trace))
            .where(
                AgentConversationMessage.id == message_id,
                AgentConversationMessage.conversation_id == conversation_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_turn(
        self,
        *,
        conversation: AgentConversation,
        user_content: str,
        assistant_content: str,
        agent_trace: dict[str, object] | None = None,
    ) -> tuple[AgentConversationMessage, AgentConversationMessage]:
        user_message = AgentConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_content,
        )
        assistant_message = AgentConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_content,
            agent_trace=agent_trace,
        )
        self.session.add_all((user_message, assistant_message))
        if conversation.title in DEFAULT_CONVERSATION_TITLES:
            conversation.title = title_from_message(user_content)
        conversation.updated_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(user_message)
        await self.session.refresh(assistant_message)
        return user_message, assistant_message

    async def delete_for_user(self, conversation_id: UUID, user_id: UUID) -> bool:
        result = await self.session.execute(
            delete(AgentConversation).where(
                AgentConversation.id == conversation_id,
                AgentConversation.user_id == user_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)


def get_conversation_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationRepository:
    return ConversationRepository(session)


ConversationRepositoryDependency = Annotated[
    ConversationRepository,
    Depends(get_conversation_repository),
]
