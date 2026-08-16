# Tianxu Backend

FastAPI 后端，提供确定性排盘、加密模型设置、一次性结构化报告和 Neo4j 规则图谱存储。
报告请求不会持久化出生资料。

排盘引擎另外通过内部工具 `calculate_bazi_chart` 暴露一个最小化、地点无关的调用边界：

```json
{
  "gender": "male",
  "true_solar_datetime": "1974-04-28T15:45:32"
}
```

调用方必须先完成地点、时区和真太阳时换算；工具不会再次校时。它仍复用现有确定性排盘
引擎，但只计算和返回原局，不生成大运、流年、流月时间线。四柱、节气边界和子初换日
口径与原排盘接口一致。`gender` 仍用于命造类型和部分神煞计算，因此为必填字段。
工具结果就是直接发送给模型的 Observation，顶层直接包含 `年柱`、`月柱`、`日柱`、`时柱`，
不再经过二次上下文转换。每柱使用常见排盘字段 `主星`、`天干`、`地支`、`藏干`、`星运`、
`自坐`、`空亡`、`纳音`、`神煞`；每个藏干内部携带对应的 `副星`。日主直接读取
`日柱.天干`。工具输出的阴阳值使用 `阳`、`阴`；普通排盘预览接口仍保持原结构。

## 本地运行

```powershell
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

后端启动前需要有可用的 Neo4j，默认连接为 `bolt://localhost:7687`、数据库 `neo4j`。
应用启动时会验证连接并创建图谱唯一约束，不会写入示例节点。Docker 开发环境已经包含
Neo4j 服务和独立持久化卷。

首次打开前端时会通过一次性 `/api/v1/auth/bootstrap` 接口创建首位管理员。命令行
`uv run python -m app.cli create-admin --username admin --display-name 管理员` 可作为无前端时的备用方式。

打开 `http://localhost:8000/docs` 查看 OpenAPI，运行测试：

```powershell
uv run pytest
```

## 接口

- `GET /api/v1/health`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/bootstrap-status`
- `POST /api/v1/auth/bootstrap`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/change-password`
- `POST /api/v1/charts/preview`
- `GET /api/v1/model-settings`
- `PUT /api/v1/model-settings`
- `POST /api/v1/model-settings/test`
- `DELETE /api/v1/model-settings`
- `POST /api/v1/reports/generate`
- `GET /api/v1/admin/users`
- `POST /api/v1/admin/users`
- `PATCH /api/v1/admin/users/{user_id}`
- `POST /api/v1/admin/users/{user_id}/reset-password`
- `POST /api/v1/admin/users/{user_id}/revoke-sessions`
- `GET /api/v1/admin/knowledge/documents`
- `POST /api/v1/admin/knowledge/documents`
- `GET /api/v1/admin/knowledge/documents/{document_id}/content`
- `GET /api/v1/admin/knowledge/documents/{document_id}/download`
- `DELETE /api/v1/admin/knowledge/documents/{document_id}`
- `GET /api/v1/admin/graph`
- `GET /api/v1/admin/graph/status`
- `GET /api/v1/admin/graph/jobs`
- `POST /api/v1/admin/graph/jobs`
- `POST /api/v1/admin/graph/jobs/{job_id}/pause`
- `POST /api/v1/admin/graph/jobs/{job_id}/resume`
- `POST /api/v1/admin/graph/jobs/{job_id}/retry`
- `POST /api/v1/admin/graph/jobs/{job_id}/cancel`
- `GET /api/v1/admin/graph/jobs/{job_id}/traces`
- `GET /api/v1/admin/graph/jobs/{job_id}/traces/{trace_id}`

