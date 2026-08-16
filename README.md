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

- 用户与管理员账户、管理员分配的初始密码和可撤销 Session。
- 确定性排盘，以及当前大运、流年和流月展示。
- 基于结构化命盘与知识库原文的 AI 报告，以及管理员可见的执行链路。
- MingLi-Bench 管理员评测中心，支持5题、单年40题和完整160题评测。
- 管理员知识库，支持上传、搜索、分页浏览、下载和删除 TXT 原始资料。
- 独立 Neo4j 规则图谱、真实关系可视化，以及自动融合 TXT 的整理 Agent。
- 面向所有登录用户的多轮命理对话，可创建普通会话，也可从排盘结果绑定命盘后连续追问。

报告与评测共用通用工具调用执行器，并分别通过显式白名单获得工具权限；通用执行器还支持一次注册完整的 Agent 能力包，由能力包成套提供动态提示词、工具和最终输出处理。报告 Agent 与评测 Agent 都注册 `KnowledgeCapability` 和 `RuleGraphReadCapability`，自动获得当前书目、知识库搜索阅读，以及规则图谱关键词搜索和自由只读 Cypher 查询。

知识库采用动态全文阅读方式，不预先建立固定切片：每次 Agent 运行只把当前书目目录、搜索命中附近的少量上下文，以及 Agent 主动翻页读取的原文发送给模型，不会把整本 TXT 放入提示词。当前尚未引入 BM25、向量检索或 Embedding；后续方案见 [Agent 与知识库建设计划](docs/agent-knowledge-base-plan.md)。

## 多轮命理对话

所有登录用户都可以从排盘页右上角进入 `/chat`。命盘生成后还可以点击“就此命盘提问”，
后端会创建只属于当前用户的会话，并在数据库中绑定原始排盘输入；完整出生资料不会放进 URL。
绑定命盘的 Agent 只能使用服务端固定的性别和真太阳出生时间调用排盘与运势工具，普通会话则
用于咨询一般命理知识，不会猜测出生资料。

对话在 PostgreSQL 的 `agent_conversations` 和 `agent_conversation_messages` 中保存会话标题、
可选排盘输入，以及规范化的 `user` / `assistant` 正文。每次追问会重新读取既有正文历史，
并重新注册知识库和只读规则图谱能力；因此 Neo4j 查询始终读取真实图谱，知识库目录和 TXT
内容也以当前数据库为准。回答通过 NDJSON 实时推送，工具调用期间会显示当前处理状态。
普通用户的回答不保存模型原始请求、响应或工具轨迹；管理员自己的回答会附带一份紧凑执行
轨迹，可从回答下方打开格式化的 Agent 执行链路。

## 管理员知识库

管理员可从排盘页右上角进入 `/admin/knowledge`。普通用户不显示入口，所有知识库 API 仍由后端管理员依赖强制校验，不能通过直接访问地址绕过权限。

每份资料只在 PostgreSQL 的 `knowledge_documents` 表中保存一次：`file_data` 使用 `BYTEA` 保留上传时的原始字节，另外记录书名、原文件名、编码、大小、SHA-256 和上传时间。数据库不重复保存解码正文；浏览时后端根据已识别编码实时解码并按字符分页返回，下载时直接返回原始字节。

生成报告时，后端从当前资料生成精简书目，并为本次 Agent 运行创建只读知识会话。`search_knowledge` 精确搜索多个关键词并返回命中附近上下文，`read_knowledge` 使用不可伪造的临时游标读取原文及前后页。cursor 只用于本次 Agent 运行中的阅读定位和翻页，报告结束后即失效，不进入最终报告响应。

上传仅接受最大10MB的 `.txt` 文件。系统识别带 BOM 的 UTF-8、UTF-16 LE/BE，并在无 BOM 时严格尝试 UTF-8 和 GB18030；空文件、重复文件和包含异常二进制控制字符的内容会被拒绝。上传和删除会写入现有管理员审计日志。

## 规则图谱存储

规则图谱使用独立的 Neo4j Community 服务。TXT 完整原文仍只保存在 PostgreSQL；Neo4j
用于保存整理 Agent 提取的规则、条件逻辑、概念、结论、来源及其关系。
图数据库启动时只创建 `Rule`、`ConditionGroup`、`Condition`、`Concept`、`Outcome` 和
`Source` 的唯一标识约束，不会写入演示节点或默认规则。规则的多个条件组之间采用 ANY，
每组通过 `REQUIRES` 和 `EXCLUDES` 分别表达必须成立及不得出现的条件，避免把原文中的
“且、或、非”压平成同一种关系。

