from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import AuthRepository, hash_password


async def create_account(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str,
    password: str,
    role: str,
) -> UUID:
    async with session_factory() as session:
        user = await AuthRepository(session).create_user(
            username=username,
            display_name=username,
            password_hash=hash_password(password),
            role=role,
        )
        return user.id


@pytest.mark.asyncio
async def test_protected_chart_requires_login(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = database_client

    health = await client.get("/api/v1/health")
    chart = await client.post(
        "/api/v1/charts/preview",
        json={"beijing_datetime": "1990-01-01T12:00:00", "gender": "male"},
    )

    assert health.status_code == 200
    assert chart.status_code == 401


@pytest.mark.asyncio
async def test_first_visit_can_bootstrap_exactly_one_administrator(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = database_client

    before = await client.get("/api/v1/auth/bootstrap-status")
    created = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "owner",
            "display_name": "系统管理员",
            "password": "owner123",
        },
    )
    me = await client.get("/api/v1/auth/me")
    after = await client.get("/api/v1/auth/bootstrap-status")
    repeated = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "attacker",
            "display_name": "第二个管理员",
            "password": "attacker-password",
        },
    )

    assert before.json() == {"required": True}
    assert created.status_code == 201
    assert created.json()["user"]["role"] == "admin"
    assert me.status_code == 200
    assert after.json() == {"required": False}
    assert repeated.status_code == 409


@pytest.mark.asyncio
async def test_login_me_and_logout_use_revocable_http_only_cookie(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client
    await create_account(
        session_factory,
        username="admin",
        password="correct-password",
        role="admin",
    )

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "ADMIN", "password": "correct-password"},
    )
    me = await client.get("/api/v1/auth/me")
    logout = await client.post("/api/v1/auth/logout")
    after_logout = await client.get("/api/v1/auth/me")

    assert login.status_code == 200
    assert login.json()["user"]["role"] == "admin"
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=lax" in login.headers["set-cookie"]
    assert me.status_code == 200
    assert logout.status_code == 204
    assert after_logout.status_code == 401


@pytest.mark.asyncio
async def test_admin_creates_user_with_immediately_usable_password(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client
    await create_account(
        session_factory,
        username="admin",
        password="admin-password",
        role="admin",
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-password"},
    )
    created = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "reader",
            "display_name": "普通用户",
            "password": "temp1234",
            "role": "user",
        },
    )
    await client.post("/api/v1/auth/logout")

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "reader", "password": "temp1234"},
    )
    chart = await client.post(
        "/api/v1/charts/preview",
        json={"beijing_datetime": "1990-01-01T12:00:00", "gender": "male"},
    )
    admin_list = await client.get("/api/v1/admin/users")
    model_settings = await client.get("/api/v1/model-settings")

    assert created.status_code == 201
    assert login.status_code == 200
    assert chart.status_code == 200
    assert admin_list.status_code == 403
    assert model_settings.status_code == 403


@pytest.mark.asyncio
async def test_last_active_admin_cannot_be_disabled(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client
    admin_id = await create_account(
        session_factory,
        username="admin",
        password="admin-password",
        role="admin",
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-password"},
    )

    response = await client.patch(
        f"/api/v1/admin/users/{admin_id}",
        json={"status": "disabled"},
    )

    assert response.status_code == 409
    assert "最后一个有效管理员" in response.text


@pytest.mark.asyncio
async def test_admin_password_reset_sets_permanent_password(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client
    await create_account(
        session_factory,
        username="admin",
        password="admin-password",
        role="admin",
    )
    user_id = await create_account(
        session_factory,
        username="reader",
        password="original-password",
        role="user",
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-password"},
    )

    reset = await client.post(
        f"/api/v1/admin/users/{user_id}/reset-password",
        json={"new_password": "reset888"},
    )
    await client.post("/api/v1/auth/logout")
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "reader", "password": "reset888"},
    )
    chart = await client.post(
        "/api/v1/charts/preview",
        json={"beijing_datetime": "1990-01-01T12:00:00", "gender": "male"},
    )

    assert reset.status_code == 204
    assert login.status_code == 200
    assert chart.status_code == 200


@pytest.mark.asyncio
async def test_failed_logins_are_throttled_in_database(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = database_client
    await create_account(
        session_factory,
        username="reader",
        password="correct-password",
        role="user",
    )

    attempts = [
        await client.post(
            "/api/v1/auth/login",
            json={"username": "reader", "password": "incorrect-password"},
        )
        for _ in range(6)
    ]

    assert [response.status_code for response in attempts[:5]] == [401] * 5
    assert attempts[5].status_code == 429


@pytest.mark.asyncio
async def test_production_session_mutations_require_trusted_origin(
    database_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory = database_client
    await create_account(
        session_factory,
        username="admin",
        password="admin-password",
        role="admin",
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-password"},
    )
    monkeypatch.setenv("APP_ENV", "production")

    rejected = await client.post("/api/v1/auth/logout")
    accepted = await client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost:3000"},
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 204
