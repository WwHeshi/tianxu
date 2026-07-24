# 出生地点与经度数据

后端运行时会合并两份版本化静态快照，并以稳定的 `location_id` 查找地点：

- `district_longitudes.json`：中国大陆 31 个省级地区下的 2,849 个正式末级行政单位。
- `special_region_locations.json`：香港 18 区、澳门 8 个地理区域、台湾 368 个乡镇市区。

总计 3,243 个可选地点，所有记录均有独立坐标，`fallback=0`。客户端只提交
`location_id`，不提交名称、层级、经纬度或时区；后端从快照返回规范化的
`region_code`、`timezone` 和 `division_path`。

所有地区的出生时间统一按北京时间填写。`Asia/Shanghai`、`Asia/Hong_Kong`、
`Asia/Macau` 和 `Asia/Taipei` 仅作为地点元数据保留，本版真太阳时仍统一以东经
120°标准经线计算。

## 中国大陆

大陆运行时 ID 为 `CN:<六位末级行政区代码>`。功能区、产业园、统计管理区和人为
构造的中间层不进入快照。直辖市区县、省直辖县级单位以及没有县级辖区的城市使用
真实层级，不补造市级节点。

数据来源：

- 行政区代码和名称：[`@aurouscia/china-areas@0.7.0`](https://www.npmjs.com/package/@aurouscia/china-areas)，
  MIT License，发布于 2026-05-18，上游为民政部国家地名信息库。
- 2,839 条行政中心坐标：[`tmap-citycoordinate@1.0.1`](https://www.npmjs.com/package/tmap-citycoordinate)，
  MIT License，上游为腾讯地图行政中心坐标；该包未声明坐标系。
- 10 条补齐或纠错坐标：民政部国家地名信息库公开检索接口，查询于 2026-07-24。每条均校验
  名称、正式行政代码、县级行政区类型和行政层级，并按公开网页客户端使用的
  `EPSG:4326` 保存。

覆盖统计为 `direct_code=2839`、`official_mca_api=10`、`fallback=0`。

## 香港

开放民政事务总署的 18 个地方行政区，ID 为 `CN-HK:DCD:<AREA_ID>`。区划身份来自
HAD 的 CSDI `District boundary (DCD)` 图层，坐标采用各区官方民政咨询中心点；离岛区
有三个咨询中心，固定选用人口中心东涌的点位。坐标由官方服务转换为 `EPSG:4326`。

- 区界数据集：`had_rcd_1634523272907_75218`
- 咨询中心数据集：`had_rcd_1629267205214_43393`
- 许可：DATA.GOV.HK Terms of Use 1.2
- 时区元数据：`Asia/Hong_Kong`

## 澳门

澳门的堂区不具有现行区级行政机关，因此本项目将用户熟悉的七个传统堂区和路氹城
明确建模为 8 个“地理区域”，不把它们描述为正式行政区。项目自定义稳定 ID 为
`CN-MO:AREA:01` 至 `CN-MO:AREA:08`；源身份同时保留 GeoNames `geonameId`，避免把项目
编号伪装成官方代码。

区域身份和独立代表点来自 [GeoNames](https://www.geonames.org/) 的 8 条公开 RDF 记录，
地址为 `https://sws.geonames.org/<geonameId>/about.rdf`。生成器按稳定 `geonameId`、源名称、
澳门国家代码、`A.ADM1` 要素类型、许可证和署名共同校验后写入坐标，不采用土地工务局
Web Map，也不再包含原来的 26 个统计区数据。

- 地理区域：花地玛堂区、圣安多尼堂区、大堂区、望德堂区、风顺堂区、嘉模堂区、
  路氹城、圣方济各堂区
- GeoNames ID：`11875154` 至 `11875161`
- 坐标口径：GeoNames 要素代表点，`EPSG:4326`
- 许可：[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/)
- 署名：GeoNames（快照内同时保存下载日期及 8 条 RDF 合并内容的 SHA-256）
- 时区元数据：`Asia/Macau`

## 台湾

开放 22 个县市下的 368 个乡镇市区，ID 使用官方五位 `COUNTYCODE` 和八位
`TOWNCODE` 的命名空间形式 `CN-TW:TOWN:<TOWNCODE>`。边界采用
[`taiwan-atlas@2021.9.20`](https://www.npmjs.com/package/taiwan-atlas) 的
`towns-10t.json`；该包明确由内政部国土测绘中心政府资料开放平台
[`dataset 7441`](https://data.gov.tw/dataset/7441) 转换，许可证为 MIT。上游许可为
政府资料开放授权条款第 1 版。坐标是官方派生边界的面积质心，坐标系为 `EPSG:4326`；
简体显示名由固定版本的 `opencc-js@1.4.1` 转换，原始繁体中英文名称仍逐条保留。

官方平台目前发布的资源文件为 2025-03-18 版本，但 TGOS 下载端点在自动化环境返回
403，因此当前快照使用上述可审计镜像。两者的乡镇市区总数均为 368；以后能够取得
官方文件时，应重新计算并比较代码、名称和边界哈希后再升级。

- 时区元数据：`Asia/Taipei`

## 精度与零回退规则

地点代表点是区级近似值，不等同于具体医院或住址。同一地区内部仍可能产生数分钟的
经度差；接近换日、时辰或节气边界时应使用更具体地址复核。

澳门采用 GeoNames 地理要素代表点；台湾采用面积质心来代表区域平均经度。台湾的凹形
或多岛边界，其数学质心可能落在边界外或水面上；这不影响本项目把它作为经度统计代表
值使用，但它不能作为地图落点或实际地址。若以后增加地图标记，应另行生成
`point-on-surface` 坐标。

每条记录必须包含独立经纬度、坐标方法、来源和 `fallback=false`。加载器还会拒绝
`coordinate_match` 以 `_fallback` 结尾的记录；缺少数据时直接停止排盘，绝不会改用
所属城市、地区或省级中心。

## 更新快照

安装前端依赖后，在仓库根目录运行：

```powershell
node backend/scripts/fetch_mca_coordinate_overrides.mjs
node backend/scripts/generate_district_longitudes.mjs
node backend/scripts/generate_special_region_locations.mjs
cd backend
uv run pytest tests/test_location_data.py
```

第三条命令只在维护期访问香港官方接口、8 条 GeoNames 澳门 RDF 并读取锁定版本的台湾
边界包；运行时完全读取已经提交的 JSON，不请求任何地图服务。生成器同时输出前端使用
的 `frontend/lib/special-region-options.json`，确保前后端来自同一份地点身份数据。更新
任何上游时必须复核记录数、身份代码、来源哈希、许可证、坐标范围及 `fallback=0`。
