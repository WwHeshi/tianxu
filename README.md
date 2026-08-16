# Tianxu 天序

一个将确定性八字排盘、知识检索与 AI Agent 结合的全栈命理分析平台。

Tianxu 先由后端排盘引擎完成历法转换、真太阳时修正、四柱及运势计算，再把经过验证的
结构化命盘交给 Agent 解读。模型负责检索资料、查询规则图谱并组织分析，不自行推算或修改四柱。

> 项目目前处于 MVP 阶段，适合本地部署、功能验证和继续开发。排盘结果及 AI 内容仅供研究与参考。

## 项目截图

以下位置已为项目截图预留。建议保持文件名不变，将图片放入
<code>docs/screenshots/</code> 后，把对应单元格替换为图片标签。

| 排盘与运势 | AI 分析报告 |
| :---: | :---: |
| **截图待补充**<br><code>docs/screenshots/chart-overview.png</code> | **截图待补充**<br><code>docs/screenshots/ai-report.png</code> |
| **多轮命理对话** | **规则图谱管理** |
| **截图待补充**<br><code>docs/screenshots/chat.png</code> | **截图待补充**<br><code>docs/screenshots/rule-graph.png</code> |

添加图片时可使用：

~~~html
<img src="./docs/screenshots/chart-overview.png" alt="Tianxu 排盘与运势页面" width="100%">
~~~

## 核心功能

### 用户功能

- **确定性排盘**：支持公历、农历输入，展示四柱、藏干、十神、神煞、五行、大运、流年和流月。
- **真太阳时换算**：按地点代表点经度和均时差，将北京时间转换为真太阳时后排盘。
- **AI 分析报告**：Agent 基于结构化命盘、知识库原文和规则图谱生成固定章节报告。
- **多轮命理对话**：支持普通命理咨询，也可以绑定已有命盘继续追问；出生资料只保存在服务端。
- **账户与会话**：提供登录、修改密码和可撤销的 HttpOnly Session。

### 管理员功能

- **用户管理**：创建用户、分配初始密码、重置密码、调整权限及撤销会话。
- **模型设置**：在管理界面保存并测试兼容 OpenAI 协议的模型配置。
- **知识库**：上传、搜索、分页浏览、下载和删除 TXT 原始资料。
- **规则图谱**：使用 Neo4j 保存命理规则、条件、概念、结论和来源，并可视化真实关系。
- **图谱整理 Agent**：从知识库原文中提取和融合规则，支持暂停、继续、重试、取消及执行轨迹查看。
- **模型评测**：运行 MingLi-Bench 快速 5 题、单年 40 题或完整 160 题评测。
- **执行链路**：管理员可查看报告、对话、评测及图谱整理过程中的紧凑 Agent 轨迹。

## 工作流程

~~~mermaid
flowchart LR
    A[出生信息] --> B[地点与真太阳时换算]
    B --> C[确定性排盘引擎]
    C --> D[结构化命盘]
    D --> E[报告或对话 Agent]
    F[PostgreSQL 知识库] --> E
    G[Neo4j 规则图谱] --> E
    H[确定性运势工具] --> E
    E --> I[分析报告或多轮回答]
~~~

排盘引擎与 Agent 之间保持明确边界：排盘引擎负责计算，Agent 只消费已验证的数据并调用
显式授权的工具。报告、对话、评测和图谱整理共用通用工具调用运行时，但各自拥有独立的
工具白名单。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Next.js 15、React 19、TypeScript、React Force Graph |
| 后端 | Python 3.12、FastAPI、SQLAlchemy、Alembic |
| 排盘 | lunar-python、项目内固定规则与测试 |
| Agent | OpenAI Responses / Chat Completions 兼容接口、能力包与工具调用 |
| 数据库 | PostgreSQL 16 |
| 规则图谱 | Neo4j 5 Community |
| 本地检索 | bge-base-zh-v1.5、ONNX Runtime、BM25 与 RRF |
| 部署 | Docker Compose |

## 快速开始

### 1. 准备环境

推荐安装：

- Docker Desktop（包含 Docker Compose）
- Git

后端镜像不会在构建时联网下载向量模型。首次启动前，请确认以下文件存在：

~~~text
external/models/bge-base-zh-v1.5/
  tokenizer.json
  onnx/model.onnx
~~~

模型来源为 <code>Xenova/bge-base-zh-v1.5</code>，项目当前固定使用修订
<code>71e50dc531959f9e04ebf190ea25b00261a0a186</code>。模型目录只保存在本机，
已由 <code>.gitignore</code> 排除。

### 2. 启动开发环境

~~~powershell
docker compose -f docker-compose.dev.yml up --build -d
~~~

本地开发配置已有可直接使用的默认值，通常无需创建 <code>.env</code>。启动后访问：

| 服务 | 地址 |
| --- | --- |
| Web 前端 | <http://localhost:3000> |
| FastAPI 文档 | <http://localhost:8000/docs> |
| 后端健康检查 | <http://localhost:8000/api/v1/health> |
| Neo4j Browser | <http://localhost:7474> |
| PostgreSQL | <code>localhost:5432</code> |
| Neo4j Bolt | <code>localhost:7687</code> |

第一次打开前端会进入 <code>/setup</code>，用于创建首位管理员并直接登录。系统没有默认
管理员密码；初始化成功后，该入口会永久关闭。

如果前端暂时不可用，可以通过命令行初始化：

