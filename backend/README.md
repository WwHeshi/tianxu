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
  "local_datetime": "1990-01-01T12:00:00",
  "timezone": "Asia/Shanghai",
  "gender": "male",
  "longitude": 121.4737,
  "calculation_policy": {
    "version": "v1",
    "year_boundary": "lichun",
    "month_boundary": "solar_terms",
    "day_boundary": "midnight",
    "time_basis": "local_civil_time",
    "true_solar_time": false
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
engine
warnings
limitations
```

## v1 计算口径与限制

- 使用 `lunar-python 1.4.8` 计算立春分年、节气分月、四柱、藏干、十神和纳音。
- 输入按 IANA 时区标准化并同时返回 UTC；排盘使用该时区的当地民用钟表时间。
- 日柱在当地时间 `00:00` 换日。适配层修正了上游库在 `23:00-23:59` 仍按次日日干推时干的行为，使时柱与本策略一致。
- `longitude` 当前只记录，不参与计算；`true_solar_time=true` 会被拒绝，避免伪装成已支持。
- 上游节气表尚未通过独立天文算法和正式 golden dataset 交叉校验；尤其是非 `Asia/Shanghai` 时区且临近节气的案例，需要人工复核。
- 性别仅保留在输入摘要中；当前未计算大运、起运和流年。
- 五行 `visible` 统计四个天干和四个地支本气，`hidden_stems` 对藏干做不加权计数；`total` 只是两者相加，不代表旺衰强弱。

跨域来源通过逗号分隔的 `CORS_ORIGINS` 设置，默认允许本机的 `localhost:3000` 和 `127.0.0.1:3000`。