图谱接口只对管理员开放：快照接口直接读取 Neo4j 的真实节点和关系，任务接口把选定 TXT
交给整理 Agent。`search_rule_graph` 每次直接读取当前 Neo4j，并将名称或别名精确匹配、中文
BM25 前 30 条和本地原版 `bge-base-zh-v1.5` 向量前 30 条通过 RRF 融合，最终仍返回前 5 条；
模型使用 ONNX Runtime 在 CPU 本地批量运行，查询侧附加 BGE 中文检索指令；
失败时自动退化为精确匹配和 BM25。规则向量使用进程内增量缓存，新增或变化的规则才会重算，
后端重启时会从真实图谱重新预热。Embedding 文件位于
`../external/models/bge-base-zh-v1.5`；Docker 构建只复制 Embedding，不会联网下载模型，
非量化 FP32 ONNX 由 Xenova 从 BAAI 原始权重转换。模型目录由 `.gitignore` 排除，不使用
Git LFS；新环境需人工放置固定版本 `71e50dc531959f9e04ebf190ea25b00261a0a186` 的
`onnx/model.onnx` 和 `tokenizer.json`。Docker 构建不校验模型哈希。
`query_rule_graph` 根据工具描述
内置的图谱 Schema 执行自由的只读 Cypher，可用于多跳、路径和聚合查询；每段提取结果通过
`submit_rule_graph` 提交，后端自动附加当前片段范围后，立即通过固定 Cypher 在单个事务中融合
该批规则，并把结果返回同一 Session。Agent 确认没有遗漏并停止调用工具后才结束本段；同一
Session 可以继续搜索或分批提交，后续段落也能查询到已写入的新规则。任务中途失败时
已成功提交的段落会保留，失败段的事务不会部分写入。自由查询会先由 Neo4j 验证为只读计划，
数据库过程、外部文件和修改语句均被拒绝；Agent 不会自动删除或覆盖已有规则。TXT 原始文件
继续保存在 PostgreSQL，临时阅读分段不会落库；Neo4j 只
保存结构化规则、关系、来源编号和可回查的原文片段范围。提交成功后不再请求或校验模型最终
回答。提交参数校验失败会作为工具结果返回同一 Agent Session，修正后可以继续提交；
每段 Session 默认最多运行 600 秒，不限制工具和修正次数，可用
`GRAPH_ORGANIZER_SECTION_TIMEOUT_SECONDS` 覆盖。每个“段落 × 尝试”完成或失败后会保存一条紧凑执行轨迹，供管理员通过与
评测共用的调试界面查看模型请求、响应和工具执行；API 密钥及 Authorization 请求头不会
写入轨迹，迁移前的历史整理任务也不会补生成轨迹。

规则合并必须通过 `existing_rule_id` 明确指定。编号不存在，或者编号留空但候选名称或别名与
现有规则完全相同时，`submit_rule_graph` 会返回可修正错误；Agent 必须明确填写已有编号，或
使用可区分的新名称重新提交。BM25 和向量检索只用于提供候选，不会触发自动语义合并。

排队任务可以立即暂停；正在分析的任务会在当前段落完成并记录进度后暂停。继续和失败重试
沿用原任务，从 `current_offset` 指向的未完成段落接着执行，并保留已有图谱、统计和轨迹。
取消会终止当前 Agent Session 并释放后台 Worker；已经成功写入 Neo4j 的事务和历史轨迹保留，
已取消任务不能继续或重试。

图谱节点类型固定为 `Rule`、`ConditionGroup`、`Condition`、`Concept`、`Outcome` 和
`Source`。条件组之间是 ANY，组内 `REQUIRES` 条件全部成立且 `EXCLUDES` 条件均不出现；
规则还可通过 `REFINES`、`EXCEPTION_TO` 和 `CONTRADICTS` 连接既有规则。
Agent 只能填写固定提交结构，不能创造新的节点标签或关系类型。

账户使用 Argon2id 密码哈希和 PostgreSQL 持久化的可吊销 Session，浏览器仅保存
HttpOnly Cookie。普通用户可以排盘和生成报告；管理员额外负责用户、模型设置和知识库资料。
模型 API 密钥使用 `APP_ENCRYPTION_KEY`（Base64 编码的 32 字节
主密钥）进行 AES-GCM 加密；数据库只保存密文、末四位和模型连接元数据，GET 响应不返回
明文密钥。

连接测试使用当前表单的协议、模型、Base URL 和新密钥；未输入新密钥但已有保存配置时，
使用已保存密钥。测试会向所选协议发送一条最小生成请求以验证真实接口、鉴权和模型访问，
可能产生极少量 token，但不会生成报告或改写数据库。

报告接口接收与排盘相同的出生输入，后端先校验并换算真太阳时间，再运行共享工具调用循环：
模型可以按需调用 `calculate_bazi_chart`，也可以直接返回最终报告；调用工具时，后端严格核对
参数并执行确定性排盘，随后直接把工具原局结果回传。报告用户提示词只提供性别、真太阳出生
时间和北京时间报告基准时间，不再预先写入当前大运、流年和流月；报告 Agent 需要当前运势时
调用 `calculate_fortune_at` 获取。知识库有资料时，系统提示词附加动态书目，Agent 可以通过
`search_knowledge` 搜索多个精确短语，再用 `read_knowledge` 按临时游标读取原文前后页。系统
不建立固定切片或向量索引，只向模型返回命中上下文和实际读取页面；cursor 仅用于本次运行的阅读定位和翻页。
报告 Agent 同时注册 `RuleGraphReadCapability`，可用 `search_rule_graph` 查询结构化规则，或用
`query_rule_graph` 按工具描述中的图谱 Schema 执行自由的只读 Cypher；它没有图谱写入工具。
当前仍不保留对话历史。