~~~powershell
docker compose -f docker-compose.dev.yml exec backend uv run python -m app.cli create-admin --username admin --display-name 管理员
~~~

登录后，在页面右上角的模型设置中填写 API 地址、模型名称和 API Key。API Key 不通过
前端环境变量注入，而是由后端加密保存到 PostgreSQL。

### 3. 查看状态与日志

~~~powershell
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml logs -f frontend backend
~~~

停止开发环境：

~~~powershell
docker compose -f docker-compose.dev.yml down
~~~

普通 Compose 配置可通过以下命令启动接近生产构建的环境：

~~~powershell
docker compose up --build -d
~~~

该配置不会挂载源码、虚拟环境或 <code>node_modules</code>。日常开发仍推荐使用
<code>docker-compose.dev.yml</code>。

## 开发配置

只有在端口冲突、访问地址变化或需要更换开发主加密密钥时，才需要复制环境变量模板：

~~~powershell
Copy-Item .env.example .env
~~~

常用配置包括：

| 变量 | 用途 |
| --- | --- |
| <code>FRONTEND_PORT</code> | 前端映射端口 |
| <code>BACKEND_PORT</code> | 后端映射端口 |
| <code>NEXT_PUBLIC_API_BASE_URL</code> | 浏览器访问后端的地址 |
| <code>CORS_ORIGINS</code> | 允许携带 Session 的前端来源 |
| <code>APP_ENCRYPTION_KEY</code> | 加密模型 API Key 的 32 字节主密钥 |
| <code>NEO4J_*</code> | Neo4j 连接和端口设置 |
| <code>RULE_GRAPH_EMBEDDING_ENABLED</code> | 是否启用规则图谱向量召回 |

修改 <code>FRONTEND_PORT</code> 时需要同步调整 <code>CORS_ORIGINS</code>；修改
<code>BACKEND_PORT</code> 时需要同步调整 <code>NEXT_PUBLIC_API_BASE_URL</code>。
Compose 中的默认密码和主密钥只适合本地开发，不能用于生产环境。

开发环境已启用后端自动重载和前端 Fast Refresh。修改依赖文件或 Dockerfile 后需要重新构建镜像：

~~~powershell
docker compose -f docker-compose.dev.yml up --build -d
~~~

## 不使用 Docker 启动

本地运行需要 Python 3.12+、uv、Node.js 20+，以及可用的 PostgreSQL 和 Neo4j。
请先按照 <code>.env.example</code> 提供数据库、图数据库、跨域和加密配置。

后端：

~~~powershell
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
~~~

前端（另开终端）：

~~~powershell
cd frontend
npm ci
npm run dev
~~~

## 项目结构

~~~text
tianxu/
├─ frontend/                   Next.js 用户界面
├─ backend/                    FastAPI API、排盘引擎和 Agent 编排
│  ├─ app/                     后端业务代码
│  ├─ alembic/                 PostgreSQL 数据库迁移
│  └─ tests/                   后端测试
├─ docs/
│  └─ screenshots/             README 项目截图
├─ external/
│  ├─ MingLi-Bench/            固定评测数据子集
│  └─ models/                  本地向量模型（不提交到 Git）
├─ docker-compose.dev.yml      热更新开发环境
├─ docker-compose.yml          普通 Compose 环境
└─ .env.example               环境变量模板
~~~

更详细的模块说明：

- [后端说明](backend/README.md)
- [前端说明](frontend/README.md)
- [出生地点与经纬度数据](backend/app/bazi/data/README.md)
- [MingLi-Bench 数据子集](external/MingLi-Bench/README.md)

## 排盘口径与数据说明

- 出生时间统一按北京时间输入；选择地点时，后端按地点经度和均时差换算真太阳时。
- 真太阳时计算公式为：北京时间 + 4 ×（地点经度 − 120°）分钟 + 均时差。
- 当前提供中国大陆、香港、澳门和台湾共 3,243 个静态末级地点，缺少独立坐标时会明确拒绝排盘。
- 日柱采用 23:00 子初换日；年份按立春分界，月份按节气分界。
- 大运顺逆、起运、流年和流月均由固定版本规则计算，并随响应返回引擎与口径版本。
- 地点坐标是区县或地区代表点，不等同于医院或具体住址；临近换日、时辰或节气边界时应使用更精确地点复核。
- AI 报告不会覆盖排盘结果，知识库和图谱内容也只作为解释依据。

具体数据来源、地区口径和许可证见
[地点数据说明](backend/app/bazi/data/README.md)。

## 数据与安全

- 密码使用 Argon2id 哈希，登录状态使用 HttpOnly Session Cookie。
- 模型 API Key 使用 AES-GCM 加密后存入 PostgreSQL，浏览器只读取配置状态和末四位。
- 普通用户不能访问管理员用户、知识库、评测和图谱管理接口。
- TXT 原文保存在 PostgreSQL；Neo4j 只保存抽取后的规则、条件、概念、结论和来源关系。
- 知识库按需搜索和分页读取，不会把整本资料一次性放入模型上下文。
- 生产环境应使用独立强密钥、HTTPS、安全 Cookie 和严格的 <code>CORS_ORIGINS</code>。

## 常用检查命令

~~~powershell
# 后端
cd backend
uv run ruff check app tests
uv run pytest

# 前端
cd ../frontend
npm run lint
npm run build
~~~

> <code>docker compose -f docker-compose.dev.yml down -v</code> 会删除本地 PostgreSQL 和
> Neo4j 数据卷。仅在明确需要清空全部开发数据时使用。
