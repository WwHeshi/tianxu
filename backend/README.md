# Tianxu Backend

FastAPI 后端，当前只提供确定性排盘，不调用 AI，也不保存出生资料。

## 本地运行

```powershell
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

打开 `http://localhost:8000/docs` 查看 OpenAPI，运行测试：

```powershell
uv run pytest
```

## 接口

- `GET /api/v1/health`
- `POST /api/v1/charts/preview`

请求示例：

```json
{
  "beijing_datetime": "1990-01-01T12:00:00",
  "gender": "male",
  "birthplace": {
    "location_id": "CN:440106"
  },
  "calculation_policy": {
    "version": "v1",
    "year_boundary": "lichun",
    "month_boundary": "solar_terms",
    "day_boundary": "midnight",
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

## v1 计算口径与限制

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
- 输入时间固定解释为北京时间；提供地点时换算为真太阳时，未提供地点时直接使用北京时间。
- 经度修正以东经 120° 为基准，每度相差 4 分钟；均时差按出生日期和时间计算。
- 日柱在所选排盘时间基准的 `00:00` 换日。真太阳时模式下，适配层修正了上游库在
  `23:00-23:59` 仍按次日日干推时干的行为，使时柱与本策略一致。
- 大陆使用行政中心、香港使用官方民政咨询中心、澳门使用 GeoNames 地理区域代表点、
  台湾使用区域边界质心；这些
  代表点都不等于具体医院或住址，同一区域内的实际经度仍可能带来数分钟偏差。
- 坐标记录必须对应所选末级地点；缺失时明确报错，禁止静默回退到上级中心。
- 上游节气表尚未通过独立天文算法和正式 golden dataset 交叉校验，临近节气的案例需要人工复核。
- 性别用于输入摘要、乾造/坤造标签和元辰规则；当前未计算大运、起运和流年。
- 五行 `visible` 统计四个天干和四个地支本气，`hidden_stems` 对藏干做不加权计数；`total` 只是两者相加，不代表旺衰强弱。

跨域来源通过逗号分隔的 `CORS_ORIGINS` 设置，默认允许本机的 `localhost:3000` 和 `127.0.0.1:3000`。
