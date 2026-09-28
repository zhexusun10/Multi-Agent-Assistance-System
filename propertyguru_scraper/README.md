# PropertyGuru 数据采集、清洗与 PostgreSQL 入库系统

基于 Python 现代技术栈（`curl_cffi` + `SQLAlchemy 2.0` + `PostgreSQL` + `Rich`）构建的端到端 PropertyGuru 房地产数据工程系统。针对 Cloudflare Turnstile / WAF 强防护进行了底层 TLS 指纹伪装与抗反爬设计，直接解析 Next.js 结构化数据，经过高精度多维清洗后批量无冲突（Upsert）存入本地 PostgreSQL 数据库。

---

## 🌟 核心特性与架构升级

1. **强抗反爬（Cloudflare Bypass）**：采用 `curl_cffi` 配合 Chrome 浏览器底层 TLS/HTTP2 指纹伪装与会话 Cookie 管理，无需启动重量级浏览器驱动即可 100% 穿透防护。
2. **具体精确地址解析**：
   - 提取精准的新加坡 6 位邮政编码（`postal_code`，如 `536562`）
   - 提取具体街道名称（`street_name`，如 `Amber Gardens`）与门牌号（`street_number`，如 `30`）
   - 提取楼栋（`block`）、单元号（`unit`）、楼层（`floor_level`）
   - 提取高精度地理经纬度（`latitude` 与 `longitude`）
3. **主页与详情页图片严格分离存储**：
   - **主页/搜索列表页图片（`HOMEPAGE`）**：
     - 主搜索卡片缩略图（`THUMBNAIL`，如 `V550`）
     - 搜索卡片轮播预览图（`PREVIEW`）
   - **详情页图片（`DETAIL_PAGE`）**：
     - 全套超清房源展示照片（`PHOTO`，如 `V800`）
     - 房屋平面户型图（`FLOOR_PLAN`）
   - **双重存储架构**：
     - `properties` 主表提供 `homepage_images` 和 `detail_images` 两个独立 `JSONB` 字段快速调取；
     - 专设 `property_images` 规范化关系表，按 `source_page`（`HOMEPAGE` vs `DETAIL_PAGE`）与 `image_type` 进行分类索引与关联查询。
4. **PostgreSQL 高性能入库与去重**：
   - 使用 `ON CONFLICT (listing_id) DO UPDATE` 幂等增量更新
   - 核心字段建立 B-tree、GIN 及外键级联索引。
5. **开箱即用 CLI 控制台**：带有交互式进度条、终端彩色报表展示以及 CSV/JSON 导出功能。

---

## 📁 目录结构

```
propertyguru_scraper/
├── config.py             # 数据库连接、爬虫速率、超时及反爬配置
├── models.py             # SQLAlchemy 2.0 ORM 房源与图片双表模型与索引
├── database.py           # 数据库引擎、连接池管理及批量 Upsert 逻辑
├── cleaner.py            # 核心清洗器（主页/详情页图片分离、具体地址/经纬度提取）
├── scraper.py            # 抗 Cloudflare 抓取引擎（支持列表页与详情页连续抓取）
├── pipeline.py           # 抓取-详情增强-清洗-入库一体化编排调度器
├── main.py               # CLI（init-db / run / stats / export / sync-graph）
├── graph.py              # 从 PostgreSQL 到 Neo4j 的可重建投影
├── compose.yaml          # 可选本地 Neo4j（持久化数据卷）
├── requirements.txt      # 爬虫、入库、图同步及测试依赖
├── tests/                # 清洗、持久化和图同步测试
└── README.md             # 使用说明文档
```

---

## 🚀 快速上手

**以下所有命令均从项目根目录运行**（不要 `cd propertyguru_scraper`）。需要 Python 3.11+、本机 PostgreSQL；Neo4j 可选。脚本采用脚本目录相对导入，应以 `python3 propertyguru_scraper/main.py` 调用。

### 1. 安装与独立数据库
```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r propertyguru_scraper/requirements.txt
# 若也运行根项目 FastAPI，另安装：python3 -m pip install -r requirements.txt

# 使用有 CREATE ROLE/DATABASE 权限的本机 PostgreSQL 管理账号；已存在则跳过。
psql -d postgres -c 'CREATE ROLE propertyguru_scraper LOGIN'
psql -d postgres -c 'CREATE DATABASE propertyguru OWNER propertyguru_scraper'
test -e .env || cp .env.example .env    # 仅首次创建；不要覆盖现有 .env
# 编辑 .env，设置 PROPERTYGURU_DATABASE_URL；如需密码，先给角色设密码并写入该 URL。
set -a; source .env; set +a           # 新终端需重新加载
python3 propertyguru_scraper/main.py init-db
python3 propertyguru_scraper/main.py stats
```

