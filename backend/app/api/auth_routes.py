"""Login, logout, current-account, and password-change endpoints."""

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError

from ..auth import (
    AuthRepositoryDependency,
    CurrentUserDependency,
    clear_session_cookie,
    dummy_password_hash,
    hash_password,
    login_throttle_key,
    request_ip,
    session_token_from_request,
    set_session_cookie,
    verify_password,
)
from ..schemas import (
    BootstrapAdminRequest,
    BootstrapStatusResponse,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/bootstrap-status", response_model=BootstrapStatusResponse)
async def bootstrap_status(
    repository: AuthRepositoryDependency,
) -> BootstrapStatusResponse:
    return BootstrapStatusResponse(required=await repository.bootstrap_required())


@router.post(
    "/bootstrap",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bootstrap_admin(
    payload: BootstrapAdminRequest,
    request: Request,
    response: Response,
    repository: AuthRepositoryDependency,
) -> LoginResponse:
    try:
        resolved = await repository.bootstrap_admin(
            username=payload.username,
            display_name=payload.display_name,
            password_hash=hash_password(payload.password.get_secret_value()),
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except IntegrityError as exc:
        await repository.session.rollback()
        raise HTTPException(status_code=409, detail="系统已经完成管理员初始化") from exc
    if resolved is None:
        raise HTTPException(status_code=409, detail="系统已经完成管理员初始化")
    user, token = resolved
    set_session_cookie(response, token)
    return LoginResponse(user=UserResponse.model_validate(user))


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    repository: AuthRepositoryDependency,
) -> LoginResponse:
    ip_address = request_ip(request)
    throttle_key = login_throttle_key(payload.username, ip_address)
    if await repository.is_login_blocked(throttle_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过于频繁，请稍后再试",
        )

    user = await repository.get_user_by_username(payload.username)
    password = payload.password.get_secret_value()
    valid_password = verify_password(
        password,
        user.password_hash if user is not None else dummy_password_hash(),
    )
    if user is None or not valid_password or user.status != "active":
        await repository.record_login_failure(throttle_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    await repository.clear_login_failures(throttle_key)
    token = await repository.create_login_session(
        user,
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
    )
    set_session_cookie(response, token)
    return LoginResponse(user=UserResponse.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    repository: AuthRepositoryDependency,
) -> Response:
    digest = session_token_from_request(request)
    if digest is not None:
        await repository.revoke_token(digest)
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUserDependency) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post("/change-password", response_model=UserResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: CurrentUserDependency,
    repository: AuthRepositoryDependency,
) -> UserResponse:
    if not verify_password(payload.current_password.get_secret_value(), user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前密码不正确",
        )
    await repository.change_password(
        user,
        hash_password(payload.new_password.get_secret_value()),
        keep_token_digest=session_token_from_request(request),
    )
    await repository.add_audit_log(
        actor_user_id=user.id,
        target_user_id=user.id,
        action="user.password_changed",
        details={},
        ip_address=request_ip(request),
    )
    return UserResponse.model_validate(user)
