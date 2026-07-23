"""FastAPI application entry point."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .bazi.engine import ENGINE_VERSION


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
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "tianxu-bazi-api", "engine_version": ENGINE_VERSION}