管理员可从排盘页右上角进入 `/admin/graph`。页面读取并绘制 Neo4j 中的真实节点和关系，
也可以上传 TXT、选择资料并启动自动整理。整理任务按自然段临时分段阅读，但不把切片永久
保存；模型只提交结构化规则，后端自动记录当前原文片段的全局起止位置。`search_rule_graph` 每次直接查询
当前 Neo4j；`query_rule_graph` 可按工具描述中的图谱 Schema 执行自由的只读 Cypher，用于多跳、
路径和聚合读取；`submit_rule_graph` 校验后通过固定 Cypher 在单个事务中立即融合当前段落，写入
结果返回同一 Agent Session；Agent 确认没有遗漏并停止调用工具后才结束本段，因此后续提交
和后续段落都可以搜索到刚写入的规则。自由查询会先由 Neo4j 验证为只读计划，数据库过程、
外部文件和修改语句均被拒绝；Agent 没有删除规则或覆盖既有规则的权限。任务中途失败时，
已经成功提交的段落会保留，失败段不会留下
不完整事务，也不会再请求或校验额外的模型最终回答。提交参数校验失败时，错误会返回
同一 Agent Session 供其修正后再次提交；每个片段的完整 Session 默认最多运行 10 分钟，不限制
工具或修正次数，可通过 `GRAPH_ORGANIZER_SECTION_TIMEOUT_SECONDS` 调整。
整理任务支持安全暂停、继续和失败重试：正在分析时会等待当前片段完成并记录进度后暂停，
继续或重试时沿用原任务，从尚未完成的片段接着执行。
管理员也可以在二次确认后取消任务；当前 Agent Session 会立即终止，但已经成功写入 Neo4j
的内容和历史轨迹会保留。

任务进度保存在 PostgreSQL 的 `graph_organizing_jobs` 表中；每个“段落 × 尝试”
完成或失败后，还会在 `graph_organizing_traces` 中保存紧凑执行轨迹，管理员可从任务卡片进入
与评测共用的调试弹窗，查看各轮模型请求、响应和工具执行。API 密钥及 Authorization 请求头
不会写入轨迹。开发环境数据持久化在 Docker 命名卷 `tianxu-dev_neo4j_dev_data`，普通 Compose 环境使用
`tianxu_neo4j_data`；两者都不进入源码目录或 Git。

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
- Neo4j Browser：<http://localhost:7474>
- Neo4j Bolt：`localhost:7687`

第一次打开前端时，系统会自动进入 `/setup`，引导创建首位管理员并直接登录。创建成功后，
该初始化入口会永久关闭。如果前端无法使用，也可以通过后端命令行完成初始化：

```powershell
docker compose -f docker-compose.dev.yml exec backend uv run python -m app.cli create-admin --username admin --display-name 管理员
```

系统不提供默认密码。后续普通用户和其他管理员都由管理后台创建，可直接使用管理员分配的初始密码登录。

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

非 Docker 启动前需要提供可用的 `DATABASE_URL`、`NEO4J_URI`、`NEO4J_USERNAME`、
`NEO4J_PASSWORD` 和 `APP_ENCRYPTION_KEY`。本地默认 Neo4j 地址是
`bolt://localhost:7687`；后端启动时会验证连接并初始化图谱约束。模型 API 密钥不放在环境
变量中：在页面右上角设置后，后端用 AES-GCM 加密并保存到 PostgreSQL，只向浏览器返回
配置状态和末四位。

账户密码使用 Argon2id 哈希，登录状态通过 HttpOnly Session Cookie 和 PostgreSQL 管理。普通用户可排盘和生成报告；只有管理员可以管理用户、模型 API、模型评测和知识库资料。生产环境还会校验携带 Session 的写请求来源，并要求 Cookie 使用 HTTPS。

## 目录约定

```text
tianxu/
  frontend/       # Next.js + TypeScript 用户界面
  backend/        # FastAPI API、排盘引擎和 Agent 编排
  docs/           # 架构决策和接口文档
  external/       # 本地固定的第三方评测数据
  docker-compose.yml
  docker-compose.dev.yml
  .env.example
```

## 架构边界

- 排盘引擎是确定性模块：负责历法转换、四柱和派生数据，并记录计算规则与版本。
- 报告模块只消费已验证的命盘 JSON 和精简上下文，负责解释；它不能修改或重新推算四柱。
- 报告通过一次 REST 请求生成固定结构 JSON，本身不保留会话；多轮对话由独立的用户会话表保存规范化正文。Agent 只能调用显式允许的确定性排盘、运势、知识库和只读规则图谱工具。
- `AgentCapability` 是通用运行时扩展边界：新 Agent 只需注册一次能力实例，执行器会自动合并该能力的提示词、工具和输出校验；每次请求使用独立实例，不共享游标等运行状态。
- 管理员知识库的完整浏览接口不向普通用户开放；知识库工具读取的原文只进入本次模型上下文，不进入最终报告响应。
- 评测题目与标签只从固定的 `data_tianxu.json` 读取；评测 Agent 另外使用评测开始时载入的知识库快照辅助作答。
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

`docker compose -f docker-compose.dev.yml down -v` 会同时删除本地 PostgreSQL 和 Neo4j
数据卷，仅在明确需要清空全部开发数据时使用。
