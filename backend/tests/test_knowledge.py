from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import AuthRepository, hash_password
from app.knowledge import KnowledgeRepository, clean_uploaded_filename
from app.models import AuditLog


async def create_account(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str,
    role: str,
) -> UUID:
    async with session_factory() as session:
        user = await AuthRepository(session).create_user(
            username=username,
            display_name=username,
            password_hash=hash_password("test-password"),
            role=role,
            must_change_password=False,
        )
        return user.id


async def login(client: AsyncClient, username: str) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "test-password"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_knowledge_endpoints_require_an_administrator(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client

    anonymous = await client.get("/api/v1/admin/knowledge/documents")
    assert anonymous.status_code == 401

    await create_account(session_factory, username="reader", role="user")
    await login(client, "reader")
    forbidden_list = await client.get("/api/v1/admin/knowledge/documents")
    forbidden_upload = await client.post(
        "/api/v1/admin/knowledge/documents",
        files={"file": ("book.txt", "内容".encode(), "text/plain")},
    )
    assert forbidden_list.status_code == 403
    assert forbidden_upload.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "raw", "expected_encoding"),
    [
        ("utf8.txt", "第一章\n天地".encode("utf-8"), "utf-8"),
        ("utf16.txt", "第一章\n天地".encode("utf-16"), "utf-16-le"),
        ("gb.txt", "第一章\n天地".encode("gb18030"), "gb18030"),
    ],
)
async def test_admin_uploads_browses_and_downloads_supported_txt_encodings(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    filename: str,
    raw: bytes,
    expected_encoding: str,
) -> None:
    client, session_factory = database_client
    await create_account(session_factory, username="admin", role="admin")
    await login(client, "admin")

    uploaded = await client.post(
        "/api/v1/admin/knowledge/documents",
        data={"title": "测试资料"},
        files={"file": (filename, raw, "text/plain")},
    )
    assert uploaded.status_code == 201
    payload = uploaded.json()
    assert payload["title"] == "测试资料"
    assert payload["encoding"] == expected_encoding
    assert payload["byte_size"] == len(raw)
    assert "file_data" not in payload

    document_id = payload["id"]
    listing = await client.get("/api/v1/admin/knowledge/documents?search=测试")
    first_page = await client.get(
        f"/api/v1/admin/knowledge/documents/{document_id}/content?offset=0&limit=3"
    )
    second_page = await client.get(
        f"/api/v1/admin/knowledge/documents/{document_id}/content?offset=3&limit=100"
    )
    downloaded = await client.get(
        f"/api/v1/admin/knowledge/documents/{document_id}/download"
    )

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert first_page.json()["content"] == "第一章"
    assert first_page.json()["has_more"] is True
    assert second_page.json()["content"] == "\n天地"
    assert second_page.json()["has_more"] is False
    assert downloaded.content == raw
    assert downloaded.headers["content-type"] == "application/octet-stream"
    async with session_factory() as session:
        agent_documents = await KnowledgeRepository(session).list_agent_documents()
    assert len(agent_documents) == 1
    assert agent_documents[0].file_data == raw


@pytest.mark.asyncio
async def test_duplicate_invalid_and_oversized_files_are_rejected(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client
    await create_account(session_factory, username="admin", role="admin")
    await login(client, "admin")
    raw = "命理资料".encode("utf-8")

    accepted = await client.post(
        "/api/v1/admin/knowledge/documents",
        files={"file": ("book.txt", raw, "text/plain")},
    )
    duplicate = await client.post(
        "/api/v1/admin/knowledge/documents",
        files={"file": ("other.txt", raw, "text/plain")},
    )
    wrong_extension = await client.post(
        "/api/v1/admin/knowledge/documents",
        files={"file": ("book.pdf", raw, "application/pdf")},
    )
    binary = await client.post(
        "/api/v1/admin/knowledge/documents",
        files={"file": ("binary.txt", b"\x00\x01\x02\x03\x04\x05", "text/plain")},
    )
    oversized = await client.post(
        "/api/v1/admin/knowledge/documents",
        files={"file": ("large.txt", b"a" * (10 * 1024 * 1024 + 1), "text/plain")},
    )

    assert accepted.status_code == 201
    assert duplicate.status_code == 409
    assert wrong_extension.status_code == 400
    assert binary.status_code == 400
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_deleting_document_removes_data_and_writes_audit_logs(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client
    await create_account(session_factory, username="admin", role="admin")
    await login(client, "admin")
    uploaded = await client.post(
        "/api/v1/admin/knowledge/documents",
        files={"file": ("book.txt", "原文".encode("utf-8"), "text/plain")},
    )
    document_id = uploaded.json()["id"]

    deleted = await client.delete(f"/api/v1/admin/knowledge/documents/{document_id}")
    missing = await client.get(
        f"/api/v1/admin/knowledge/documents/{document_id}/content"
    )

    assert deleted.status_code == 204
    assert missing.status_code == 404
    async with session_factory() as session:
        actions = list((await session.scalars(select(AuditLog.action))).all())
    assert "admin.knowledge_uploaded" in actions
    assert "admin.knowledge_deleted" in actions


def test_uploaded_filename_cannot_escape_into_a_path() -> None:
    assert clean_uploaded_filename("../../资料.txt") == "资料.txt"
    assert clean_uploaded_filename("..\\..\\资料.txt") == "资料.txt"
