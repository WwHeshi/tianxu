"""Password hashing, revocable sessions, authorization, and account persistence."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import app_environment
from .database import get_session
from .models import ApplicationState, AuditLog, AuthSession, LoginThrottle, User

SESSION_COOKIE_NAME = "tianxu_session"
SESSION_TTL = timedelta(days=7)
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_BLOCK = timedelta(minutes=15)
LOGIN_MAX_FAILURES = 5
BOOTSTRAP_STATE_KEY = "auth.bootstrap_completed"
_password_hash = PasswordHash.recommended()


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_username(value: str) -> str:
    return value.strip().lower()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except (TypeError, ValueError):
        return False


@cache
def dummy_password_hash() -> str:
    return hash_password("tianxu-dummy-password-not-used")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login_throttle_key(username: str, ip_address: str | None) -> str:
    value = f"{normalize_username(username)}\0{ip_address or '-'}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def request_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def set_session_cookie(response: Response, token: str) -> None:
    secure = app_environment() not in {"development", "local", "test"}
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    secure = app_environment() not in {"development", "local", "test"}
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user_by_username(self, username: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.username == normalize_username(username))
        )
        return result.scalar_one_or_none()

    async def get_user(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: str,
        must_change_password: bool = True,
    ) -> User:
        user = User(
            username=normalize_username(username),
            display_name=display_name.strip(),
            password_hash=password_hash,
            role=role,
            status="active",
            must_change_password=must_change_password,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def bootstrap_required(self) -> bool:
        state = await self.session.get(ApplicationState, BOOTSTRAP_STATE_KEY)
        if state is not None and state.boolean_value:
            return False
        count = await self.session.scalar(select(func.count()).select_from(User))
        return int(count or 0) == 0

    async def bootstrap_admin(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[User, str] | None:
        result = await self.session.execute(
            select(ApplicationState)
            .where(ApplicationState.key == BOOTSTRAP_STATE_KEY)
            .with_for_update()
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = ApplicationState(key=BOOTSTRAP_STATE_KEY, boolean_value=False)
            self.session.add(state)
            await self.session.flush()
        if state.boolean_value:
            return None
        count = await self.session.scalar(select(func.count()).select_from(User))
        if int(count or 0) > 0:
            state.boolean_value = True
            await self.session.commit()
            return None

        now = utc_now()
        user = User(
            username=normalize_username(username),
            display_name=display_name.strip(),
            password_hash=password_hash,
            role="admin",
            status="active",
            must_change_password=False,
            last_login_at=now,
        )
        self.session.add(user)
        await self.session.flush()
        token = secrets.token_urlsafe(32)
        self.session.add(
            AuthSession(
                user_id=user.id,
                token_hash=token_digest(token),
                expires_at=now + SESSION_TTL,
                ip_address=ip_address,
                user_agent=(user_agent or "")[:512] or None,
            )
        )
        self.session.add(
            AuditLog(
                actor_user_id=user.id,
                target_user_id=user.id,
                action="system.admin_bootstrapped",
                details={"source": "web"},
                ip_address=ip_address,
            )
        )
        state.boolean_value = True
        await self.session.commit()
        await self.session.refresh(user)
        return user, token

    async def mark_bootstrap_completed(self) -> None:
        state = await self.session.get(ApplicationState, BOOTSTRAP_STATE_KEY)
        if state is None:
            self.session.add(ApplicationState(key=BOOTSTRAP_STATE_KEY, boolean_value=True))
        else:
            state.boolean_value = True
        await self.session.commit()

    async def create_login_session(
        self,
        user: User,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        self.session.add(
            AuthSession(
                user_id=user.id,
                token_hash=token_digest(token),
                expires_at=utc_now() + SESSION_TTL,
                ip_address=ip_address,
                user_agent=(user_agent or "")[:512] or None,
            )
        )
        user.last_login_at = utc_now()
        await self.session.commit()
        await self.session.refresh(user)
        return token

    async def get_session_user(self, digest: str) -> tuple[AuthSession, User] | None:
        result = await self.session.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == digest)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def revoke_token(self, digest: str) -> None:
        result = await self.session.execute(
            select(AuthSession).where(AuthSession.token_hash == digest)
        )
        auth_session = result.scalar_one_or_none()
        if auth_session is not None and auth_session.revoked_at is None:
            auth_session.revoked_at = utc_now()
            await self.session.commit()

    async def revoke_user_sessions(
        self,
        user_id: UUID,
        *,
        except_digest: str | None = None,
    ) -> None:
        result = await self.session.execute(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
        )
        changed = False
        for auth_session in result.scalars():
            if except_digest is not None and auth_session.token_hash == except_digest:
                continue
            auth_session.revoked_at = utc_now()
            changed = True
        if changed:
            await self.session.commit()

    async def change_password(
        self,
        user: User,
        password_hash: str,
        *,
        keep_token_digest: str | None = None,
    ) -> None:
        user.password_hash = password_hash
        user.must_change_password = False
        await self.session.commit()
        await self.revoke_user_sessions(user.id, except_digest=keep_token_digest)
        await self.session.refresh(user)

    async def is_login_blocked(self, key_hash: str) -> bool:
        throttle = await self.session.get(LoginThrottle, key_hash)
        return bool(
            throttle
            and throttle.blocked_until
            and _as_utc(throttle.blocked_until) > utc_now()
        )

    async def record_login_failure(self, key_hash: str) -> None:
        now = utc_now()
        throttle = await self.session.get(LoginThrottle, key_hash)
        if throttle is None:
            throttle = LoginThrottle(
                key_hash=key_hash,
                failure_count=0,
                window_started_at=now,
            )
            self.session.add(throttle)
        elif now - _as_utc(throttle.window_started_at) >= LOGIN_WINDOW:
            throttle.failure_count = 0
            throttle.window_started_at = now
            throttle.blocked_until = None
        throttle.failure_count += 1
        if throttle.failure_count >= LOGIN_MAX_FAILURES:
            throttle.blocked_until = now + LOGIN_BLOCK
        await self.session.commit()

    async def clear_login_failures(self, key_hash: str) -> None:
        throttle = await self.session.get(LoginThrottle, key_hash)
        if throttle is not None:
            await self.session.delete(throttle)
            await self.session.commit()

    async def list_users(self, *, offset: int, limit: int) -> tuple[list[User], int]:
        total = await self.session.scalar(select(func.count()).select_from(User))
        result = await self.session.execute(
            select(User).order_by(User.created_at.desc(), User.username).offset(offset).limit(limit)
        )
        return list(result.scalars()), int(total or 0)

    async def locked_active_admin_count(self) -> int:
        result = await self.session.execute(
            select(User.id).where(
                User.role == "admin",
                User.status == "active",
            ).with_for_update()
        )
        return len(list(result.scalars()))

    async def save_user(self, user: User) -> None:
        await self.session.commit()
        await self.session.refresh(user)

    async def add_audit_log(
        self,
        *,
        actor_user_id: UUID | None,
        target_user_id: UUID | None,
        action: str,
        details: dict[str, Any],
        ip_address: str | None,
    ) -> None:
        self.session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                target_user_id=target_user_id,
                action=action,
                details=details,
                ip_address=ip_address,
            )
        )
        await self.session.commit()


def get_auth_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthRepository:
    return AuthRepository(session)


AuthRepositoryDependency = Annotated[AuthRepository, Depends(get_auth_repository)]


async def get_current_user(
    request: Request,
    repository: AuthRepositoryDependency,
) -> User:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    resolved = await repository.get_session_user(token_digest(token))
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    auth_session, user = resolved
    if (
        auth_session.revoked_at is not None
        or _as_utc(auth_session.expires_at) <= utc_now()
        or user.status != "active"
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    return user


CurrentUserDependency = Annotated[User, Depends(get_current_user)]


def require_password_changed(user: CurrentUserDependency) -> User:
    if user.must_change_password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请先修改临时密码")
    return user


ReadyUserDependency = Annotated[User, Depends(require_password_changed)]


def require_admin(user: ReadyUserDependency) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


AdminUserDependency = Annotated[User, Depends(require_admin)]


def session_token_from_request(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    return token_digest(token) if token else None
