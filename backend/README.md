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
    "country_code": "CN",
    "province_code": "440000",
    "province_name": "广东省",
    "city_code": "440100",
    "city_name": "广州市",
    "district_code": "440106",
    "district_name": "天河区"
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

`calculation_policy` 整体可省略。响应的核心结构为：

```text
chart
  pillars
    year / month / day / hour
  day_master
  element_distribution
normalized_input
calculation_policy
solar_time_adjustment
engine
warnings
limitations
```

`beijing_datetime` 是用户看到的北京时间。`birthplace` 使用中国大陆正式行政区代码和名称；直辖市区县、省直辖县级单位或没有县级辖区的城市没有人为中间层，此时 `city_code` 和 `city_name` 为 `null`。后端会校验完整层级与官方名称，客户端不提交经度。

真太阳时按以下口径计算：

```text
真太阳时 = 北京时间 + 4 ×（区县行政中心经度 - 120°）分钟 + 均时差
```

`solar_time_adjustment` 会返回采用的经度、东经 120° 标准经线、经度修正、均时差、总修正、地点精度和坐标匹配方式，`normalized_input` 同时保留北京时间和换算后的真太阳时。

坐标快照禁止使用所属城市、地区或省级中心点作为回退值。后端遇到缺少独立坐标或标记为回退的记录时会拒绝排盘，不会用近似的上级中心坐标继续计算。

行政区主数据锁定为 `@aurouscia/china-areas@0.7.0` 的 2026-05-18 快照，上游为民政部国家地名信息库。当前开放中国大陆 31 个省级地区、2,849 个正式末级行政单位；其中 2,841 条坐标由现有坐标包按正式代码直接命中，8 条由民政部国家地名信息库公开接口独立补齐，回退记录为 0。开发区、园区、统计管理区和虚拟市级层不会进入接口。香港、澳门、台湾待分别接入权威数据后开放。

## v1 计算口径与限制

- 使用 `lunar-python 1.4.8` 计算立春分年、节气分月、四柱、藏干、十神和纳音。
- 输入时间固定解释为北京时间，排盘使用根据出生区县换算后的真太阳时。
- 经度修正以东经 120° 为基准，每度相差 4 分钟；均时差按出生日期和时间计算。
- 日柱在真太阳时 `00:00` 换日。适配层修正了上游库在 `23:00-23:59` 仍按次日日干推时干的行为，使时柱与本策略一致。
- 区县经度取行政中心坐标，不代表具体医院或住址。同一区县内的实际经度仍可能带来数分钟偏差；临近 `00:00`、时辰交界或节气交界的案例必须提示用户复核。
- 坐标记录必须对应所选末级行政单位；缺失时明确报错，禁止静默回退到上级行政中心。
- 上游节气表尚未通过独立天文算法和正式 golden dataset 交叉校验，临近节气的案例需要人工复核。
- 性别仅保留在输入摘要中；当前未计算大运、起运和流年。
- 五行 `visible` 统计四个天干和四个地支本气，`hidden_stems` 对藏干做不加权计数；`total` 只是两者相加，不代表旺衰强弱。

跨域来源通过逗号分隔的 `CORS_ORIGINS` 设置，默认允许本机的 `localhost:3000` 和 `127.0.0.1:3000`。