爬虫只使用 `PROPERTYGURU_DATABASE_URL`，**不会**读取根项目 LangGraph 的 `DATABASE_URL` 或普通 `PG*` 环境变量。根项目的 `multi_agent_assistance` 数据库保存 checkpoint、任务及事件；爬虫使用独立的 `propertyguru` 数据库，避免表名/数据/权限冲突。不要把这两个 URL 指向同一个数据库。示例 URL 在根目录 `.env.example`；本机若不允许无密码 TCP 连接，请为角色设置密码并修改本地 `.env` 的 URL（不要提交凭据）。未提供 URL 时，才使用专属 `PROPERTYGURU_PGUSER`（默认 `jerry`）、`PROPERTYGURU_PGPASSWORD`（默认空）、`PROPERTYGURU_PGHOST`（默认 `localhost`）、`PROPERTYGURU_PGPORT`（默认 `5432`）、`PROPERTYGURU_PGDATABASE`（默认 `propertyguru`）。`init-db` 要求建表权限，创建表和索引，并给已有 `properties` 表补齐模型中缺失的可空列及索引；不会创建数据库，也不修改旧列的类型/约束或升级其他旧表。非兼容手工 schema 仍需人工迁移。

---

## 💻 常用 CLI 命令

### 1. 抓取数据并入库（默认自动包含详情页地址与高清大图）
```bash
# 抓取买房数据（默认抓取 5 页，自动提取门牌号、邮编、经纬度与分离图片）
python3 propertyguru_scraper/main.py run --type sale --pages 5

# 抓取租房数据
python3 propertyguru_scraper/main.py run --type rent --pages 5

# 买房和租房同时抓取
python3 propertyguru_scraper/main.py run --type all --pages 10

# 极速模式：跳过详情页抓取（仅抓取主页数据与卡片预览图）
python3 propertyguru_scraper/main.py run --type sale --pages 10 --skip-details

# 从指定页码开始抓取（支持断点续爬）
python3 propertyguru_scraper/main.py run --type sale --start-page 6 --pages 10
```

### 2. 查看数据库统计与图片分离看板
```bash
python3 propertyguru_scraper/main.py stats
```

### 3. 导出数据为 CSV / JSON
```bash
# 导出全部房源为 CSV（包含具体街道、邮编、经纬度及图片统计）
python3 propertyguru_scraper/main.py export --format csv --output singapore_properties.csv

# 导出指定区域的房源为 JSON（export 不支持 --type/--limit）
python3 propertyguru_scraper/main.py export --table properties --format json --output properties.json
```

### 4. 运行单元测试
```bash
PYTHONPATH=propertyguru_scraper python3 -m pytest propertyguru_scraper/tests -q
# 可选：用 PostgreSQL 管理账号运行：
# psql -d postgres -c 'CREATE DATABASE propertyguru_test OWNER propertyguru_scraper'
# 测试会在该独立测试库创建/删除临时 schema（不要使用生产库）。
PROPERTYGURU_TEST_DATABASE_URL='postgresql+psycopg2://propertyguru_scraper@127.0.0.1:5432/propertyguru_test' \
  PYTHONPATH=propertyguru_scraper python3 -m pytest propertyguru_scraper/tests/test_persistence.py -q
```

---

## 可选本地 Neo4j 知识图谱投影

PostgreSQL `properties` / `agents` 是唯一数据源；Neo4j 仅存放可重建的房源投影，**不参与 PostgreSQL 入库事务**。先按上节安装依赖、执行 `init-db` 并完成 PostgreSQL 入库。所有命令从项目根目录执行；图谱回填只读现有 PostgreSQL 数据，不触发抓取：

```bash
# 编辑本地 .env 的 NEO4J_PASSWORD（至少 8 个字符）；不要提交密码。
# Compose 不会自动读取根目录 .env，因此显式指定 --env-file。
docker compose --env-file .env -f propertyguru_scraper/compose.yaml up -d
# Neo4j 首次启动后等待服务就绪；Bolt 127.0.0.1:7687，Browser http://127.0.0.1:7474
set -a; source .env; set +a           # 使 CLI 与 Compose 使用相同的 NEO4J_* 值
python3 propertyguru_scraper/main.py sync-graph  # 全量回填；可安全重跑
python3 propertyguru_scraper/main.py sync-graph --limit 100 --batch-size 25 # 小批量试运行
python3 propertyguru_scraper/main.py run --type sale --pages 1 --sync-graph
python3 propertyguru_scraper/main.py stats
# 不删除数据卷：下次 up 会保留图数据和首次启动的密码。
docker compose --env-file .env -f propertyguru_scraper/compose.yaml down
```

Browser 中选择 `neo4j` 数据库执行示例 Cypher（或使用同一账号的 Neo4j 客户端）：

```cypher
MATCH (l:Listing)-[:IN_DISTRICT]->(d:District {district_code: 'D05'})
OPTIONAL MATCH (l)-[:IN_PROJECT]->(p:Project)
RETURN l.listing_id AS listing_id, l.title AS title, l.price AS price,
       d.district_code AS district, p.project_id AS project_id
ORDER BY listing_id LIMIT 10;
```

