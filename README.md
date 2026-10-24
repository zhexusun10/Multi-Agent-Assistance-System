# 新加坡租房多 Agent 工作台 · V1.1

可通过对话提取和修改租房需求，核查房源硬条件，解释推荐证据，生成包含协商诉求的跟进草稿，安排候选看房、跟踪回复与提醒，并下载推荐总结及当天会话文档。

需求提取使用模型优先流程：完整语义理解 → 两条相关few-shot → 严格JSON Schema/Zod校验 → 真实地点解析及完整需求变更预览 → 独立模型语义复核 → 有界纠错 → 推荐。语序、否定和地点关系由模型判断；规则不再先生成语义草案或过滤模型补充。197条真实模型样本覆盖21个登记字段，完整实现、契约、提示词、模板及评估见[需求提取LLM优先实现与验收](docs/需求提取LLM优先_实现与验收.md)。多轮修改的复核误判已单独修复，见[多轮需求修改修复](docs/多轮需求修改_原因与修复.md)。

Explanation Agent现为有依据的房源介绍与选房顾问：保留Recommendation名单与原排序，仅为这些房源读取已有SQL详情及图谱周边，让LLM逐套介绍月租、挂牌面积、卫生间数量、MRT及生活配套等有用特点，包括用户没主动提出的信息。相对低价或较大面积可作为选择亮点，不冒充实际排名原因。正文不再重复整份需求；结构、逐套覆盖、实体、来源、数值校验及独立语义复核约束真实表达。前端继续通过SSE和状态恢复显示正文，需求补充卡片仍接在后面。实现和本轮零付费验证范围见[Explanation Agent选房介绍V3](docs/Explanation_Agent_选房介绍实现.md)；[V2历史说明](docs/Explanation_Agent_自然语言解释实现与验收.md)保留作历史记录。

跟进默认使用无需平台凭证的手动流程：**编辑并复制草稿 → 自行发送后确认已发送 → 粘贴经纪人回复并确认 → 系统理解、总结并起草下一步**。目前不打开外部平台链接。WhatsApp / Telegram API 发送接口保留为高级功能，需单独配置；没有凭证也可以完整使用手动跟进。详见[手动跟进使用与实现说明](docs/手动跟进使用与实现说明.md)。

推荐列表只展示名称、月租、需求匹配和合并的待确认项，地点条件直接显示实际距离；左侧编号导航可跳转到任意房源。点击“展开详情”后，单套房源使用整个主工作区展示完整照片、已知资料和周边地点，缺失字段不占行；左侧找房对话保留。返回恢复原列表和滚动位置，方便反复比较。“联系经纪人”统一提供咨询草稿和看房时间选择。桌面保留约330px对话，其余空间给房源；手机上下排列。

推荐解释下方按原生 Recommendation 2.0 的 `selection.cold_start.active` 决定是否显示需求补充卡片：true 时仅询问仍有数据支持、尚未处理的字段，false 时不追问，不再使用 Mock 或应用自设分数阈值。自 2026-10-24 起，原生每轮按前 30 名合格房源正式分数的平均两两差 D 是否低于 0.01 判定 `active`（mpd-v1，起始值，未校准）；D 只表示分数区分度，不说明需求已足够或推荐正确，D 持续偏低时后续轮次也可能出现卡片。每张最多三项，提交后按 Requirement → Recommendation → Explanation 更新；房源列表保持用户操作前固定。说明见[Recommendation 2.0 合并与真实需求追问](docs/Recommendation_2.0_合并与真实需求追问.md)。

“目前预算即可”明确保留原预算，不表示取消预算。卡片补充后的数字引用检查、错误摘要和旧页面9套展示已修复；LLM写作异常时以已有事实提供经过校验的简短介绍，保留后台诊断，避免已完成推荐因介绍失败而整轮报错。原因和零付费回放见[卡片补充后解释失败修复](docs/需求卡片补充后解释失败_预算摘要与展示修复.md)。

详情页自动记录有效浏览时长，后台、长时间空闲和操作其他区域暂停计时。浏览不会刷新或更换当前列表；只有用户要求“按当前需求重新推荐”或修改找房条件后，才将已确认的合格浏览反馈传入原生推荐模块。默认质量门槛为每套累计15秒、至少两套、总计60秒；证据不足仍正常执行搜索。正常界面不再要求手动输入浏览秒数。使用、边界及验收见[真实浏览反馈实现说明](docs/真实浏览反馈_实现与验收.md)。

历史对话右侧的箭头可展开红色“删除对话”按钮；删除当前对话后切换到其他对话或创建空对话。后端按所有者校验并逻辑删除，停止该会话后续排队工作，已删除记录不能重新访问。详情浏览增加视口及焦点检查；用户选择有效未来看房时间并显式保存草稿后记录看房意向，取消或移除时间会撤回。下轮主动推荐的`behavior_context`携带累计浏览摘要和`book_viewing_listings`；当前列表不自动改变。完整字段、新增标记、触发规则及原生评分接入边界见[Recommendation Contract：浏览时长与看房意向](docs/Recommendation_Contract_浏览时长与看房意向.md)。

当前交付已合并 GitHub main `6a910ab` 中崔江山的 Recommendation 2.0 与本地最新实现：完整候选池、四阶段工具链、硬条件验证、软需求/累计浏览/看房意向融合评分、最终重读校验和 SQLite 状态恢复均保留。原生评分、同分抽样、冷启动探索、完整候选池归一化及最终重放校验均保留江山的新版本；冷启动判定于 2026-10-24 改为前 30 名分数平均两两差（mpd-v1），探索抽样本身不变。默认 `RECOMMENDATION_MODE=native`；原基线仅供显式配置的回归使用。接入边界、算法、接口和验收见 [原生推荐接入说明](docs/Recommendation_Agent_原生接入说明.md)。

原生接口不能表达独立卫浴等部分原有需求，这些条件保留并显示待核实。应用推荐入口现增加官方地理数据适配器：URA区域按区内0米、区外至最近边界的直线距离计算，LTA站点取已记录出口的最近直线距离，证据交给原推荐算法进行筛选、排序及最终重读核验；原OSM读取、评分和权重不变。缺少房源坐标时仍保持未知，详见[金文泰距离链路修复](docs/金文泰距离缺失_区域证据接入与数据边界.md)。页面每轮合计最多5套，正式推荐优先，待核实房源只补足剩余位置；导航、解释、当前房源选择和需求补充共用这一范围。原始接口及存档仍保留单独的待确认列表，不把其占位0分解释为原生评分。单位面积月租只在本轮同口径房源中有明显高低差异时介绍，并说明同样面积多花或少花月租的意义，见[整体比较与Top5](docs/Explanation_Agent_整体比较与Top5.md)。

已修复“裕廊东、交通站点与1B1B”组合在空排名后耗尽模型轮数的问题：应用层约束最终输出，并解析单个符合原生 Schema 的 JSON；不替换房源编号、不修改排序或最终校验。该组合目前正常返回待确认选项，仍不表示全部条件已满足。原因、实际测试与未接通边界见[推荐空排名循环修复](docs/推荐空排名循环_原因与修复.md)。

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
| 推荐 | 江山原生服务 `8092`，LLM 四工具 + 完整候选池 + fused-v2 软需求/行为融合评分与 select-v1 选择；原生请求/返回保留在会话隔离的证据存档 |
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
