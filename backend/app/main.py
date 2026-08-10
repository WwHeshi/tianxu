"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.admin_routes import router as admin_router
from .api.auth_routes import router as auth_router
from .api.evaluation_routes import router as evaluation_router
from .api.routes import router
from .auth import SESSION_COOKIE_NAME
from .bazi.engine import ENGINE_VERSION
from .config import app_environment
from .evals.mingli_bench.worker import evaluation_task_manager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await evaluation_task_manager.start()
    try:
        yield
    finally:
        await evaluation_task_manager.stop()


def _cors_origins() -> list[str]:
    configured = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


app = FastAPI(
    title="Tianxu BaZi API",
    version="0.1.0",
    description="Deterministic BaZi chart calculations for the Tianxu analysis agent.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_trusted_origin_for_session_requests(request: Request, call_next):
    """Reject production cookie-authenticated mutations from untrusted browser origins."""

    if (
        app_environment() not in {"development", "local", "test"}
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and SESSION_COOKIE_NAME in request.cookies
        and request.headers.get("origin") not in _cors_origins()
    ):
        return JSONResponse(status_code=403, content={"detail": "请求来源未通过安全校验"})
    return await call_next(request)


app.include_router(router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(evaluation_router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "tianxu-bazi-api", "engine_version": ENGINE_VERSION}