知识库通过通用 `AgentCapability` 接口接入执行器。能力实例一次注册后，执行器自动附加动态
提示词、合并能力工具，并在模型给出最终答案后运行能力自己的校验器。`KnowledgeCapability`
同时封装书目、全文搜索和游标阅读；实例按请求创建，不能跨 Agent
运行共享游标。`RuleGraphReadCapability` 同时封装实时规则搜索和只读 Cypher；实例按请求创建，
共享 Neo4j Driver 但不保存图谱快照。以后新增问答或资料研究 Agent 时，只需注册所需能力包。

管理员生成报告时同时返回 `debug_trace`，用于展示模型请求、工具执行、工具结果、最终答案和
输出校验链路，并按实际响应轮数保存每次模型请求快照。普通用户的成功和失败响应都不包含执行
链路。调试快照不包含 API 密钥、Authorization 请求头或模型内部推理文本；原始模型响应不应
写入普通日志。

设置中的 Base URL 只填写版本根路径，后端按协议追加端点。例如智谱 Chat Completions
填写 `https://open.bigmodel.cn/api/paas/v4`，后端会追加 `/chat/completions`。

请求示例：

```json
{
  "beijing_datetime": "1990-01-01T12:00:00",
  "gender": "male",
  "birthplace": {
    "location_id": "CN:440106"
  },
  "calculation_policy": {
    "version": "v2",
    "year_boundary": "lichun",
    "month_boundary": "solar_terms",
    "day_boundary": "zi_hour_start",
    "time_basis": "beijing_standard_time",
    "true_solar_time": true
  }
}
```

`calendar_type` 默认为 `"solar"`，因此上面的公历请求可以省略它；公历请求禁止同时
提供 `lunar_date`。完整农历请求示例：

```json
{
  "beijing_datetime": "2023-03-22T12:00:00",
  "calendar_type": "lunar",
  "lunar_date": {
    "year": 2023,
    "month": 2,
    "day": 1,
    "is_leap_month": true
  },
  "gender": "female",
  "birthplace": {
    "location_id": "CN:440106"
  }
}
```

农历模式必须同时提交 `lunar_date` 和 `beijing_datetime`。其中 `lunar_date` 是用户选择
的农历年月日，`is_leap_month=true` 表示闰月；`beijing_datetime` 的年月日必须填写该
农历日期换算后的公历日期，时分秒仍是用户输入的北京时间。后端会使用
`lunar-python` 独立换算并校验两者完全一致，不存在的日期、不存在的闰月或日期不一致
都会返回 `422`。响应的 `normalized_input` 会原样回传 `calendar_type` 和经过校验的
`lunar_date`；公历模式下 `lunar_date` 为 `null`。

`birthplace` 和 `calculation_policy` 均可省略。响应的核心结构为：

```text
chart
  calendar
  pillars
    year / month / day / hour
      heavenly_stem / earthly_branch / hidden_stems
      growth_stage / self_growth_stage / xun_kong
      na_yin / shen_sha
  day_master
  element_distribution
  fortune_cycles
    direction / start_offset / start_solar_datetime
    big_luck_periods
      years
        months
normalized_input
calculation_policy
solar_time_adjustment
engine
warnings
limitations
```

`beijing_datetime` 是用户看到的北京时间，香港、澳门、台湾也使用同一输入口径。
提供 `birthplace` 时只提交静态快照中的 `location_id`，客户端不提交名称、层级、经纬度
或时区；此时 `true_solar_time` 必须为 `true`。响应会返回后端核验后的 `region_code`、
IANA `timezone` 和真实可变层级 `division_path`。地点时区仅作为元数据，本版不用于时间
换算。

不提供 `birthplace`（省略或传 `null`）时，`true_solar_time` 默认为且必须为 `false`。
系统按输入的北京时间直接排盘，`normalized_input.birthplace` 和
`solar_time_adjustment` 均为 `null`。

提供出生地点时，真太阳时按以下口径计算：

```text
真太阳时 = 北京时间 + 4 ×（地点代表点经度 - 120°）分钟 + 均时差
```

