# Tianxu 八字分析 Agent

这是一个前后端分离的八字分析 Web 应用。系统先用确定性的排盘引擎计算命盘，再由 Agent 基于结构化命盘生成解释和回答追问。模型不负责自行计算四柱。

出生信息统一按北京时间输入，并选择中国大陆正式行政区。后端根据行政中心经度计算经度修正和均时差，将北京时间换算为真太阳时后排盘；行政中心坐标属于近似值，出生时间临近换日、时辰或节气边界时需要结合更精确地点复核。

坐标表只接受与末级行政单位对应的独立坐标，不使用所属城市、地区或省级中心点回退。当前 2,849 个正式末级行政单位均有独立坐标记录，回退记录为 0；后续如果出现缺失，系统会停止排盘并返回明确错误，待坐标核验补齐后再开放。

地区选择使用民政部国家地名信息库的 2026 快照，只保留正式末级行政单位，不包含开发区、园区、统计管理区或人为构造的市级层。香港、澳门、台湾待接入各自权威数据后开放。

## 当前状态

项目正在按 MVP 分阶段搭建。`frontend/`、`backend/` 和排盘规则会逐步完善；根目录已经提供统一的本地开发配置。

## Docker 热更新开发（推荐）

需要 Docker Desktop（包含 Docker Compose）。本地默认值已经写在 Compose 中，无需创建 `.env`：

```powershell
docker compose -f docker-compose.dev.yml up --build
```

只有端口冲突或访问地址变化时才需要覆盖配置：

```powershell
Copy-Item .env.example .env
```

复制后直接使用默认内容即可。修改 `FRONTEND_PORT` 时要同步修改 `CORS_ORIGINS`；修改 `BACKEND_PORT` 时要同步修改 `NEXT_PUBLIC_API_BASE_URL`。

启动后访问：

- 前端：<http://localhost:3000>
- FastAPI 文档：<http://localhost:8000/docs>
- 后端健康检查：<http://localhost:8000/api/v1/health>
- PostgreSQL：`localhost:5432`

开发配置使用源码目录挂载，不需要 `--watch`：

- 修改 `backend/app/` 后，Uvicorn 会自动重载。
- 修改 `frontend/app/`、`components/`、`lib/` 或 `public/` 后，会触发 Next.js Fast Refresh。
- `node_modules`、`.venv` 和 `.next` 保留在 Linux 容器中，不会与 Windows 主机目录互相覆盖。
- Windows Docker Desktop 下已启用轮询监听，避免文件事件丢失。

修改 `pyproject.toml`、`uv.lock`、`package.json`、`package-lock.json` 或 `next.config.ts` 后，需要重新执行 `docker compose -f docker-compose.dev.yml up --build`。如果新增前端顶层源码目录，也需要将该目录加入 `frontend.volumes`。

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

当前后端尚未使用数据库和模型服务，相应连接信息由 Compose 的开发默认值占位，不需要手动填写。进入持久化和 Agent 阶段后，再单独增加真实数据库及模型密钥配置。

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
