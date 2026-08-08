"""Persistence helpers for encrypted model credentials."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .models import ModelCredential

LOCAL_CREDENTIAL_SCOPE = "local-default"


class ModelCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, scope: str = LOCAL_CREDENTIAL_SCOPE) -> ModelCredential | None:
        result = await self.session.execute(
            select(ModelCredential).where(ModelCredential.scope == scope)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        provider: str,
        api_protocol: str,
        model: str,
        base_url: str,
        encrypted_api_key: str,
        api_key_last_four: str,
        encryption_key_version: str,
        scope: str = LOCAL_CREDENTIAL_SCOPE,
    ) -> ModelCredential:
        credential = await self.get(scope)
        if credential is None:
            credential = ModelCredential(scope=scope, user_id=None)
            self.session.add(credential)
        credential.provider = provider
        credential.api_protocol = api_protocol
        credential.model = model
        credential.base_url = base_url
        credential.encrypted_api_key = encrypted_api_key
        credential.api_key_last_four = api_key_last_four
        credential.encryption_key_version = encryption_key_version
        await self.session.commit()
        await self.session.refresh(credential)
        return credential

    async def delete(self, scope: str = LOCAL_CREDENTIAL_SCOPE) -> bool:
        credential = await self.get(scope)
        if credential is None:
            return False
        await self.session.delete(credential)
        await self.session.commit()
        return True


def get_credential_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelCredentialRepository:
    return ModelCredentialRepository(session)
