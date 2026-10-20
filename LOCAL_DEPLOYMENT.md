# 当前完整快照部署状态（2026-10-20）

已将 `D:\Real Estate Agent\design\propertyguru\propertyguru.db` 原样复制到项目 `data/propertyguru.db`，SHA-256 一致。PostgreSQL `propertyguru` 已导入 21,431 套租赁房源、4,657 条经纪人、418,700 条图片记录；正式 Neo4j `7474/7687` 已同步完整房源、OSM 周边与校园关系以及 URA/LTA 目录。`.env` 已切换为 full，网页推荐使用完整库。

数据核对报告：`data/.runtime/verification-report.json`；业务核对：`evaluation/reports/full-data-live.json`；结构与字段：[完整数据结构与接入说明](docs/完整数据结构与接入说明.md)。页面仍为 <http://127.0.0.1:8010/>。常规 `scripts/start-app.ps1 -SkipDataImport` 自动读取 full 配置，不会重置为样本。

以下保留的是**取得完整文件之前**的部署记录；其中“完整快照尚缺”等状态已由以上结果取代。样本库和会话历史均保留。

---

# 本机数据服务部署记录

部署目录：`D:\Real Estate Agent\Multi-Agent-Assistance-System`
检查日期：2026-10-19
仓库：<https://github.com/zhexusun10/Multi-Agent-Assistance-System>
检出分支：`main`，提交：`0c45bf3d692710b79af95e5d3de69295e0a72f2b`

## 后续 V1.1 应用部署（2026-10-19）

下文记录的是首次完整数据环境部署。随后已另外创建 **隔离的历史样本环境**：PostgreSQL `propertyguru_sample` 含 593 套房源（475 租赁），样本 Neo4j 在 `7475/7688`，已同步房源、真实 OSM 地点及空间关系；完整库 `propertyguru` 和 `7474/7687` 的状态仍如下文记录，未用样本填充完整快照。

应用工作台现已运行于 <http://127.0.0.1:8010/>，使用真实 DeepSeek、样本只读 API、本地推荐基线和模拟跟进执行。启动/停止应用请用 `scripts/start-app.ps1` / `scripts/stop-app.ps1`。实现与验收见 [IMPLEMENTATION.md](IMPLEMENTATION.md) 和 [evaluation/ACCEPTANCE.md](evaluation/ACCEPTANCE.md)。

## 首次完整数据环境部署结果

**数据库服务已部署，GitHub 中已有的 OSM 地点已导入；完整房源快照尚缺，房源知识图谱尚未完成。**

| 项目 | 实际状态 |
| --- | --- |
| Docker Engine / Compose | 29.7.2 / 5.4.0 |
| Python | 独立 `.venv`，Python 3.12.14；三个 requirements 文件已安装，`pip check` 通过 |
| PostgreSQL | `postgres:16`，健康，`127.0.0.1:5432` |
| Neo4j | `neo4j:5-community`，健康，HTTP `127.0.0.1:7474`，Bolt `127.0.0.1:7687` |
| OSM 地点 | 3,716 个 Place，包含 353 个 CampusBuilding |
| 校园关系 | 362 条 `PART_OF_CAMPUS` |
| Dataset 元数据 | 1 个节点，记录来源、许可、快照哈希和导入时间 |
| Neo4j 实际总量 | 3,717 个节点、362 条关系 |
| PostgreSQL 房源 | 0 条；表结构已创建 |
| 房源周边关系 | 0 条；等待房源坐标导入 |
| 完整快照验收 | 未通过：缺少完整房源快照；失败报告已保存 |

OSM 快照来自仓库中的 `data/datasets/singapore_places.json`，观测时间为 2026-10-09。已核对导入数量、校园关系数量及 Dataset 中的快照哈希。地点数据署名为 © OpenStreetMap contributors，许可为 ODbL-1.0。校园关系表示点位处于 OSM 校园多边形内的证据；不表示行政隶属或路线已经验证。

原仓库的统计接口只累加业务节点标签，未计入 Dataset，因此其 `total_nodes` 会显示 3,716；直接执行 `MATCH (n) RETURN count(n)` 得到包含元数据的实际数量 3,717。

## 配置和持久化

项目名固定为 `real-estate-agent`，使用 `data/compose.yaml` 和新增的 `data/compose.local.yaml`。后者设置 `restart: unless-stopped`。持久化卷为：

- `real-estate-agent_postgres_data`
- `real-estate-agent_neo4j_data`

PostgreSQL 已创建 `propertyguru`、`propertyguru_test`、`multi_agent_assistance`、`multi_agent_assistance_test` 四个数据库。`propertyguru` 中已创建 `properties`、`agents`、`property_images`、`price_history` 空表。Agent 运行时数据库与房源数据库分开；本次没有启动需要模型凭证的 Agent 应用。

