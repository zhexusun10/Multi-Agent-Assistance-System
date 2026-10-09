# Neo4j 房源图谱上手指南

Neo4j 保存从规范 SQL 数据重建的房源关系、公共地点和距离证据，不是 PostgreSQL 备份。**房源固定来自 `2026-10-09` 快照，不刷新、不追踪下架，不表示实时可租。** 房源明细 / 图片仍在 SQL 中，PostgreSQL 操作见 [README.md](README.md)。本文件合并启动、Browser、Cypher、空间查询和只读 HTTP API，无需启动 Agent 或模型服务。

所有命令从**项目根目录**执行。Python 自动读取根目录 `.env`，已有环境变量优先；下文 `python` 指项目虚拟环境，Windows 可替换为 `.\.venv\Scripts\python.exe`。

## 1. 连接信息

**已有图谱：[打开 Browser 查询](#3-browser连接后马上查询)；新环境：[首次启动](#2-首次启动选一个数据源)；应用接入：[只读 HTTP API](#5-只读-http-api)。**

| 入口 / 配置 | 本机开发默认值 |
| --- | --- |
| 官方 Neo4j Browser（图形界面） | <http://127.0.0.1:7474/browser/> |
| Bolt（Browser / 驱动连接） | `bolt://127.0.0.1:7687` |
| Database | `neo4j` |
| 本机认证方式 | **No authentication**（模板为 `NEO4J_AUTH=none`，无需填写账号密码） |
| 独立只读 HTTP API / Swagger | <http://127.0.0.1:8088/docs> |
| OpenAPI | <http://127.0.0.1:8088/openapi.json> |
| Browser 预填连接入口 | <http://127.0.0.1:8088/neo4j-browser>（需要 API 已启动） |

`7474` 是可视化入口，`7687` 是数据库连接，**`8088` 只提供 JSON API / Swagger，不是另一个图形前端**。`8000` / `3001` 属于 Agent 服务，与本模块无关。

```dotenv
NEO4J_AUTH=none
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_BROWSER_URL=http://127.0.0.1:7474/browser/
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
```

免认证**仅限回环地址开发**，禁止暴露到局域网 / 公网。生产需认证、TLS 和最小权限；Browser 直接连数据库，不受 HTTP API 的只读限制。

## 2. 首次启动：选一个数据源

### Windows：一条命令启动 PG → Neo4j → API

需要 Python 3.11+。PG 还没有房源时，先取得团队的 `2026-10-09` 版 `data/propertyguru.db`；快照被 Git 忽略，**clone 不会自带**。已有准备好的 PG 房源库则不需要本地 SQLite。首次执行需网络下载依赖、便携数据库 / Java，之后复用本地安装。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File data/start_knowledge_graph.ps1 -WithPostgres
```

脚本自动准备 `.venv`、未存在的 `.env`，启动本机 PG，为新空库导入固定快照；随后从 **PG** 重建 Neo4j（含公共地点距离）、启动以 **PG** 为规范明细来源的只读 API，并执行全量同源验收，最后输出 Browser 链接。不会覆盖已有 `.env` 或修改源 SQLite；非空但不符的 PG 库不会被自动覆盖。

**下次启动已有快照和图，不重复导图：**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File data/start_knowledge_graph.ps1 -WithPostgres -SkipImport
```

PG 已自行启动时去掉 `-WithPostgres`；Neo4j 已自行启动时可加 `-UseExistingNeo4j`。默认 `-Source Postgres` 同时决定导图与 API 来源，`-SkipImport` 只跳过导图，仍检查 PG 快照及 SQL / 图 / API 一致性。不一致时失败退出，不假装启动成功。

仅离线查看 SQLite 时，**显式选择另一种来源**：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File data/start_knowledge_graph.ps1 -Source SQLite
```

SQLite 模式不需要 PG，导图和明细 API 都读同一只读文件。切换来源会重启脚本记录且属于本项目的旧 API；未记录的进程或其他服务不会被停止，端口被它们占用时需自行停止或指定 `-Port`。

### PostgreSQL / 已有 Neo4j / Docker：CLI 流程

已有 Python 环境时只安装图谱依赖；Windows 用 [README 中的 PowerShell 命令](README.md#2-首次启动与导入)准备 `.env` / `.venv`。Linux / macOS 的 Bash：

```bash
test -e .env || cp .env.example .env
test -d .venv || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r data/graph_requirements.txt
# 已有数据库服务时跳过；启动 PG + Neo4j，不会启动 Python HTTP API
docker compose --env-file .env -f data/compose.yaml up -d --wait
python data/knowledge_graph.py prepare-postgres
python data/knowledge_graph.py check
```

没有 Docker 时，连接自己已运行的 Neo4j，并在 `.env` 设置实际 `NEO4J_*`。便携和 Docker 的图库互相独立，不能同时占用 `7474` / `7687`。

**从 PostgreSQL 全量导入**（先按 [PostgreSQL 指南](README.md)准备 `propertyguru` 库及房源）：

```bash
# 不传 --sqlite，使用 PROPERTYGURU_DATABASE_URL
python data/knowledge_graph.py import
python data/knowledge_graph.py stats
# 前台运行只读 API，保持此终端打开；Ctrl+C 停止
python data/knowledge_graph.py serve
```

`serve` 默认使用 `PROPERTYGURU_READ_DATABASE_URL`，未设置时使用 `PROPERTYGURU_DATABASE_URL`，PG 查询为只读事务。**只有显式 `--sqlite` 才选择 SQLite**；旧 `PROPERTYGURU_API_SQLITE` 不再切换来源，避免环境变量偷偷让 API 与导图使用不同存储。

**没有 PG 时，也可显式用 SQLite**，在同样已启动的 Neo4j 上执行以下命令替代导入 / 服务命令：

```bash
python data/knowledge_graph.py import --sqlite data/propertyguru.db
python data/knowledge_graph.py stats
python data/knowledge_graph.py serve --sqlite data/propertyguru.db
```

SQL 不可用会明确报错，不会自动回退。不要对同一图库交替导入 PG 与 SQLite；使用哪个规范源，就用相同来源启动 API 并验收。

### 启动后立即验证

```bash
python data/knowledge_graph.py check
python data/knowledge_graph.py stats
curl http://127.0.0.1:8088/ready
curl http://127.0.0.1:8088/api/v1/capabilities
python data/knowledge_graph.py verify --api-url http://127.0.0.1:8088
```

Windows PowerShell 的 HTTP 示例请用 `curl.exe`，避免 `curl` 别名；也可直接在 Swagger 的 **Try it out** 中执行请求。

- `check` 成功输出 `Neo4j is ready.`；`stats` 查看 `nodes.Listing`、`nodes.Place` 和坐标覆盖。
- `/health` 仅验证 Neo4j；`/ready` 同时查询规范 SQL 表和图中对应房源。`capabilities.sql_source` 显示当前 SQL 来源，`listing_snapshot` 声明固定快照日期 / 数量，**声明不等于已经验收通过**。
- `verify` 全量比对 SQL / Neo4j ID、投影字段、基础关系、坐标和地点距离边；会检查地点配置半径，并拒绝错误距离、漏边、重复边。随后完整遍历 HTTP 房源分页并抽查 SQL 明细、图片、图上下文和错误契约。它只读数据库，不迁移、不删图、不访问采集站点。不指定 `--api-url` 时只检查双库；SQLite 模式加 `--sqlite data/propertyguru.db`。
- 初次导入可能需要数分钟；导入 / 验收报告分别为 `data/.runtime/import-report.json`、`data/.runtime/verification-report.json`。固定房源数 **21,431**；自带 OSM 快照核验为 **3,716 公共地点、710,468 条 NEAR_PLACE、362 条 PART_OF_CAMPUS**，地点自身来源时间仍以 OSM 元数据为准。
- `stats.coordinate_coverage_percent` 给出实际坐标覆盖率。固定房源快照只有 **40.27%** 有有效坐标；缺失不是待刷新的实时状态，也不能用假坐标补齐。

## 3. Browser：连接后马上查询

1. 打开 <http://127.0.0.1:7474/browser/>，连接填 `bolt://127.0.0.1:7687`，选择 **No authentication**，数据库 `neo4j`。
2. 粘贴 Cypher，按 **Ctrl+Enter**；返回节点 / 关系时选择 **Graph**，返回数量 / 属性时选择 **Table**。
3. 点选节点 / 关系看属性，双击节点展开邻居；距离证据在关系的 `distance_type`、`distance_m`、`route_verified` 等属性中。

### 第一条查询：查看 20 个房源及其基础关系

```cypher
MATCH (l:Listing)
WITH l ORDER BY l.listing_id LIMIT 20
OPTIONAL MATCH (l)-[r:IN_PROJECT|LISTED_BY|IN_DISTRICT|NEAR_MRT]->(n)
RETURN l, r, n;
```

所有示例都是只读查询。`LIMIT` 只限制本次查看范围，不代表全库数量；不要一次在 Browser 渲染全部关系。完整批量读取使用下方 API 游标分页。

### D05、两卧以上、月租不超过 6,000 SGD

```cypher
MATCH (l:Listing)-[:IN_DISTRICT]->(d:District {district_code:'D05'})
WHERE l.listing_type = 'RENT' AND l.price <= 6000 AND l.bedrooms >= 2
RETURN l, d ORDER BY l.price, l.listing_id LIMIT 50;
```

### D05 房源：直线 1 km 内的学校

```cypher
MATCH (d:District {district_code:'D05'})<-[:IN_DISTRICT]-(l:Listing)
      -[r:NEAR_PLACE]->(p:Place:School {active:true})
WHERE r.distance_type = 'geodesic' AND r.distance_m <= 1000
RETURN d, l, r, p ORDER BY r.distance_m, l.listing_id LIMIT 50;
```

### 平台标注的 MRT 距离不超过 500 m

```cypher
MATCH (l:Listing)-[r:NEAR_MRT]->(m:MRT)
WHERE r.distance_type = 'platform_reported'
  AND r.distance_status = 'reported' AND r.distance_m <= 500
RETURN l, r, m ORDER BY r.distance_m, l.listing_id LIMIT 50;
```

### 房源 500 m 内的其他房源

`19571412` 是历史快照示例 ID。其他数据集先执行 `MATCH (l:Listing) WHERE l.position IS NOT NULL RETURN l.listing_id LIMIT 5;` 取得有坐标的 ID，再替换它。

```cypher
MATCH (center:Listing {listing_id:19571412})
WHERE center.position IS NOT NULL
MATCH (neighbor:Listing)
WHERE neighbor.position IS NOT NULL
  AND neighbor.listing_id <> center.listing_id
WITH center, neighbor, point.distance(center.position, neighbor.position) AS distance_m
WHERE distance_m <= 500
RETURN center.listing_id AS center_id, neighbor.listing_id AS neighbor_id,
       neighbor.title AS title, round(distance_m, 2) AS straight_line_m
ORDER BY distance_m, neighbor_id LIMIT 50;
```

这是直线距离。房源间邻近关系按请求计算，不持久化为全量两两关系。

### NUS 校园建筑及 BIZ2

```cypher
// NUS Kent Ridge 校园内的建筑
MATCH (c:Place {place_id:'OSM:way:54519165'})
      <-[r:PART_OF_CAMPUS]-(b:Place:CampusBuilding {active:true})
RETURN c, r, b ORDER BY b.name LIMIT 50;
```

```cypher
// BIZ2 / Business 2 的校园与邻近房源
MATCH (b:Place {place_id:'OSM:way:54619697', active:true})
OPTIONAL MATCH (b)-[pc:PART_OF_CAMPUS]->(c)
OPTIONAL MATCH (l:Listing)-[np:NEAR_PLACE]->(b)
RETURN b, pc, c, l, np LIMIT 50;
```

## 4. 图模型与距离语义

```text
(Listing)-[:IN_DISTRICT]->(District)
(Listing)-[:IN_PROJECT]->(Project)
(Listing)-[:LISTED_BY]->(Agent)
(Listing)-[:NEAR_MRT]->(MRT)
(Listing)-[:NEAR_PLACE]->(Place:School|FoodCourt|Mall|CampusBuilding|…)
(District)-[:HAS_NEARBY_PLACE]->(Place)
(Place:CampusBuilding)-[:PART_OF_CAMPUS]->(Place:School)
```

稳定业务键为 `listing_id`、`project_id`、`agent_id`、`district_code`、`station_id`、`place_id`；MRT 的 `station_id` 是本地规范名称 / 模式键，不是 LTA 官方编号。API 中 `Listing:19571412`、`Place:OSM:way:54619697` 等 **`entity_id` 可以保存**；图节点 `id` 是 Neo4j elementId，重建后可能变化，不要作为长期业务 ID。

| 关系 | 含义与限制 |
| --- | --- |
| `NEAR_MRT` | `distance_type=platform_reported`；平台标注距离 / 分钟，`route_verified=false`，不是已验证路线 |
| `NEAR_PLACE` | `distance_type=geodesic`；有效房源坐标到 OSM 参考点的 WGS-84 直线距离，默认持久化半径 **1,500 m** 内全部地点 |
| `NEAR_LISTING`（API 临时关系） | 房源间直线距离，`is_virtual=true`、`persisted=false`，不写入图库 |
| `HAS_NEARBY_PLACE` | 区域房源的邻近证据汇总，**不是官方邮区 / 行政归属**，一个地点可关联多个区域 |
| `PART_OF_CAMPUS` | 建筑参考点在 OSM 校园多边形内的几何证据，**不是产权、院系归属或入口路线** |

**直线距离不能当作步行 / 驾车距离，也不能据此判断学校入学资格。** 多边形地点坐标采用边界框中心，不是入口。没有有效房源坐标时配套为“未知”，不是“没有”；历史快照有效坐标 8,631 条、未知 12,800 条。

公共地点类别：`School`、`FoodCourt`、`Mall`、`Market`、`Hospital`、`Clinic`、`Park`、`Supermarket`、`Library`、`CampusBuilding`。校园 grounds 仍为 `School`，具名校园建筑单列为 `CampusBuilding`；同名巴士站不是同一个对象。

- 地点来自真实 OSM 快照，带 `OSM:node|way|relation:<id>`、来源 URL、时间、坐标依据和 ODbL 许可；**不是完整 MOE / NEA 官方名录**，不按同名模糊合并，也不把对象数当独立机构数。来源与许可见 [datasets/README.md](datasets/README.md)。
- [datasets/campus_corroborations.json](datasets/campus_corroborations.json) 保存人工审阅的外部对照（如 NUS 官方地图）；匹配证据有效时附加 `official_*`，不重命名或移动 OSM 对象。
- 房源和地点按业务键 Upsert，邻近边可重建。消失的 OSM 对象标为 inactive；源 SQL 删除房源不会自动删除图节点，外部业务边不由导入器清理。
- 图同步按批提交，不是跨库原子事务；只运行一个导入 / 补全进程，导入期间可能读到部分更新状态。SQL 与图投影可能有同步时间差。

## 5. 只读 HTTP API

给其他工作者的最短调用链：**搜索房源 → 取 `properties.listing_id` → SQL 详情 / 图片 → Neo4j 关系上下文**。无需驱动 SDK；直接调用 HTTP，完整输入 / 输出以 Swagger 和 `/openapi.json` 为准。

| 方法 | 路径 | 来源 / 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/capabilities`、`/api/v1/stats` | 能力、来源、距离语义 / 图谱统计 |
| `GET` | `/api/v1/districts` | Neo4j 区域入口与房源数 |
| `POST` | `/api/v1/listings/search` | Neo4j 按区域、交易类型、价格、卧室、MRT / 配套筛选 |
| `GET` | `/api/v1/listings/{listing_id}` | SQL 规范房源详情 |
| `GET` | `/api/v1/listings/{listing_id}/images` | SQL 图片 URL 分页 |
| `GET` | `/api/v1/listings/{listing_id}/context` | Neo4j 一跳关系与距离证据 |
| `POST` | `/api/v1/places/search` | Neo4j 地点名称 / 类别 / 区域附近筛选 |
| `GET` | `/api/v1/places/{place_id}` | Neo4j 地点及来源 |
| `POST` | `/api/v1/places/nearby` | 指定房源 ID 或经纬度，按直线距离分页查地点 |
| `POST` | `/api/v1/graph/search`、`/api/v1/graph/expand` | 图数据分页 / 按稳定 `entity_id` 展开 |

### 可直接复制的请求

以下为 Bash；PowerShell 推荐在 Swagger 填 JSON，不必处理 shell 引号和换行差异。示例房源 ID 在不同数据集下需替换为搜索结果中的 ID。

```bash
curl -X POST http://127.0.0.1:8088/api/v1/listings/search \
  -H 'Content-Type: application/json' \
  -d '{"filters":{"district":"D05","listing_type":"RENT","max_price":6000,"min_bedrooms":2,"place_category":"School","max_place_distance_m":1000},"page_size":50}'
curl http://127.0.0.1:8088/api/v1/listings/24685605
curl 'http://127.0.0.1:8088/api/v1/listings/24685605/images?page_size=50'
curl http://127.0.0.1:8088/api/v1/listings/24685605/context
```

```bash
curl -X POST http://127.0.0.1:8088/api/v1/places/nearby \
  -H 'Content-Type: application/json' \
  -d '{"latitude":1.333,"longitude":103.743,"radius_m":1500,"categories":["School","FoodCourt","Mall"],"page_size":50}'
```

也可把坐标替换为 `"listing_id":19571412`；**房源 ID 与经纬度只能二选一**。即时地点查询半径为 1–10,000 m，不依赖持久化 `NEAR_PLACE` 的半径。房源搜索中的配套筛选依赖持久化边，不能用它宣称覆盖超出导入半径的地点。

### 分页和返回格式

- 房源 / 地点 / 图片列表返回 `{items,next_cursor,has_more,page_size,source,warnings}`，单项返回 `{item,source,warnings}`；上下文返回 `listing` / `relationships` / 来源证据。
- 图接口返回 `{nodes,edges,listing_count,page_size,has_more,next_cursor}`，不是 `items` 列表。
- `page_size` 为 1–1,000，是每页大小，**不是结果总数上限**。把 `next_cursor` 放进下一次 POST 的 `cursor` 字段；图片 GET 放进 `cursor` 查询参数，保持筛选不变，直到 `next_cursor=null`。
- 换资源 / 筛选须重置游标；无效或不匹配游标返回 `422 INVALID_CURSOR`。并发导入时不是跨页一致快照，要一致结果先等待导入完成。

Python 标准库即可完整分页（可直接在项目 Python 中运行）：

```python
import json
from urllib.request import Request, urlopen

body = {"filters": {"district": "D05", "listing_type": "RENT"}, "page_size": 100}
items = []
while True:
    request = Request("http://127.0.0.1:8088/api/v1/listings/search",
                      data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        page = json.load(response)
    items.extend(page["items"])
    body["cursor"] = page["next_cursor"]
    if body["cursor"] is None:
        break
print(len(items), [item["properties"]["listing_id"] for item in items[:5]])
```

### 房源间半径查询（保留的 JSON 接口）

```bash
curl 'http://127.0.0.1:8088/api/nearby?listing_id=19571412&radius_m=500&limit=50'
```

半径 1–10,000 m，每页 `limit` 为 1–1,000；按未舍入距离及房源 ID 排序。响应含临时 `NEAR_LISTING` 边和 `next_cursor`，作为下一次 GET 的 `cursor` 参数继续请求；改变中心或半径需重置游标。`persisted=false`，不会写入图库。

旧 `/api/regions`、`/api/graph`、`/api/expand` 已移除，使用版本化区域 / 图接口替代。

### 错误与安全边界

| 状态 / 错误 | 含义 |
| --- | --- |
| `404` | 在所选 SQL / 图数据源中未找到实体 |
| `422 COORDINATES_UNKNOWN` | 房源无有效坐标，不能计算附近关系，不代表周围没有地点 |
| `422 INVALID_ARGUMENT` / `INVALID_CURSOR` | 输入错误 / 游标与筛选不匹配 |
| `503 SQL_NOT_CONFIGURED` / `SQL_UNAVAILABLE` | SQL 未配置 / 不可用，不会回退 SQLite |
| `503` | Neo4j 不可用时检查服务及 `NEO4J_*` |

版本化错误为 `{error:{code,message}}`，`/api/nearby` 兼容接口为 `{detail:...}`。API 只读、参数化、白名单输出，不接受任意 SQL / Cypher，不输出电话、证照、房号或 `raw_json`。上层自行负责认证、权限、限流、缓存和 Agent 编排；本数据模块只负责数据访问与只读 HTTP 契约。

## 6. 重建、停止与排错

### 从固定快照重建图谱

```bash
python data/knowledge_graph.py import                         # PG 房源 + 公共地点
python data/knowledge_graph.py import --sqlite data/propertyguru.db  # 显式 SQLite 来源
python data/knowledge_graph.py enrich-places --radius-m 1500   # 离线重放地点及距离边
python data/knowledge_graph.py enrich-places --refresh         # 在线更新公开快照
python data/knowledge_graph.py enrich-places --pbf /path/to/Singapore.osm.pbf
python data/knowledge_graph.py stats
```

前两条是**不同来源的替代命令，不要对同一图库轮流执行**。离线导入使用自带地点快照；在线获取先尝试 Overpass，失败时用 BBBike OSM PBF，按新加坡国界过滤，不导入 Johor。缓存位于 `data/.runtime/places/`；来源失败不会填入演示地点，空 / 无效快照拒绝导入。

只做冒烟测试可用 `import --limit 100 --skip-places`；不会清除已有的其他房源，也不代表全量快照验收通过。正式使用应完整导入后执行 `verify`。`main.py sync-graph` 与 `knowledge_graph.py import` 共用完整投影实现，都会重建公共地点距离；地点的显式更新不改变房源快照日期，不重新抓取房源。

可在 Browser 检查约束、元数据和空间索引：

```cypher
SHOW CONSTRAINTS;
MATCH (d:Dataset {dataset_id:'osm_sg_places'}) RETURN d;
SHOW INDEXES YIELD name, type, state, properties
WHERE name = 'listing_position' RETURN name, type, state, properties;
```

有效房源坐标存为 WGS-84 `point`，半径查询使用 `listing_position` POINT 索引。

### 停止与数据位置

```powershell
# 只停止脚本管理的 API，Neo4j 保持运行
powershell -NoProfile -ExecutionPolicy Bypass -File data/stop_knowledge_graph.ps1 -WebOnly
# 停止脚本管理的 API / 便携 Neo4j；不停止 PostgreSQL，保留数据
powershell -NoProfile -ExecutionPolicy Bypass -File data/stop_knowledge_graph.ps1
```

```bash
# 只停止 Docker Neo4j，保留卷；手动 serve 的 API 用 Ctrl+C 停止
docker compose --env-file .env -f data/compose.yaml stop neo4j
# 停止整个 Docker 数据栈（含 PostgreSQL），保留卷
docker compose --env-file .env -f data/compose.yaml down
```

**不要使用 `down -v`**，它会删除数据库卷。便携图数据位于 `data/.runtime/neo4j-community-5.26.0/data/`；Docker 图数据位于 `neo4j_data` 命名卷（实际名称含 Compose 项目前缀）。两套数据不共享。备份应停库后复制便携数据目录，或按 Neo4j 官方离线 dump / load 操作，不要复制仍在写入的目录。

| 现象 | 先检查 |
| --- | --- |
| 缺少 `propertyguru.db` | 快照不在 Git；获取团队快照，或按 PG / 已有 Neo4j 流程操作 |
| `7474` / `7687` 被占用 | 便携与 Docker 是否同时运行；不要重置他人的图库 |
| Browser 打不开 / Bolt 失败 | `knowledge_graph.py check`；便携日志 `data/.runtime/neo4j.stderr.log` |
| Browser 有连接但无房源 | `stats` 的 `nodes.Listing`；是否连接到另一套空图库、是否已完整导入 |
| API 启动失败 / 端口冲突 | `8088` 是否被占用；便携日志 `data/.runtime/visualization.stderr.log` |
| 图有房源但 SQL 详情 404 / 503 | 请求 `/ready`，核对 `capabilities.sql_source`；运行 `verify --api-url`，查找漏导、旧字段或不同来源 |
| 修改 `.env` 密码后连接失败 | `.env` 不会重设已有 Neo4j 密码；须先在实际服务修改密码，再同步配置并重启 API |
| 无法找到附近地点 | 坐标是否有效、地点是否 active、查询是否超出持久边半径；未知不等于没有 |

恢复认证时先在实际 Neo4j 设置账号密码，再删除 `NEO4J_AUTH=none` 并配置相同的 `NEO4J_USER` / `NEO4J_PASSWORD`；Windows 便携初始密码需至少 8 字符。已有外部服务不会被脚本自动重配，生产只读账号和 TLS 需另行配置。
