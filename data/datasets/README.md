# 公共地点快照

`singapore_places.json` 是可重放的真实新加坡 OSM 地点数据，不是演示数据。导入与 API 使用指南见 [Neo4j 指南](../NEO4J.md)。

- 来源：© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)，**ODbL-1.0**；不与项目代码许可混同，分发/改编须遵守 ODbL 署名及数据库许可要求。
- 获取：优先 Overpass 按新加坡国家 area 查询；不可用时使用 [BBBike Singapore PBF](https://download.bbbike.org/osm/bbbike/Singapore/Singapore.osm.pbf)。本快照实际来自 PBF。
- 境外过滤：使用 [geoBoundaries gbOpen 新加坡国界](https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/SGP/ADM0/geoBoundaries-SGP-ADM0_simplified.geojson)，不是宽泛经纬度矩形，也不是邮政分区边界。geoBoundaries 资料及署名见 [其项目](https://www.geoboundaries.org/)；边界仅在获取时用于国家筛选，不作为房源坐标/邮区归属的来源。
- `metadata` 保存采集时间、获取 URL、边界过滤方法、许可、类别和数量；每条记录保存 OSM 对象 ID、来源 URL、源更新时间及坐标依据。
- 支持学校/大学、食阁/熟食中心、商场、市场、医院、诊所、公园、超市、图书馆，以及**校园建筑**（NUS/NTU 等校园内带名称建筑）。只收入已映射、有名称、有有效坐标的对象；不是完整官方名录。
- 校园建筑的 `campus_ids`/`campus_membership_method` 是点在校园多边形内的几何证据；`datasets/campus_corroborations.json` 保存人工审核过的外部对照（如 NUS 官方地图 API），仅附加 `official_*` 字段，不改变 OSM 身份。
- 不按名称模糊去重；一个机构可能有多个 OSM 对象。图形坐标为边界框中心，不是入口；营业时间等源字段未独立核验。

从项目根目录更新：

```bash
python data/knowledge_graph.py enrich-places --refresh
```

离线只重放快照及关系：

```bash
python data/knowledge_graph.py enrich-places
```

原始提取文件/国界缓存留在忽略的 `data/.runtime/places/`；更新后的此 JSON 可审查后随项目分发。距离计算和字段未知状态见 [Neo4j 图模型与距离语义](../NEO4J.md#4-图模型与距离语义)，不宣称步行路线或学校入学资格。
