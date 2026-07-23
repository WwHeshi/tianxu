# Tianxu 八字分析 Agent

这是一个前后端分离的八字分析 Web 应用。系统先用确定性的排盘引擎计算命盘，再由 Agent 基于结构化命盘生成解释和回答追问。模型不负责自行计算四柱。

## 当前状态

项目正在按 MVP 分阶段搭建。`frontend/`、`backend/` 和排盘规则会逐步完善；根目录已经提供统一的本地开发配置。

## Docker 热更新开发（推荐）

需要 Docker Desktop（包含 Docker Compose）。首次运行前复制环境变量文件：

```powershell
Copy-Item .env.example .env
docker compose -f docker-compose.dev.yml up --build --watch
```

启动后访问：

- 前端：<http://localhost:3000>
- FastAPI 文档：<http://localhost:8000/docs>
- 后端健康检查：<http://localhost:8000/api/v1/health>
- PostgreSQL：`localhost:5432`

开发配置使用 Compose Watch：

- 修改 `backend/app/` 后，源码会同步到容器并触发 Uvicorn 重载。
- 修改 `frontend/` 后，源码会同步到容器并触发 Next.js Fast Refresh。
- 修改 `pyproject.toml`、`uv.lock`、`package.json` 或 `package-lock.json` 后，对应镜像会自动重建。
- `node_modules`、`.venv` 和 `.next` 保留在 Linux 容器中，不会与 Windows 主机目录互相覆盖。
- Windows Docker Desktop 下已启用轮询监听，避免文件事件丢失。

默认数据库密码只适合本地开发，请勿用于生产环境。停止热更新环境：

```powershell
docker compose -f docker-compose.dev.yml down
```

原有 Compose 配置仍可通过 `docker compose up --build` 启动；日常开发推荐使用 `docker-compose.dev.yml`。

## 本地启动（不使用 Docker）

后端需要 Python 3.12+ 和 `uv`，前端需要 Node.js 20+：

```powershell
# 终端一：后端
cd backend
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 终端二：前端
cd frontend
npm ci
npm run dev
```

本地运行后端时，`DATABASE_URL` 应指向 `localhost`；运行在 Compose 中时使用 `.env` 里的 `COMPOSE_DATABASE_URL`（Compose 会将它注入为容器内的 `DATABASE_URL`）。前端通过 `NEXT_PUBLIC_API_BASE_URL` 找到后端。

## 目录约定

```text
tianxu/
  frontend/       # Next.js + TypeScript 用户界面
  backend/        # FastAPI API、排盘引擎和 Agent 编排
  docs/           # 架构决策和接口文档
  plan.md         # 产品与开发计划
  docker-compose.yml
  docker-compose.dev.yml
  .env.example
```

## 架构边界

- 排盘引擎是确定性模块：负责历法转换、四柱和派生数据，并记录计算规则与版本。
- Agent 只消费已验证的命盘 JSON、规则上下文和用户问题，负责解释；它不能修改或重新推算四柱。
- REST 用于命盘和会话资源；分析、聊天为 SSE 预留流式接口。
- 出生时间、地点等敏感信息不应写入普通日志。详见 [架构 ADR](docs/adr/0001-architecture.md)。

## 常用命令

```powershell
docker compose -f docker-compose.dev.yml logs -f frontend backend
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml down
```

`docker compose -f docker-compose.dev.yml down -v` 会删除本地 PostgreSQL 数据卷，仅在明确需要清空开发数据时使用。
