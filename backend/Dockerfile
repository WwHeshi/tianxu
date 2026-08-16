FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    RULE_GRAPH_EMBEDDING_MODEL_PATH=/opt/models/bge-base-zh-v1.5

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
COPY --from=builder /app/.venv /app/.venv

COPY external/models/bge-base-zh-v1.5 /opt/models/bge-base-zh-v1.5

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app

EXPOSE 8000

CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && exec uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port 8000"]