`solar_time_adjustment` 会返回采用的经度、东经 120° 标准经线、经度修正、均时差、总修正、地点精度和坐标匹配方式，`normalized_input` 同时保留北京时间和换算后的真太阳时。

坐标快照禁止使用所属城市、地区或省级中心点作为回退值。后端遇到缺少独立坐标或标记为回退的记录时会拒绝排盘，不会用近似的上级中心坐标继续计算。

当前共开放 3,243 个静态地点：大陆 2,849 个正式末级行政单位、香港 18 区、
澳门 8 个地理区域、台湾 368 个乡镇市区。大陆行政区主数据锁定为
`@aurouscia/china-areas@0.7.0`；香港使用当地政府地图服务；澳门使用 GeoNames
CC BY 4.0 的七个传统堂区加路氹城代表点；台湾使用由官方 `dataset 7441` 派生的
版本化边界镜像。全部记录都有独立代表点，回退记录为 0。
详细来源、代码口径、许可证和生成方法见 `app/bazi/data/README.md`。

## v2 默认计算口径与限制

- 使用 `lunar-python 1.4.8` 计算立春分年、节气分月、四柱、藏干、十神、旬空和纳音。
- `chart.calendar` 始终根据实际用于四柱的排盘时间生成，同时提供公历时间、结构化农历
  日期、农历文本、时支、生肖以及乾造/坤造标签。
- `growth_stage` 表示日主在各柱地支的十二长生状态；`self_growth_stage` 表示每柱天干
  在本柱地支的十二长生状态。
- 神煞采用单一、固定的 `v2` 规则集，共 51 项：核心 33 项加福星贵人、学堂、
  词馆、金神、五鬼、天赦、红艳、天罗、地网、飞刃、血刃、八专、九丑、元辰、童子、
  天厨、十恶大败和孤鸾。响应通过 `engine.shen_sha_policy_version` 返回规则版本；前端只
  展示各柱实际命中的项目。
