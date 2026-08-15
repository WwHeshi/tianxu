"""Minimal persistence and decoding helpers for administrator TXT documents."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from .database import get_session
from .models import KnowledgeDocument

MAX_TXT_BYTES = 10 * 1024 * 1024


class InvalidTextFile(ValueError):
    """Raised when uploaded bytes are not a supported, readable text file."""


def clean_uploaded_filename(filename: str | None) -> str:
    cleaned = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not cleaned:
        raise InvalidTextFile("文件名不能为空")
    if len(cleaned) > 255:
        raise InvalidTextFile("文件名不能超过 255 个字符")
    if not cleaned.lower().endswith(".txt"):
        raise InvalidTextFile("只支持上传 TXT 文件")
    return cleaned


def _decode(data: bytes, encoding: str) -> str:
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise InvalidTextFile("TXT 编码无法识别或文件内容已损坏") from exc
    return text.removeprefix("\ufeff")


def _validate_text(text: str) -> None:
    if not text.strip():
        raise InvalidTextFile("TXT 文件内容为空")
    if "\x00" in text:
        raise InvalidTextFile("文件包含二进制内容，无法作为 TXT 读取")
    disallowed_controls = sum(
        1 for character in text if ord(character) < 32 and character not in "\t\n\r\f"
    )
    if disallowed_controls > max(4, len(text) // 1000):
        raise InvalidTextFile("文件包含过多控制字符，无法作为 TXT 读取")


def detect_txt_encoding(data: bytes) -> tuple[str, str]:
    """Return the stable encoding label and decoded text after strict validation."""

    if not data:
        raise InvalidTextFile("TXT 文件内容为空")

    if data.startswith(b"\xef\xbb\xbf"):
        candidates = ("utf-8-sig",)
    elif data.startswith(b"\xff\xfe"):
        candidates = ("utf-16-le",)
    elif data.startswith(b"\xfe\xff"):
        candidates = ("utf-16-be",)
    else:
        candidates = ("utf-8", "gb18030")

    last_error: InvalidTextFile | None = None
    for encoding in candidates:
        try:
            text = _decode(data, encoding)
            _validate_text(text)
            return encoding, text
        except InvalidTextFile as exc:
            last_error = exc
    raise last_error or InvalidTextFile("TXT 编码无法识别")


def decode_stored_txt(data: bytes, encoding: str) -> str:
    text = _decode(data, encoding)
    _validate_text(text)
    return text


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_documents(
        self,
        *,
        search: str,
        offset: int,
        limit: int,
    ) -> tuple[list[KnowledgeDocument], int]:
        filters = []
        if search:
            pattern = f"%{search}%"
            filters.append(
                or_(
                    KnowledgeDocument.title.ilike(pattern),
                    KnowledgeDocument.original_filename.ilike(pattern),
                )
            )
        total = await self.session.scalar(
            select(func.count()).select_from(KnowledgeDocument).where(*filters)
        )
        result = await self.session.execute(
            select(KnowledgeDocument)
            .where(*filters)
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.title)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), int(total or 0)

    async def get_document(
        self,
        document_id: UUID,
        *,
        include_data: bool = False,
    ) -> KnowledgeDocument | None:
        statement = select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        if include_data:
            statement = statement.options(undefer(KnowledgeDocument.file_data))
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_sha256(self, digest: str) -> KnowledgeDocument | None:
        result = await self.session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.sha256 == digest)
        )
        return result.scalar_one_or_none()

    async def list_agent_documents(self) -> list[KnowledgeDocument]:
        """Load a stable, title-ordered snapshot including raw TXT bytes for one Agent run."""

        result = await self.session.execute(
            select(KnowledgeDocument)
            .options(undefer(KnowledgeDocument.file_data))
            .order_by(KnowledgeDocument.title, KnowledgeDocument.id)
        )
        return list(result.scalars())

    async def add(self, document: KnowledgeDocument) -> None:
        self.session.add(document)
        await self.session.flush()

    async def delete(self, document: KnowledgeDocument) -> None:
        await self.session.delete(document)
        await self.session.flush()


def get_knowledge_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeRepository:
    return KnowledgeRepository(session)


KnowledgeRepositoryDependency = Annotated[
    KnowledgeRepository,
    Depends(get_knowledge_repository),
]
