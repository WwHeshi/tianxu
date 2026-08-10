# Tianxu Backend

FastAPI 后端，提供确定性排盘、加密模型设置和一次性结构化报告。报告请求不会持久化出生资料。

## 本地运行

```powershell
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

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

账户使用 Argon2id 密码哈希和 PostgreSQL 持久化的可吊销 Session，浏览器仅保存
HttpOnly Cookie。普通用户可以排盘和生成报告；管理员额外负责用户与模型设置。
模型 API 密钥使用 `APP_ENCRYPTION_KEY`（Base64 编码的 32 字节
主密钥）进行 AES-GCM 加密；数据库只保存密文、末四位和模型连接元数据，GET 响应不返回
明文密钥。

连接测试使用当前表单的协议、模型、Base URL 和新密钥；未输入新密钥但已有保存配置时，
使用已保存密钥。测试会向所选协议发送一条最小生成请求以验证真实接口、鉴权和模型访问，
可能产生极少量 token，但不会生成报告或改写数据库。

报告接口接收与排盘相同的出生输入，并在服务端重新排盘。发送给模型的上下文不包含完整
大运时间线，只保留当前大运、流年和流月；模型以一次 Responses API 或兼容的 Chat
Completions 调用返回八个固定章节。当前没有知识库、RAG、工具调用、引文或对话历史。

管理员生成报告时同时返回 `debug_trace`，用于展示排盘、上下文裁剪、提示词组装、模型调用和
输出校验链路。普通用户的成功和失败响应都不包含执行链路。调试快照包含提示词与命盘上下文，
但不包含 API 密钥或 Authorization 请求头；原始模型响应不应写入普通日志。

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
  按年干、日干采用丙至癸的八干映射，不为甲、乙追加规则；元辰同时使用性别和年干阴阳，
  `other` 不推断元辰。扩展规则参考
  [`chxb/shensha@5b90110e55fe`](https://github.com/chxb/shensha/tree/5b90110e55fe)（MIT），
  并在本项目中独立实现、以测试固定。
- 运势周期采用固定的 `v1` 口径，响应通过 `engine.fortune_policy_version` 返回版本。阳男
  阴女顺行、阴男阳女逆行；起运按出生时刻到相邻节的精确分钟数折算，4320 分钟折 1 年。
  大运起始年份按交运时刻所属的立春流年标记，流年以立春换年，流月依次以立春、惊蛰、
  清明等十二个节换月。年龄采用流年虚岁，以出生时刻所属的立春流年为 1 岁，之后每个
  立春流年递增 1 岁；`gender=other` 时不推断顺逆，`fortune_cycles` 返回 `null`。年份标签
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
`data.json` 加载，答案只在模型返回后由独立评分步骤读取；数据库仅保存评测任务、
模型输出、计分与运行元数据，不导入整套基础数据集。

本地开发默认查找仓库根目录下的
`external/MingLi-Bench/data/data.json`。也可通过 `MINGLI_BENCH_DATA_PATH` 指定固定文件；
Docker Compose 会将该数据目录只读挂载到 `/app/evaluation_data`。启动评测前，后端会校验
UTF-8 JSON、160 道题、四个选项、全部标签以及题目中不存在“正确答案”标记，并记录文件
SHA-256。评测使用题面钟表时间、不作地点或真太阳时修正，四柱使用天序 v2 口径。

跨域来源通过逗号分隔的 `CORS_ORIGINS` 设置，默认允许本机的 `localhost:3000` 和 `127.0.0.1:3000`。
