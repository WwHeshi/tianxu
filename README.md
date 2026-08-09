# Tianxu 八字分析 Agent

这是一个前后端分离的八字分析 Web 应用。系统先用确定性的排盘引擎计算命盘，再由模型基于结构化命盘生成固定章节的分析报告。模型不负责自行计算四柱。

出生信息统一按北京时间输入，并选择中国大陆、香港、澳门或台湾的静态地点。后端根据
地点代表点经度计算经度修正和均时差，将北京时间换算为真太阳时后排盘；代表点属于
区级近似值，出生时间临近换日、时辰或节气边界时需要结合更精确地点复核。

坐标表只接受与末级地点对应的独立坐标，不使用所属城市、地区或省级中心点回退。当前
大陆 2,849 个正式末级行政单位、香港 18 区、澳门 8 个地理区域和台湾 368 个乡镇
市区，共 3,243 个地点均有独立坐标记录，回退记录为 0；以后如果出现缺失，系统会停止
排盘并返回明确错误，待坐标核验补齐后再开放。

大陆地区选择使用民政部国家地名信息库的 2026 快照，只保留正式末级行政单位，不包含
开发区、园区、统计管理区或人为构造的市级层。香港采用民政事务总署 18 区；澳门按
普通用户熟悉的七个传统堂区加路氹城展示为 8 个地理区域，并明确不把它们描述为现行
正式行政区；台湾采用官方乡镇市区代码。三地的 IANA 时区随地点记录保留，但当前界面
仍按统一的北京时间输入。

## 当前状态

当前 MVP 提供确定性排盘、当前大运/流年/流月展示和一次性 AI 报告。第一版不包含知识库、RAG、Agent 工具或对话历史；报告只使用服务端重新计算的命盘和精简后的当前运势上下文。

## Docker 热更新开发（推荐）

需要 Docker Desktop（包含 Docker Compose）。本地默认值已经写在 Compose 中，无需创建 `.env`：

```powershell
docker compose -f docker-compose.dev.yml up --build -d
```

只有端口冲突、访问地址变化或需要替换开发主加密密钥时才需要覆盖配置：

```powershell
Copy-Item .env.example .env
```

复制后修改 `APP_ENCRYPTION_KEY`，再启动服务。该值是服务端加密模型 API 密钥的 32 字节主密钥，不得使用 `NEXT_PUBLIC_*` 前缀，也不得提交真实生产值。修改 `FRONTEND_PORT` 时要同步修改 `CORS_ORIGINS`；修改 `BACKEND_PORT` 时要同步修改 `NEXT_PUBLIC_API_BASE_URL`。

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

Compose 中的数据库密码和主加密密钥默认值只适合本地界面联调，请勿用于生产环境或保存正式密钥。停止热更新环境：

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
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 终端二：前端
cd frontend
npm ci
npm run dev
```

非 Docker 启动前需要提供可用的 `DATABASE_URL` 和 `APP_ENCRYPTION_KEY`。前者用于 PostgreSQL，后者必须是 Base64 编码的 32 字节随机值。模型 API 密钥不放在环境变量中：在页面右上角设置后，后端用 AES-GCM 加密并保存到 PostgreSQL，只向浏览器返回配置状态和末四位。

当前没有用户认证，因此模型设置和报告能力只在 `development`、`local` 和 `test` 环境开放；生产环境由代码强制关闭，直到接入用户身份和授权校验。

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
- 报告模块只消费已验证的命盘 JSON 和精简上下文，负责解释；它不能修改或重新推算四柱。
- 当前报告通过一次 REST 请求生成固定结构 JSON，不保留会话，也不调用工具。
- 出生时间、地点等敏感信息不应写入普通日志。详见 [架构 ADR](docs/adr/0001-architecture.md)。

## 常用命令

```powershell
docker compose -f docker-compose.dev.yml logs -f frontend backend
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml down
```

`docker compose -f docker-compose.dev.yml down -v` 会删除本地 PostgreSQL 数据卷，仅在明确需要清空开发数据时使用。