`NEO4J_URI` 默认 `bolt://localhost:7687`、`NEO4J_USER` 默认 `neo4j`、`NEO4J_DATABASE` 默认 `neo4j`；Compose 启动时必须显式设置 `NEO4J_PASSWORD`（至少 8 字符）。CLI 未设置时仍默认 `password`，但不应依赖默认凭据。compose 与 CLI 的密码必须一致；持久化数据卷已初始化后，更改 `.env` 密码不会自动更新 Neo4j 中的密码，需在 Neo4j 中修改账号密码再同步配置。`sync-graph` 无需重新抓取，`--limit` 限制本次读取的最小 listing_id 顺序记录数，`--batch-size` 控制每次 Neo4j 事务数量。生产同步不要设置 limit；变更 PG 行后重跑即可更新房源属性及四条关系。成功的详情页抓取会用最新相册移除旧的 `DETAIL_PAGE` 图片行；卡片抓取和详情页失败不会清除它们。卡片抓取不覆盖已保存的 Agent 档案；房源自己的 Agent 名称优先用于图投影。缺失字段被视为未知而非确认删除：后续详情页省略 agent/project 或地址时可能保留旧 ID/字段及图关系，需人工核实并清理确认已失效的数据。删除的 PG 房源目前不会自动从 Neo4j 删除；如需完整重建，可清空此专用图数据库后全量同步。`--all-pages` 在空页/失败页且没有分页元数据时停止并报告错误（有限 `--pages` 仍可继续下一页）。`--sync-graph` 同步整个已有 PG 表（不限于本次抓取），失败返回非零状态，但已提交的 PG 写入不会回滚；修复 Neo4j 后单独重跑 `sync-graph` 即可。

图模型：`(:Listing {listing_id})-[:IN_PROJECT]->(:Project {project_id})`、`-[:LISTED_BY]->(:Agent {agent_id})`、`-[:IN_DISTRICT]->(:District {district_code})`、`-[:NEAR_MRT]->(:MRT {name})`。缺失 ID/空地铁站不创建共享节点；房源只包含价格、类型、面积、邮编、URL 等有限白名单字段。**不复制电话、证照、原始 JSON、图片或详细住址**。换边时旧边删除，失联的维度节点可能保留；约束和 MERGE 防止重复节点。

---

## 🗄️ PostgreSQL 表结构说明

### 表 1：`properties`（房源核心表）
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `listing_id` | `BIGINT PRIMARY KEY` | 房源唯一 ID |
| `listing_type` | `VARCHAR(20)` | `SALE` 或 `RENT` |
| `title` | `VARCHAR(255)` | 房源/项目楼盘名称 |
| `property_type` | `VARCHAR(100)` | 物业类型（Condo, HDB, Landed 等） |
| `price` | `NUMERIC(15, 2)` | 售价或月租金（SGD） |
| `psf` | `NUMERIC(10, 2)` | 每平方尺单价（SGD/sqft） |
| `bedrooms` / `bathrooms` | `INTEGER` | 卧室/浴室数量 |
| `floor_area_sqft`| `NUMERIC(12, 2)` | 室内使用面积（sqft） |
| `land_area_sqft` | `NUMERIC(12, 2)` | 土地面积（sqft） |
| `postal_code` | `VARCHAR(20)` | 新加坡具体 6 位邮编（如 `439964`） |
| `street_name` | `VARCHAR(255)` | 具体街道名称（如 `Amber Gardens`） |
| `street_number`| `VARCHAR(50)` | 门牌号（如 `30`） |
| `block` / `unit` | `VARCHAR(50)` | 楼栋号 / 房号 |
| `latitude` / `longitude` | `NUMERIC(11, 8)` | 准确地理经纬度坐标 |
| `nearest_mrt` | `VARCHAR(255)` | 最近地铁站名称 |
| `mrt_distance_m`| `INTEGER` | 距离地铁站米数 |
| `homepage_images`| `JSONB` | 主页/列表页图片包（含缩略图与卡片预览图） |
| `detail_images` | `JSONB` | 详情页图片包（含全套高清相册与户型图） |
| `detail_fetched`| `BOOLEAN` | 是否已抓取详情页 |
| `raw_json` | `JSONB` | 原始完整 JSON 字典 |

### 表 2：`property_images`（分离图片明细表）
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | `BIGSERIAL PRIMARY KEY`| 自增主键 |
| `listing_id` | `BIGINT REFERENCES properties` | 关联的房源 ID |
| `source_page` | `VARCHAR(20)` | 图片来源：`HOMEPAGE` 或 `DETAIL_PAGE` |
| `image_type` | `VARCHAR(20)` | 图片类型：`THUMBNAIL`, `PREVIEW`, `PHOTO`, `FLOOR_PLAN` |
| `image_url` | `TEXT` | 完整图片 CDN 链接 |
| `caption` | `TEXT` | 图片标题/房间说明 |
| `display_order`| `INTEGER` | 展示顺序 |
