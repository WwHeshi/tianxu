# 大陆正式行政区经度数据

`district_longitudes.json` 是随后端发布的版本化行政中心坐标快照。查找键为
六位 `district_code`，共包含中国大陆 31 个省级地区下的 2,849 个正式末级行政单位。

功能区、产业园、统计管理区和人为构造的中间层不进入本快照。直辖市下的区县、
省直辖县级单位以及没有县级辖区的城市使用真实层级，`city_code` 和 `city_name`
可以为 `null`。

## 数据来源与许可

- 行政区代码和名称：[`@aurouscia/china-areas@0.7.0`](https://www.npmjs.com/package/@aurouscia/china-areas)，
  MIT License，发布于 2026-05-18。其上游是民政部国家地名信息库开放接口。
- 2,841 条行政中心坐标：[`tmap-citycoordinate@1.0.1`](https://www.npmjs.com/package/tmap-citycoordinate)，
  MIT License，作者 wentao，上游为腾讯地图行政中心坐标；该包未声明坐标系。
- 8 条补齐坐标：中华人民共和国民政部国家地名信息库公开检索接口，查询于
  2026-07-24。接口返回的记录均通过名称、正式行政代码、`县级行政区` 类型和行政层级校验；
  坐标按其官方网页客户端公开的 `EPSG:4326` 口径保存。

快照记录了行政区文件、坐标包文件和民政部补齐快照的 SHA-256。npm 上游许可文本随依赖安装；本项目的匹配和
真太阳时计算不代表民政部、腾讯地图或数据包作者对结果的认可。

## 覆盖与精度

生成结果共 2,849 条：

- `direct_code=2841`：正式代码直接命中 `tmap-citycoordinate` 中的行政中心坐标。
- `official_mca_api=8`：西沙区、南沙区、两江新区、米林市、错那市、和康县、
  和安县、白杨市使用民政部国家地名信息库中各自独立的县级行政区坐标。
- `fallback=0`：没有任何记录使用所属城市、地区或省级中心点回退。

响应会返回 `location_precision`、`coordinate_match` 和逐条记录的坐标来源。
后端会拒绝缺少独立坐标或被标记为回退的记录，不会继续计算。
行政中心坐标本身仍是区县级近似值，接近换日或时辰边界时应使用具体出生地址复核。

## 更新

安装前端依赖后，在 `backend/` 运行：

```powershell
node scripts/fetch_mca_coordinate_overrides.mjs
node scripts/generate_district_longitudes.mjs
uv run pytest tests/test_location_data.py
```

第一条命令访问民政部公开接口并重新核验 8 条补齐记录；日常离线构建可直接使用已经提交的
`mca_coordinate_overrides.json`。生成器会拒绝缺少坐标、身份不匹配或没有被消费的补齐记录，
测试会拒绝任何 `fallback=true` 的快照。更新依赖时必须同时复核来源版本、覆盖统计和坐标来源。

香港、澳门、台湾暂未开放区县选择；待分别接入并校验当地权威行政区数据后再加入，
不会继续使用旧包中的错误补充数据。
