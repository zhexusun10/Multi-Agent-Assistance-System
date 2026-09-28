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
├── main.py               # 命令行交互工具（init-db / run / stats / export）
├── requirements.txt      # 依赖包列表
├── tests/
│   └── test_cleaner.py   # 数据清洗测试用例（覆盖图片分离、具体地址等）
└── README.md             # 使用说明文档
```

---

## 🚀 快速上手

### 1. 激活 Python 环境
```bash
conda activate workshop
# 或直接调用环境中的 Python:
# /opt/miniconda3/envs/workshop/bin/python
```

### 2. 数据库配置
爬虫数据库独立于根项目 LangGraph 数据库：仅使用 `PROPERTYGURU_DATABASE_URL`，不读取根项目的 `DATABASE_URL` 或普通 `PG*` 环境变量。未设置 URL 时，使用专属 `PROPERTYGURU_PGUSER`（默认 `jerry`）、`PROPERTYGURU_PGPASSWORD`（默认空）、`PROPERTYGURU_PGHOST`（默认 `localhost`）、`PROPERTYGURU_PGPORT`（默认 `5432`）、`PROPERTYGURU_PGDATABASE`（默认 `propertyguru`）。数据库和角色需事先创建，`init-db` 创建表和索引，并补齐已知旧版属性列；账号需有建表权限。

例如：
```bash
export PROPERTYGURU_DATABASE_URL='postgresql://jerry@localhost:5432/propertyguru'
```

初始化数据库表与索引：
```bash
python main.py init-db
```

---

## 💻 常用 CLI 命令

### 1. 抓取数据并入库（默认自动包含详情页地址与高清大图）
```bash
# 抓取买房数据（默认抓取 5 页，自动提取门牌号、邮编、经纬度与分离图片）
python main.py run --type sale --pages 5

# 抓取租房数据
python main.py run --type rent --pages 5

# 买房和租房同时抓取
python main.py run --type all --pages 10

# 极速模式：跳过详情页抓取（仅抓取主页数据与卡片预览图）
python main.py run --type sale --pages 10 --skip-details

# 从指定页码开始抓取（支持断点续爬）
python main.py run --type sale --start-page 6 --pages 10
```

### 2. 查看数据库统计与图片分离看板
```bash
python main.py stats
```

### 3. 导出数据为 CSV / JSON
```bash
# 导出全部房源为 CSV（包含具体街道、邮编、经纬度及图片统计）
python main.py export --format csv --output singapore_properties.csv

# 导出租房数据为 JSON
python main.py export --type rent --format json --output rent_listings.json --limit 200
```

### 4. 运行单元测试
```bash
PYTHONPATH=. pytest tests/
# 可选：在独立测试库中使用临时 schema 验证真实 PostgreSQL 幂等与回滚
PROPERTYGURU_TEST_DATABASE_URL='postgresql://jerry@localhost:5432/propertyguru_test' PYTHONPATH=. pytest tests/test_persistence.py
```

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