- `v2` 对存在分歧的扩展规则固定采用以下口径：学堂、词馆按年柱纳音五行查地支；天厨
  按年干、日干采用丙至癸的八干映射，不为甲、乙追加规则；元辰同时使用性别和年干阴阳。
  扩展规则参考
  [`chxb/shensha@5b90110e55fe`](https://github.com/chxb/shensha/tree/5b90110e55fe)（MIT），
  并在本项目中独立实现、以测试固定。
- 运势周期采用固定的 `v1` 口径，响应通过 `engine.fortune_policy_version` 返回版本。阳男
  阴女顺行、阴男阳女逆行；起运按出生时刻到相邻节的精确分钟数折算，4320 分钟折 1 年。
  大运起始年份按交运时刻所属的立春流年标记，流年以立春换年，流月依次以立春、惊蛰、
  清明等十二个节换月。年龄采用流年虚岁，以出生时刻所属的立春流年为 1 岁，之后每个
  立春流年递增 1 岁。年份标签
  只用于排盘展示，每步大运另行返回精确的 `start_solar_datetime` 和
  `end_solar_datetime`。大运、流年、流月均按左闭右开的时间区间求交集，因此交运流年会
  同时出现在相邻两步大运中，但各自只返回实际属于该步大运的月份；交运月也会拆成前后
  两段。流年和流月通过 `segment_start_solar_datetime`、`segment_end_solar_datetime`、
  `transition_phase` 与 `transition` 返回实际有效段及交运时刻，禁止把交运所在流年或流月
  整体归入某一步大运。
- 输入时间固定解释为北京时间；提供地点时换算为真太阳时，未提供地点时直接使用北京时间。
- 经度修正以东经 120° 为基准，每度相差 4 分钟；均时差按出生日期和时间计算。
- 日柱采用流派 1，在所选排盘时间基准的 `23:00`（子初）换日；`23:00-23:59`
  的日柱按次日计算，时干也按次日日干推导。后端与前端统一且仅接受 v2 口径。
- 大陆使用行政中心、香港使用官方民政咨询中心、澳门使用 GeoNames 地理区域代表点、
  台湾使用区域边界质心；这些
  代表点都不等于具体医院或住址，同一区域内的实际经度仍可能带来数分钟偏差。
- 坐标记录必须对应所选末级地点；缺失时明确报错，禁止静默回退到上级中心。
- 上游节气表尚未通过独立天文算法和正式 golden dataset 交叉校验，临近节气的案例需要人工复核。
- 性别用于输入摘要、乾造/坤造标签、元辰规则和大运顺逆。
- 五行 `visible` 统计四个天干和四个地支本气，`hidden_stems` 对藏干做不加权计数；`total` 只是两者相加，不代表旺衰强弱。

## 管理员评测中心

管理员可从前端右上角进入 `/admin/evaluations`，使用当前保存的模型配置运行
MingLi-Bench 的快速 5 题、单年 40 题或完整 160 题评测。题目从只读的本地
`data_tianxu.json` 加载，答案只在模型返回后由独立评分步骤读取；数据库仅保存评测任务、
模型输出、计分与运行元数据，不导入整套基础数据集。

本地开发默认查找仓库根目录下的
`external/MingLi-Bench/data/data_tianxu.json`。该文件从上游 `data.json` 派生，将题目和选项中的
“目前”“现在”“至今”替换为对应赛题年份，不再依赖提示词内的评测年份元数据。也可通过
`MINGLI_BENCH_DATA_PATH` 指定固定文件；
Docker Compose 会将该数据目录只读挂载到 `/app/evaluation_data`。启动评测前，后端会校验
UTF-8 JSON、160 道题、四个选项、全部标签以及题目中不存在“正确答案”标记，并记录文件
SHA-256。评测使用题面钟表时间、不作地点或真太阳时修正，四柱使用天序 v2 口径。

评测 Agent 与报告 Agent 复用同一个通用工具调用执行器，并分别通过显式工具注册表控制
本次运行允许模型调用的工具。用户提示词只用自然文本传入原始
出生资料、性别、作为已归一化排盘时间使用的题面钟表时间、题目和选项；排盘工具说明仅放在
API 的 `tools` 字段中。评测开始时加载一次当前知识库快照，每个题目使用独立的
`KnowledgeCapability`、cursor 会话和 `RuleGraphReadCapability`；知识库资料沿用该次评测的
加载结果，图谱工具每次调用都读取当前 Neo4j。模型可以直接返回 Final，也可以按需调用
`calculate_bazi_chart`、`search_knowledge`、`read_knowledge`、`search_rule_graph` 和
`query_rule_graph`；评测 Agent 没有 `submit_rule_graph`。
工具 Observation 只包含原局，不包含大运、流年或流月。单轮可发起多个工具调用，工具调用
数量和模型响应轮数不设固定上限。后端会累加所有响应轮次和重试的 Token，
并在评测调用链路中按“系统提示词、用户提示词、请求体 N、原始响应 N”的顺序展示实际轮次。
评测路径在数据库中使用紧凑快照：只保存首轮请求、各轮模型响应和工具输入输出，后续累积请求
在读取链路时还原；不持久化固定请求头、HTTP 状态码或与最后一轮重复的顶层请求和响应。

模型历史回传遵循同一条规则：只要后续还会发起模型请求，无论下一轮来自工具结果还是未来的
用户追问，都回传上一轮的 Reason。Responses 协议回传完整 `output`（包括 reasoning、message
和 function call item）；Chat Completions 协议回传 assistant 的 `content`、`tool_calls` 以及
服务商返回的 `reasoning_content`、`reasoning` 或 `thinking`。当前 MVP 尚未实现连续追问；
Responses 在 `store: false` 下会请求 `reasoning.encrypted_content`，以便无状态回传推理状态。
没有工具调用时不会产生下一次请求，但最终响应已经可以通过同一历史提取逻辑供未来会话复用。

`calculate_fortune_at` 已作为通用工具提供给未来有明确指定日期查询需求的 Agent。模型参数
包含性别、真太阳出生时间和北京时间 `as_of_datetime`，不依赖服务端命盘上下文绑定。工具内部复用现有
确定性完整运势计算，出口只保留查询时点命中的大运、流年和流月，范围外查询不会静默钳制到
第一项或最后一项。报告 Agent 已将该工具加入白名单，用于查询报告基准时点；评测 Agent
没有运势查询需求，仍只开放排盘工具。
精简 Observation 的顶层只有“大运、流年、流月”；每柱返回干支、天干十神和地支本气十神，
另外保留大运状态、流年归属年份与虚岁，以及流月交界节气。

数据集 `case_31` 的参考排盘采用农历年柱“丁卯”，天序 v2 按立春分年计算为“戊辰”；
该案例的年柱口径与参考数据不同，解读相关评测结果时需考虑这一差异。

跨域来源通过逗号分隔的 `CORS_ORIGINS` 设置，默认允许本机的 `localhost:3000` 和 `127.0.0.1:3000`。
