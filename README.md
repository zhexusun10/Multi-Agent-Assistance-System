# 新加坡租房多 Agent 工作台 · V1.1

可通过对话提取和修改租房需求，核查房源硬条件，解释推荐证据，生成包含协商诉求的跟进草稿，安排候选看房、跟踪回复与提醒，并下载推荐总结及当天会话文档。

跟进默认使用无需平台凭证的手动流程：**编辑并复制草稿 → 自行发送后确认已发送 → 粘贴经纪人回复并确认 → 系统理解、总结并起草下一步**。目前不打开外部平台链接。WhatsApp / Telegram API 发送接口保留为高级功能，需单独配置；没有凭证也可以完整使用手动跟进。详见[手动跟进使用与实现说明](docs/手动跟进使用与实现说明.md)。

推荐卡片除需求匹配外，还独立展示已收录的房型、卧室与卫浴数量、面积、地址、照片及周边地点；未提及的属性也会展示。桌面采用约35%对话、65%工作台布局，手机上下排列。补充资料不改变硬条件筛选、软偏好排序和证据，详见[房源展示与数据来源](docs/房源展示与数据来源.md)。

当前交付已接入 GitHub main `907cfa8` 中崔江山的原生 Recommendation Agent：完整候选池、四工具 Agent、硬条件验证、显式/样例浏览兴趣融合评分、最终重读校验和 SQLite 状态恢复均保留。默认 `RECOMMENDATION_MODE=native`；原基线仅供显式配置的回归使用。接入边界、算法、接口和验收见 [原生推荐接入说明](docs/Recommendation_Agent_原生接入说明.md)。

原生接口不能表达独立卫浴等部分原有需求，也不能解析 URA 区域边界 / LTA 出口目录；这些条件保留并显示待核实，原生报文不改写、不用旧基线补排。原生每组最多4条；不把其待核实候选的占位0分解释为原生评分。

## 直接使用

本机应用入口：**<http://127.0.0.1:8010/>**。

首次克隆的组员可先解压仓库自带的完整快照 `data/snapshots/propertyguru-2026-10-09.zip`，再安装依赖及导入数据库；文件哈希与操作见 [快照说明](data/snapshots/README.md) 和 [部署说明](LOCAL_DEPLOYMENT.md)。

