# Tianxu 八字分析 Agent

这是一个前后端分离的八字分析 Web 应用。系统先用确定性的排盘引擎计算命盘，再由模型基于结构化命盘生成固定章节的分析报告。模型不负责自行计算四柱。项目同时提供本地账户与管理员权限、模型评测中心，以及用于保存和浏览命理 TXT 原始资料的管理员知识库。

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

当前 MVP 已提供：

- 用户与管理员账户、强制修改临时密码和可撤销 Session。
- 确定性排盘，以及当前大运、流年和流月展示。
- 基于结构化命盘的 AI 报告和管理员可见执行链路。
- MingLi-Bench 管理员评测中心，支持5题、单年40题和完整160题评测。
- 管理员知识库，支持上传、搜索、分页浏览、下载和删除 TXT 原始资料。

报告与评测共用通用工具调用执行器，并分别通过显式白名单获得工具权限；评测 Agent 可按需调用确定性排盘工具，报告 Agent 还可调用 `calculate_fortune_at` 获取报告基准时点的大运、流年和流月。

当前知识库只负责资料存储和人工浏览，不会把文档发送给模型，也尚未实现章节切分、全文检索、引用、RAG 或对话历史。后续方案见 [Agent 与知识库建设计划](docs/agent-knowledge-base-plan.md)。

## 管理员知识库

管理员可从排盘页右上角进入 `/admin/knowledge`。普通用户不显示入口，所有知识库 API 仍由后端管理员依赖强制校验，不能通过直接访问地址绕过权限。

每份资料只在 PostgreSQL 的 `knowledge_documents` 表中保存一次：`file_data` 使用 `BYTEA` 保留上传时的原始字节，另外记录书名、原文件名、编码、大小、SHA-256 和上传时间。数据库不重复保存解码正文；浏览时后端根据已识别编码实时解码并按字符分页返回，下载时直接返回原始字节。

上传仅接受最大10MB的 `.txt` 文件。系统识别带 BOM 的 UTF-8、UTF-16 LE/BE，并在无 BOM 时严格尝试 UTF-8 和 GB18030；空文件、重复文件和包含异常二进制控制字符的内容会被拒绝。上传和删除会写入现有管理员审计日志。

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

第一次打开前端时，系统会自动进入 `/setup`，引导创建首位管理员并直接登录。创建成功后，
该初始化入口会永久关闭。如果前端无法使用，也可以通过后端命令行完成初始化：

```powershell
docker compose -f docker-compose.dev.yml exec backend uv run python -m app.cli create-admin --username admin --display-name 管理员
```

系统不提供默认密码。后续普通用户和其他管理员都由管理后台创建，并在首次登录时强制修改临时密码。

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

账户密码使用 Argon2id 哈希，登录状态通过 HttpOnly Session Cookie 和 PostgreSQL 管理。普通用户可排盘和生成报告；只有管理员可以管理用户、模型 API、模型评测和知识库资料。生产环境还会校验携带 Session 的写请求来源，并要求 Cookie 使用 HTTPS。

## 目录约定

```text
tianxu/
  frontend/       # Next.js + TypeScript 用户界面
  backend/        # FastAPI API、排盘引擎和 Agent 编排
  docs/           # 架构决策和接口文档
  external/       # 本地固定的第三方评测数据
  plan.md         # 产品与开发计划
  docker-compose.yml
  docker-compose.dev.yml
  .env.example
```

## 架构边界

- 排盘引擎是确定性模块：负责历法转换、四柱和派生数据，并记录计算规则与版本。
- 报告模块只消费已验证的命盘 JSON 和精简上下文，负责解释；它不能修改或重新推算四柱。
- 报告通过一次 REST 请求生成固定结构 JSON，不保留对话会话；Agent 只能调用显式允许的确定性排盘与运势工具。
- 评测数据只从固定的 `data_tianxu.json` 读取，知识库文档当前不参与报告或评测提示词。
- 出生时间、地点等敏感信息不应写入普通日志。详见 [架构 ADR](docs/adr/0001-architecture.md)。

## 常用命令

```powershell
docker compose -f docker-compose.dev.yml logs -f frontend backend
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml down

cd backend
uv run ruff check app tests
uv run pytest

cd ../frontend
npm run lint
npm run build
```

`docker compose -f docker-compose.dev.yml down -v` 会删除本地 PostgreSQL 数据卷，仅在明确需要清空开发数据时使用。
