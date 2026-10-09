# PostgreSQL 房源库上手指南

PostgreSQL 是规范房源、经纪人和图片的主库。**Neo4j 启动、图谱查询、空间距离和只读 HTTP API 统一见 [NEO4J.md](NEO4J.md)**。

> **房源数据固定来自 `2026-10-09` 快照，不刷新、不追踪房源下架、不承诺当前仍在挂牌。** 租金、属性和配套检索均基于这一固定房源快照；字段缺失按未知处理，不猜测补值。所有工作者应把结果表述为“快照中的房源 / 租金”，而不是实时房源。

使用数据不需要启动 Agent、Node.js 或模型服务。所有命令从**项目根目录**执行，需要 Python 3.11+；下文 `python` 指项目虚拟环境，Windows 可替换为 `.\.venv\Scripts\python.exe`。Python 入口自动读取根目录 `.env`，已有环境变量优先，不需要手动 `source .env`。

**已有房源库：[连接信息](#1-连接信息) → [SQL / Python 查询](#3-直接可用的-sql--python-查询)；新环境：[首次启动与导入](#2-首次启动与导入)。**

## 1. 连接信息

以下是 [.env.example](../.env.example) 对应的本机开发配置，DBeaver / pgAdmin 也可以直接填写：

| 项目 | 默认值 |
| --- | --- |
| Host / Port | `127.0.0.1` / `5432`（以启动输出和本地 `.env` 为准） |
| Database / Schema | `propertyguru` / `public` |
| User / Password | `propertyguru_scraper` / 无密码（仅限本机开发 `trust` 模式） |
| Python SQLAlchemy URL | `postgresql+psycopg2://propertyguru_scraper@127.0.0.1:5432/propertyguru` |
| psql / 其他 PostgreSQL 客户端 URL | `postgresql://propertyguru_scraper@127.0.0.1:5432/propertyguru` |

```dotenv
PROPERTYGURU_DATABASE_URL=postgresql+psycopg2://propertyguru_scraper@127.0.0.1:5432/propertyguru
```

**不要用 `DATABASE_URL`**：它属于 Agent Runtime 的 `multi_agent_assistance` 库，保存 checkpoint、任务和会话事件，不是房源库。可选的 `PROPERTYGURU_READ_DATABASE_URL` 是只读 API / 查询账号；抓取、迁移仍使用有写权限的 `PROPERTYGURU_DATABASE_URL`。

连接方式任选一种（端口不同时相应替换 `5432`）：

```bash
psql -h 127.0.0.1 -p 5432 -U propertyguru_scraper -d propertyguru
# Docker 用户不需要在宿主机安装 psql：
docker compose --env-file .env -f data/compose.yaml exec postgres psql -U propertyguru_scraper -d propertyguru
```

Windows 便携版的 `psql` 不会加入 PATH，可直接运行：

```powershell
& "C:\ProgramData\PropertyGuruPortablePg\pgsql\bin\psql.exe" -h 127.0.0.1 -p 5432 -U propertyguru_scraper -d propertyguru
```

## 2. 首次启动与导入

只选一种启动方式，**不要同时启动便携 PostgreSQL 和 Docker PostgreSQL**。已有服务和数据时，跳过依赖安装、建表及导入。

首次导入需要团队提供的 `2026-10-09` 版 `data/propertyguru.db`（约 1 GB，**被 Git 忽略，clone 不会自带**），或对应的 PG 备份。没有本地文件时直接连接团队已准备好的房源库；**不要重新抓取来代替固定快照，也不要把空库误认为全量数据**。

只想一次启动完整 PG → Neo4j → API 链路的 Windows 用户，执行 `powershell -NoProfile -ExecutionPolicy Bypass -File data/start_knowledge_graph.ps1 -WithPostgres` 即可；步骤见 [Neo4j 指南](NEO4J.md#2-首次启动选一个数据源)。

### Windows：便携 PostgreSQL

在项目根目录的 PowerShell 执行，已有 `.env` / `.venv` 不会被覆盖：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
if (!(Test-Path .venv\Scripts\python.exe)) { py -3 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r data/graph_requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File data/start_postgres.ps1
.\.venv\Scripts\python.exe data/knowledge_graph.py prepare-postgres
```

脚本**只启动 PostgreSQL，不启动 Neo4j、不导入房源**。首次自动下载 PostgreSQL 16.10，安装和数据位于 `C:\ProgramData\PropertyGuruPortablePg`（避免中文路径导致 `initdb` 失败）。它会创建 `propertyguru_scraper`、`multi_agent_assistance` 两个角色及各自的开发库 / 测试库。

如果 `5432` 已被其他服务占用，脚本尝试使用 `54329`，只更新 `.env` 中指向 `127.0.0.1:5432` 的开发 URL，不修改远程配置。**连接工具也要改用实际端口**；终端已有的旧环境变量需同步更新或清除，否则会覆盖 `.env`。

### Docker：PostgreSQL 容器

以下为 Bash 命令；Windows 先用上面的命令准备 `.env` / Python，再执行相同的 Compose 和 Python 操作。

```bash
test -e .env || cp .env.example .env
test -d .venv || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r data/graph_requirements.txt
docker compose --env-file .env -f data/compose.yaml up -d --wait postgres
python data/knowledge_graph.py prepare-postgres
```

Compose 自动创建角色和数据库，但只在**新数据卷首次初始化**时执行 [建库 SQL](docker/postgres-init.sql)，不会自动导入房源。宿主机端口固定为 `5432`；冲突时先处理占用，Compose 不会自动换端口。

### 导入成功怎么看

`prepare-postgres` 应输出 `status=imported` 或 `already_prepared`，计数为 **21,431 套 RENT 房源、4,657 位经纪人、418,700 条图片 URL、D01–D28**，`price_history=0`。固定快照声明与数量定义在 [snapshot.py](storage/snapshot.py)，不是实时采集状态。

- 空 PG 库会校验团队 SQLite 快照、建表并导入；已准备好的 PG 库直接复用，**不再需要本地 SQLite 文件**。
- 非空但数量不符的库直接报错，不自动覆盖或清空。已确认属于同一快照的中断迁移，可安装 `data/requirements.txt` 后显式执行 `python data/main.py import-postgres --sqlite-path data/propertyguru.db --batch-size 500` 恢复，再运行 `prepare-postgres` 核验。
- 旧表缺列时，归档工具 `main.py init-db` 可补齐 `properties` 缺失的可空列及索引；不创建数据库、不迁移旧列类型或其他旧表结构。
- 迁移源 SQLite 以 `mode=ro` 打开；房源 / 经纪人按主键 Upsert，图片按唯一键去重，PG 中 JSON 使用 JSONB。
- **按批提交**；中断后保留已提交批次，修复后可重跑，不清空目标库已有的其他记录。迁移摘要的 `migrated` 是处理行数，不是新增行数。
- `--dry-run` 使用内存 SQLite，**不验证真实 PostgreSQL**，大快照可能占用较多内存。PG 故障不会自动回退到 SQLite。

## 3. 直接可用的 SQL / Python 查询

在 `psql` 或数据库客户端执行；`psql` 中 `\dt` 查看表，`\d properties` 查看字段，`\q` 退出。

### 检查数据是否已导入

```sql
SELECT listing_type, count(*) AS listings
FROM properties GROUP BY listing_type;
SELECT count(*) AS agents FROM agents;
SELECT count(*) AS images FROM property_images;
```

### 找 D05、月租 2,000–6,000 SGD、至少两卧的房源

```sql
SELECT listing_id, title, price, bedrooms, bathrooms,
       floor_area_sqft, full_address, nearest_mrt, url
FROM properties
WHERE listing_type = 'RENT' AND district_code = 'D05'
  AND price BETWEEN 2000 AND 6000 AND bedrooms >= 2
ORDER BY price, listing_id
LIMIT 20;
```

### 按房源 ID 查图片

`24685605` 是示例 ID，可替换为上一条查询的 `listing_id`。这里只保存图片 URL，不保存图片二进制。

```sql
SELECT source_page, image_type, image_url, display_order
FROM property_images
WHERE listing_id = 24685605
ORDER BY source_page, display_order, id
LIMIT 50;
```

### Python 只读查询

使用已安装的 SQLAlchemy / dotenv，在项目根目录运行；变量使用绑定参数，不拼接用户输入：

```python
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(".env")
url = os.getenv("PROPERTYGURU_READ_DATABASE_URL") or os.environ["PROPERTYGURU_DATABASE_URL"]
engine = create_engine(url)
with engine.connect() as connection:
    connection.execute(text("SET TRANSACTION READ ONLY"))
    rows = connection.execute(text("""
        SELECT listing_id, title, price, bedrooms
        FROM properties
        WHERE listing_type = 'RENT' AND district_code = :district
        ORDER BY listing_id LIMIT 20
    """), {"district": "D05"}).mappings().all()
    print([dict(row) for row in rows])
engine.dispose()
```

## 4. 表和字段怎么用

完整定义以 [models.py](storage/models.py) 为准，常用表如下：

| 表 | 主键 / 关联 | 用途与常用字段 |
| --- | --- | --- |
| `properties` | `listing_id`（BIGINT） | 房源；`listing_type`、`title`、`price`、`bedrooms`、`district_code`、`url` |
| `agents` | `agent_id`（BIGINT） | 经纪人；用 `properties.agent_id = agents.agent_id` 关联，读取 `name`、`agency_name` |
| `property_images` | `id`；`listing_id` 外键 | 图片；`source_page`、`image_type`、`image_url`、`display_order`；唯一键为 `(listing_id, source_page, image_url)` |
| `price_history` | `id`；`project_id` 关联项目 | 历史交易；`contract_date`、`transaction_type`、`price`、`psf`，不是挂牌价格变化 |

| `properties` 字段 | 类型 / 语义 |
| --- | --- |
| `listing_type` | `RENT` / `SALE`；筛选价格前先选类型 |
| `price` / `currency` | NUMERIC / 币种；RENT 为 SGD 月租，SALE 为售价，不能混合算均价 |
| `psf` | NUMERIC；SGD / 平方尺 |
| `bedrooms` / `bathrooms` | INTEGER；卧室 / 卫浴数 |
| `floor_area_sqft` / `land_area_sqft` | NUMERIC；室内 / 土地面积，单位平方尺，土地面积主要用于独立住宅 |
| `district_code` / `region_code` | D01–D28 / CCR、RCR、OCR |
| `full_address` / `postal_code` | 地址文本 / 邮编字符串；新加坡邮编为六位，保留前导零 |
| `latitude` / `longitude` | NUMERIC；经纬度，可能未知；不要用区域中心补充假坐标 |
| `nearest_mrt` / `mrt_distance_m` / `mrt_walking_mins` | 平台标注站名 / 米 / 分钟，未独立验证步行路线 |
| `project_id` / `agent_id` | BIGINT；项目 / 经纪人 ID，`project_id=0` 表示未知，不应据此合并项目 |
| `homepage_images` / `detail_images` | JSONB；列表预览 / 详情照片与户型图；逐张读取优先用 `property_images` |
| `posted_at` / `created_at` / `updated_at` | 带时区时间；发布 / 本库创建 / 更新 |

未知值为 `NULL`，不是 0。历史快照有 **8,631 条有效坐标、12,800 条坐标未知**；无坐标不能推断“没有附近配套”。面积、邮编等字段也有缺失，不能把业务猜测当作缺失原因的已核验证据。

SQL 中含联系方式、证照、房号和 `raw_json` 等字段，**不要直接把整行记录发送给网页或模型**。对上层应用优先使用 [只读 HTTP API](NEO4J.md#5-只读-http-api)，它按白名单输出规范明细、图片和图关系；认证、权限和 Agent 编排保持在上层。

## 5. 日常维护

| 操作 | 项目根目录命令 |
| --- | --- |
| 核验固定 PG 快照 | `python data/knowledge_graph.py prepare-postgres`（已准备好时只读核验数量） |
| 导出 CSV / JSON | `python data/main.py export --format csv`（或 `json`），输出到 `data/exports/` |
| 审计原始 SQLite 快照的字段缺失 | `python data/main.py check-sparsity --sqlite-path data/propertyguru.db`（不是 PG 审计） |
| 从固定 PG 快照重建图谱及地点距离 | `python data/knowledge_graph.py import`，前提见 [Neo4j 指南](NEO4J.md) |
| 只读验收 PG → Neo4j → HTTP | `python data/knowledge_graph.py verify --api-url http://127.0.0.1:8088` |

`main.py` 的统计、导出、旧迁移工具额外需要 `python -m pip install -r data/requirements.txt`。爬虫保留为历史采集工具，**不要对固定房源库执行 `run`**。`main.py sync-graph` 与 `knowledge_graph.py import` 共用完整投影实现，都会重建公共地点距离；推荐使用后者并执行 `verify`。Neo4j 同步失败不会回滚 PG，验收失败也不会自动删改数据库。

停止便携 PostgreSQL，或只停止 Docker PostgreSQL（都保留数据）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File data/stop_postgres.ps1
```

```bash
docker compose --env-file .env -f data/compose.yaml stop postgres
```

### 自管服务与只读角色

本机免密配置**仅允许回环地址开发**，不能暴露到局域网 / 公网。自管 PostgreSQL 需使用真实密码和最小权限；已有角色 / 库时跳过创建。管理员在 `psql` 中执行：

```sql
CREATE ROLE propertyguru_scraper LOGIN;
\password propertyguru_scraper
CREATE DATABASE propertyguru OWNER propertyguru_scraper;
```

把 `.env` 的 `PROPERTYGURU_DATABASE_URL` 改为自己的连接地址；密码中的特殊字符需 URL 编码，不提交真实凭据。可选只读角色的授权**必须在 `propertyguru` 库中**执行：

```sql
\connect propertyguru
CREATE ROLE propertyguru_reader LOGIN;
\password propertyguru_reader
GRANT CONNECT ON DATABASE propertyguru TO propertyguru_reader;
GRANT USAGE ON SCHEMA public TO propertyguru_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO propertyguru_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE propertyguru_scraper IN SCHEMA public
  GRANT SELECT ON TABLES TO propertyguru_reader;
```

然后设置 `PROPERTYGURU_READ_DATABASE_URL=postgresql+psycopg2://propertyguru_reader:<URL编码后的密码>@127.0.0.1:5432/propertyguru`；不要授予 reader 建表或写入权限。

### 备份与测试

用 PostgreSQL 客户端工具备份，恢复到**新建的独立房源库**，不要覆盖 Agent Runtime 库（Windows 工具也在便携安装的 `pgsql\bin\` 下）：

```bash
mkdir -p data/backups
pg_dump -h 127.0.0.1 -p 5432 -U propertyguru_scraper -d propertyguru -Fc -f data/backups/propertyguru.dump
# propertyguru_restore 需事先创建，且目标账号需有恢复权限
pg_restore -h 127.0.0.1 -p 5432 -U propertyguru_scraper -d propertyguru_restore data/backups/propertyguru.dump
```

```bash
python -m pip install -r data/requirements.txt -r data/graph_requirements.txt -r requirements.txt
python -m pytest data/tests -q
# 已启动真实 PG / Neo4j / API 后，重复执行只读全量验收
python data/knowledge_graph.py verify --api-url http://127.0.0.1:8088
```

`verify` 比对规范 SQL 数量、全部房源 ID / 投影字段 / 基础关系 / 坐标，以及公共地点配置半径内的距离边（错误距离、漏边、重复边均失败），并实际遍历 HTTP 房源分页、抽查 SQL 明细 / 图片 / 上下文和未知坐标错误；报告在 `data/.runtime/verification-report.json`。它不迁移、不补抓、不删除图节点；发生不一致时失败退出，不能把数量相同当作同源证明。

设置 `PROPERTYGURU_VERIFY_LIVE=1` 后，`test_live_data.py` 使用真实 PG / Neo4j 和 HTTP 契约做同样的只读验收。`PROPERTYGURU_TEST_DATABASE_URL` 则用于会写临时 schema 的 PG 持久化 / 迁移测试，**必须指向专用测试库**；未设置相应变量时，各自的真实服务测试跳过。

## 6. 常见问题

| 现象 | 先检查 |
| --- | --- |
| 连接被拒绝 | PostgreSQL 是否启动；实际端口是 `5432` 还是 `54329`；环境变量是否覆盖 `.env` |
| 提示角色 / 数据库不存在 | 使用了哪个实例；Docker 旧卷不会重跑初始化 SQL，可由管理员补建，勿删除数据卷 |
| `properties` 不存在 / 房源数为 0 | 确认连接专用房源库，提供团队快照后运行 `prepare-postgres`，不重新抓取 |
| `SQLite source not found` | Git 不包含 `data/propertyguru.db`；获取真实快照，不要新建空文件代替 |
| 迁移中断 | 修复连接后重跑；之前的批次仍在，不能把处理行数当作新增行数 |
| `/health` 正常但 HTTP 明细失败 | `/health` 只验证 Neo4j；请求 `/ready` 检查 SQL 表与图入口，再用 `verify --api-url` 做全量验收 |