`.env` 已从原仓库模板创建，保持 Git 忽略。采用仓库的本机开发配置：PostgreSQL trust、Neo4j 无密码，端口均绑定 127.0.0.1。当前未启动数据 HTTP API；本机 8088 已被另一项目占用，后续若启动本项目 API 可明确选择 8089。

运行记录：

- `data/.runtime/deployment-status.json`：实际容器、SQL 表数量及图数量。
- `data/.runtime/places-import.log`：地点导入与原仓库统计输出。由于房源尚未导入，其中关于 Listing 标签不存在的提示符合当前状态。
- `data/.runtime/verification-report.json`：完整快照验收失败报告。
- `data/.runtime/full-snapshot-verification.log`：验收命令输出。
- `data/.runtime/installed-python-packages.txt`：本次安装的依赖版本。

## 日常运行

先通过 Windows 正常打开 Docker Desktop，待引擎就绪，再在项目目录运行 PowerShell：

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System'
.\scripts\local-data.ps1 -Action Start
.\scripts\local-data.ps1 -Action Status
```

停止本项目数据库并保留数据：

```powershell
.\scripts\local-data.ps1 -Action Stop
```

在浏览器打开 <http://127.0.0.1:7474/browser/>，使用 `bolt://127.0.0.1:7687` 和无身份验证连接。可用下面的 Cypher 查看已有数据：

```cypher
MATCH (b:CampusBuilding)-[r:PART_OF_CAMPUS]->(c:Place)
RETURN b, r, c LIMIT 50;
```

只重新同步仓库自带的地点快照：

```powershell
.\scripts\local-data.ps1 -Action Places
```

该命令使用缓存快照，不会重新爬取房源或刷新 OSM。导入通过原仓库的 `knowledge_graph.py enrich-places` 完成。

## 补齐完整房源数据

实际 `.gitignore` 没有忽略整个 `data/`，而是忽略 `data/*.db`、临时目录及部分完整导出文件。修改这些规则只会影响以后哪些本地文件可以提交，不会让未上传的数据库出现在远端，也不会改变 `git clone` 能下载的内容。已检查当前远端分支、Git 历史中的数据库文件、LFS 和公开 Release；未找到完整数据库附件。管理员权限不能读取组员电脑上未上传的文件。

需要数据负责人提供 **2026-10-09 的完整 `propertyguru.db`，或对应 PostgreSQL 备份**。可以通过共享盘或 GitHub Release 提供下载链接；不必为了交接修改 `.gitignore`。本次没有使用旧 CSV 样本冒充完整快照，也没有重新运行爬虫生成不同日期的数据。

SQLite 快照的四表预期数量：

| 业务数据 | 数量 |
| --- | ---: |
| properties | 21,431 |
| agents | 4,657 |
| property_images | 418,700 |
| price_history | 0 |

拿到完整 SQLite 文件后，可保存在任意本地目录，通过绝对路径导入，无须先复制到仓库：

```powershell
.\scripts\local-data.ps1 -Action Start
.\scripts\local-data.ps1 -Action ImportSnapshot -SnapshotPath 'D:\实际下载目录\propertyguru.db'
```

如果文件已放在本项目 `data\propertyguru.db`，可省略 `-SnapshotPath`。命令依次执行：

1. `prepare-postgres`：只读检查 SQLite 快照，将完整快照导入专用 PostgreSQL 数据库。
2. `import`：从 PostgreSQL 同步房源、经纪人、楼盘、区域、MRT 和地点证据到 Neo4j。
3. `verify`：执行原仓库完整 SQL / Neo4j 一致性验收。

每一步失败都会停止后续步骤。原仓库校验会拒绝数量不匹配的快照，以及内容非空但数量不匹配的目标数据库。若交接的是 PostgreSQL 备份，先确认备份格式和恢复目标；上面的 `ImportSnapshot` 参数仅接收 SQLite 文件。

完整快照单独验收：

```powershell
.\scripts\local-data.ps1 -Action Verify
```

在当前仅有地点数据的状态下，此命令应当失败，不能作为房源推荐服务已经就绪的证明。

## 本次 Docker 启动问题

2026-10-20新增意图与知识检索：`data/compose.local.yaml` 部署Chroma（本机8002、持久卷），`scripts/start-app.ps1` 自动导出42个意图模板并索引8篇Markdown，再启动检索服务8091。首次需要下载本地多语言MiniLM模型，缓存到`data/.runtime/models/`。无需新增embedding密钥；对话、改写和重排继续使用`.env`的大模型。配置、索引重建、来源及验收命令详见 [Orchestrator与RAG重构记录](docs/Orchestrator与RAG重构记录.md)。已有服务运行时先停止或只重启所改组件，不要重复启动。

首次从代理终端启动 Docker Desktop 时，后端因 AppData 下 `sailor-ingest.sock` 访问错误退出。已关闭本次失败进程，通过 Windows Explorer 正常应用启动路径重启，引擎恢复。没有执行恢复出厂设置、删除 Docker 数据或更改其他项目容器配置。以后可优先从 Windows 开始菜单正常启动 Docker Desktop。
