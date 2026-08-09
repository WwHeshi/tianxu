"""Administrator-only account management endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError

from ..auth import AdminUserDependency, AuthRepositoryDependency, hash_password, request_ip
from ..schemas import (
    AdminPasswordReset,
    AdminUserCreate,
    AdminUserUpdate,
    UserListResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/users", response_model=UserListResponse)
async def list_users(
    _admin: AdminUserDependency,
    repository: AuthRepositoryDependency,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> UserListResponse:
    users, total = await repository.list_users(offset=offset, limit=limit)
    return UserListResponse(
        items=[UserResponse.model_validate(user) for user in users],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminUserCreate,
    request: Request,
    admin: AdminUserDependency,
    repository: AuthRepositoryDependency,
) -> UserResponse:
    if await repository.get_user_by_username(payload.username) is not None:
        raise HTTPException(status_code=409, detail="该用户名已存在")
    try:
        user = await repository.create_user(
            username=payload.username,
            display_name=payload.display_name,
            password_hash=hash_password(payload.temporary_password.get_secret_value()),
            role=payload.role,
            must_change_password=True,
        )
    except IntegrityError as exc:
        await repository.session.rollback()
        raise HTTPException(status_code=409, detail="该用户名已存在") from exc
    await repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=user.id,
        action="admin.user_created",
        details={"role": user.role},
        ip_address=request_ip(request),
    )
    return UserResponse.model_validate(user)


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    _admin: AdminUserDependency,
    repository: AuthRepositoryDependency,
) -> UserResponse:
    user = await repository.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    request: Request,
    admin: AdminUserDependency,
    repository: AuthRepositoryDependency,
) -> UserResponse:
    user = await repository.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not payload.model_fields_set:
        raise HTTPException(status_code=422, detail="请至少提供一个需要修改的字段")

    removes_active_admin = (
        user.role == "admin"
        and user.status == "active"
        and (
            (payload.role is not None and payload.role != "admin")
            or (payload.status is not None and payload.status != "active")
        )
    )
    if removes_active_admin and await repository.locked_active_admin_count() <= 1:
        raise HTTPException(status_code=409, detail="不能停用或降级最后一个有效管理员")

    changes: dict[str, str] = {}
    for field in ("display_name", "role", "status"):
        value = getattr(payload, field)
        if value is not None and value != getattr(user, field):
            changes[field] = value
            setattr(user, field, value)
    await repository.save_user(user)
    if payload.status == "disabled":
        await repository.revoke_user_sessions(user.id)
    await repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=user.id,
        action="admin.user_updated",
        details={"changed_fields": sorted(changes)},
        ip_address=request_ip(request),
    )
    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: UUID,
    payload: AdminPasswordReset,
    request: Request,
    admin: AdminUserDependency,
    repository: AuthRepositoryDependency,
) -> Response:
    user = await repository.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.password_hash = hash_password(payload.new_password.get_secret_value())
    user.must_change_password = False
    await repository.save_user(user)
    await repository.revoke_user_sessions(user.id)
    await repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=user.id,
        action="admin.password_reset",
        details={},
        ip_address=request_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/revoke-sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_sessions(
    user_id: UUID,
    request: Request,
    admin: AdminUserDependency,
    repository: AuthRepositoryDependency,
) -> Response:
    user = await repository.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    await repository.revoke_user_sessions(user.id)
    await repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=user.id,
        action="admin.sessions_revoked",
        details={},
        ip_address=request_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
