# 新加坡受控地点目录

当前版本包含 55 个 URA 规划区、5 个规划大区，以及 LTA 出口文件内的 188 个站、613 个有效具名出口。区域和站点为不同实体。原 3716 个 OSM 地点继续从只读 Data API 获取。

188 是按轨道制式区分的站点条目数，不是去重后的物理换乘地点数。MRT 使用 `LTA:MRT:*`，LRT 使用 `LTA:LRT:*`；例如武吉班让的 MRT 与 LRT 分别保存出口，不能因同名合并覆盖。解析器逐一验证全部 188 个名称与唯一 ID，投影脚本必须核对实际节点和关系数量才报告成功。

- `ura-areas.json`：区域索引、来源、版本、原始 SHA-256；几何在 `data/datasets/ura_planning_areas.geojson`，采用公开 MP2019 口径，不冒称实时边界。
- `lta-stations.json`：从 `data/datasets/lta_mrt_exits.geojson` 构建的站点/出口目录及来源哈希。
- `area-aliases.json`：覆盖全部 55 区及 5 大区的应用中文别名，标准英文名直接可查。别名是人工维护映射，不代表所有译名都属于官方译名。
- `aliases.json`：额外常用站点别名；与同名区域共享的中文名也可配合 MRT/地铁站类型使用。

例：金文泰/Clementi/金文台 → `URA:AREA:CLEMENTI`；Clementi MRT/金文泰地铁站/克莱门特MRT → `LTA:MRT:CLEMENTI`。不得自动把区域变成车站或邮区 D05。

目录公开只读入口：`/api/locations/catalog`；按 ID 获取实体和来源：`/api/locations/URA:AREA:CLEMENTI`、`/api/locations/LTA:MRT:CLEMENTI`。应用解析、B 的工具、KG 投影使用同一稳定 ID。

刷新：`.venv\Scripts\python.exe scripts/sync_locations.py`。该命令从 data.gov.sg 官方公开下载 API 取得快照，保存原文件及哈希，不用 LLM 生成坐标。来源：[URA 规划区](https://data.gov.sg/datasets/d_4765db0e87b9c86336792efe8a1f7a66/view)、[LTA 出口](https://data.gov.sg/datasets/d_b39d3a0871985372d7e1637193335da5/view)，均附官方 Open Data Licence。

样本图同步：`node --env-file=.env --import tsx scripts/project-locations.ts`。脚本固定隔离样本端口 7475，并核验 593 条样本；写 GeoArea/GeoRegion/GeoStation/GeoExit 与区域归属关系，不写 7474 的完整环境。正常样本启动会调用此步骤。

目录范围不等于所有地址。未覆盖道路、楼栋、非标准街区及所有拼写变体，不能造坐标。真正未匹配或多义时给出候选/请求确认；已解析名称的“附近”按软偏好检索，不能要求用户再自行寻找地标。