先打开 Docker Desktop，在 PowerShell 中运行：

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System'
.\scripts\start-app.ps1 -SkipDataImport
```

首次初始化或需要核查导入时去掉 `-SkipDataImport`。脚本默认读取 `.env` 的数据模式；本机当前为完整快照，启动 PostgreSQL、正式 Neo4j、Chroma，以及数据 API、图服务和前端。样本库保留隔离。已运行时不会重复启动；修改代码或 `.env` 后用以下命令重启：

```powershell
.\scripts\stop-app.ps1
.\scripts\start-app.ps1 -SkipDataImport
```

停止脚本仅停止记录且创建时间匹配的本项目应用进程树，保留数据库容器和数据。完整依赖安装、全量数据切换见 [实现与运维文档](IMPLEMENTATION.md)。

建议依次尝试：

1. `帮我找租房，预算不超过5000新币，至少2房，D05`
2. `预算改为4500新币`
3. `最好超市直线距离800米以内`
4. `比较前两套，说明各自的证据和待核实项`
5. `请联系第1套房源的经纪人，先生成询问草稿`
6. 在工作台选择已核实的 WhatsApp / Telegram 渠道和地址，加入协商诉求、选择候选时段，保存、审核、模拟执行。
7. `生成当前推荐的总结文档`，下载 Markdown 或 HTML。

也可在新会话中提出：`先帮我找预算5000新币、至少2房、D05的租房，并生成总结文档`。系统按 A → B → C → D 顺序完成。

## 当前数据与能力范围

| 项目 | 当前实际状态 |
| --- | --- |
| 模型 | 本机 `.env` 的 DeepSeek，真实 API 调用；无密钥时可明确使用 `LLM_MODE=rules` |
| 当前房源 | 已接入 2026-10-09 完整 SQLite 快照，PostgreSQL 实际使用 21,431 套租赁房源；4,657 条经纪人资料、418,700 条图片链接 |
| 当前图谱 | 正式 Neo4j：21,431 个 Listing、3,716 个 OSM Place、710,468 条 NEAR_PLACE；另有 URA 区域、LTA 出口和校园关系 |
| 距离 | 官方区域边界、站点出口或 OSM 参考点的 geodesic / 直线距离；不能证明真实步行或通勤路线 |
| 地名目录 | 55 规划区、5 大区、188 站及 613 个有效具名出口；区域与站点分开解析，附近默认更近优先 |
| 推荐 | 江山原生服务 `8092`，LLM 四工具 + 完整候选池 + weighted-v1 融合评分；原生请求/返回保留在会话隔离的证据存档 |
| 跟进与看房 | 可编辑草稿与协商诉求、审核实际正文；WhatsApp Cloud / Telegram Bot 适配器、持久发送队列、回执与回复、月历及ICS、双方确认、独立跟进提醒与日总结；未配凭证时仅模拟 |
| 总结 | 可下载 Markdown、HTML，包含需求版本、结果、未知项及证据索引 |
| 意图与编排 | Orchestrator 三路融合：LLM 70%、Chroma 模板向量20%、关键词10%；可直接解释、知识问答与跟进 |
| 知识检索 | 8篇Markdown、Docker Chroma向量 + BM25、RRF融合、模型重排与逐句出处核验；资料整理自URA/HDB/IRAS/CEA/警方/法院 |
| 完整快照验收 | SQL/Neo4j 逐条核对、HTTP 全量分页与真实业务排序均已通过；数据结构见 [完整数据说明](docs/完整数据结构与接入说明.md) |

快照价格和页面来源不证明房源现在仍可租。家具、租期、整租范围、当前可租和真实路线等缺少证据的条件保留为 `unknown`；硬条件未知的房源单列为待核实，不能进入已验证推荐。

本轮已完成实际字段审计、120 条租房话术的前后评估、25 个真实对话的独立 SQL 业务核对及 58 项自动测试。`1B1B` 可按精确卧室/卫浴数量检索；独立卫浴仍需专用使用权证据。详情与评估限制见 [租房需求语义评估与字段审计](docs/租房需求语义评估与字段审计.md)。

## 代码入口

```text
frontend/                 对话工作台、证据与动作审核界面
api/main.py               FastAPI 同源入口、SSE 代理、静态资源
src/agents/               LangGraph master 和 A/B/C/D 角色图
src/domain/               需求、编排、解释、动作、知识业务规则
src/contracts/            Contract V1 Zod 模型与语义校验
src/adapters/             真实数据 API、远程推荐接口、本地推荐基线
recommendation/           江山原生 Python 推荐模块、契约、算法、工具、状态及测试
src/stores/               事务状态、版本、引用与哈希
src/tools/                权限、预算、缓存、熔断、执行追踪
skills/                   应用角色技能及边界
migrations/               业务表迁移；运行时直接读取
contracts/releases/       可执行 JSON Schema 和字段登记表
scripts/                  隔离样本导入、启动停止、契约导出
tests/                    单元、PostgreSQL、真实 HTTP 业务测试
evaluation/               浏览器用户旅程、验收报告和截图
data/                     原团队数据模块：采集、SQL、图谱、只读 API
```

## 测试与交接

```powershell
npm run typecheck
npm run typecheck:recommendation
npm test
.venv\Scripts\python.exe -m pytest recommendation/tests tests/test_native_transport.py -q
.venv\Scripts\python.exe scripts/import-recommendation-upstream.py --check
node --env-file=.env --import tsx --test tests/postgres-session.test.ts
.venv\Scripts\python.exe -m pytest tests/test_api.py -q
$env:BUSINESS_LIVE='1'
npm run test:live
.venv\Scripts\python.exe evaluation/browser_flow.py
```

真实业务测试使用已运行的样本应用，会创建独立测试会话并调用配置的模型。完整测试结果与业务断言见 [验收报告](evaluation/ACCEPTANCE.md)。

- [实现、架构、运行与交接](IMPLEMENTATION.md)
- [模糊需求与就近推荐调研、具体实施方案](docs/模糊需求与地理推荐调研及实施方案.md)
- [推荐接口说明及待双方确认的细节](contracts/releases/README.md)
- [完整数据部署记录](LOCAL_DEPLOYMENT.md)
- [团队原始运行框架说明](docs/ORIGINAL_RUNTIME.md)
- [数据模块说明](data/README.md) / [Neo4j 与空间语义](data/NEO4J.md)

## Follow-up 与两个消息平台

实现了可选的真实发送适配器，目前本机没有 WhatsApp / Telegram 凭证，因此仍以 simulation 运行，真实账号收发未验收。

- [WhatsApp 与 Telegram 账号申请、配置和真实收发验收](docs/WhatsApp与Telegram申请配置指南.md)
- [首次操作：平台代发消息逐步教程](docs/平台代发消息逐步操作教程.md)
- [仅使用 WhatsApp：完整配置、收发与跟进教程](docs/WhatsApp单平台完整操作教程.md)
- [Follow-up 工程设计、状态机、范围与测试](docs/Follow-up工程实现与验收.md)

“平台已受理”不代表已读或看房确认；待核实房源可以咨询和约看，未知条件仍保持未知。发出后可创建、调整、完成跟进提醒，生成当天会话与跟进文档。当前只实现两个消息 API，日历为本地日历与 ICS，文档为数据库存储和下载。

## Testing and acceptance evidence

Start with the [Group 42 QA handoff](evaluation/README.md) for the current English
report and tracker, recorded results, open issues, complete original evidence
and historical archive. The recommendation fixes and QA evidence are published
in [PR 2](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/2)
for review; overall system acceptance remains PENDING.
