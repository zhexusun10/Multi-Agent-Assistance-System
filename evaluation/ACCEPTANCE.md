# V1.1 本机业务验收记录

## 2026-10-19 Follow-up / External Tools 增量验收

本次已实现 WhatsApp Cloud API、Telegram Bot API、受控联系人线索、协商草稿、实际发送正文审核、持久队列、回执和回信、月历及 ICS、发送后可修改的跟进提醒、每日会话文档。当前没有真实平台凭证，仍为 simulation；**没有真实账号收发验收结论**。

- 最终 TypeScript 全量回归：**80 通过，0 失败，0 跳过**，包含真实 DeepSeek、PostgreSQL、Neo4j、25条独立 SQL 对照业务场景。消息平台使用注入的假 HTTP，测试未向外发送消息。报告：`reports/typescript-followup-v1.2.txt`。
- 消息平台适配器6项测试；真实 PostgreSQL 发送业务测试包含10个子场景，验证单次提交、审批、超时和进程中断、签名、去重、乱序、过早回执、原始来信时间、回复隔离和提醒。报告：`reports/followup-delivery-business.json`。
- Chrome 用户流程8项检查全部通过，**0 page errors**：真实 Queenstown 待核实候选 → 选房 → 联系人线索 → 未配置 WhatsApp 模板拦截 → Telegram 合成联系人 → 协商1800 → 月历未来日期并保留未保存表单 → 审核 → 模拟执行 → 发送后提醒改期/完成 → 双方同意 → ICS → 当天总结下载 → 刷新保留。报告：`reports/followup-workflow-browser.json`。
- 补充 HTTP/对话检查4项通过：无推荐也能通过 D 生成日总结、文档保留会话、所有者隔离/非法月份/未认证 webhook 拦截、缺凭证保持模拟。报告：`reports/followup-chat-api.json`。
- FastAPI 回归：`tests/test_api.py` **7 passed**；TypeScript 类型检查与 `git diff --check` 均通过。

截图：`screenshots/11-area-distance-context.png`、`12-followup-workbench.png`、`13-daily-followup-summary.png`。申请配置步骤见 [WhatsApp与Telegram申请配置指南](../docs/WhatsApp与Telegram申请配置指南.md)，工程边界见 [Follow-up工程实现与验收](../docs/Follow-up工程实现与验收.md)。

以下保留前次 V1.1 验收记录；当前消息能力以本增量及工程文档为准。

验收日期：2026-10-19。位置：`D:\Real Estate Agent\Multi-Agent-Assistance-System`。

结论：**历史样本部署下，A/C/D + master + 工具 + UI 的本次验收通过。**使用真实 DeepSeek API、PostgreSQL、Neo4j 和实际 HTTP / Chrome 用户旅程；完整房源快照、队友最终推荐 Agent 和真实消息投递不在已通过范围内。

## 环境和结果

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| TypeScript 类型检查及构建 | 通过 | `npm run typecheck`；启动脚本执行 `npm run build` |
| 全部 TypeScript 测试 | **58 passed、0 failed、0 skipped** | [最新完整日志](reports/typescript-rental-final.txt)；含 120 条语义回归、25 条独立 SQL 业务核对、原有真实业务/目录/空间/PostgreSQL 测试 |
| FastAPI 测试 | **7 passed** | `.venv\Scripts\python.exe -m pytest tests/test_api.py -q` |
| 原团队数据模块 | **154 passed、3 skipped** | 配专用 PostgreSQL 测试库后运行 `pytest data/tests -q`；1 条依赖弃用提示，不影响结果 |
| 浏览器用户旅程 | **7 项通过，0 JS 异常** | [browser-flow.json](reports/browser-flow.json)、[截图](screenshots/) |
| 截图问题浏览器回归 | **3 项通过，0 JS 异常** | [intake-browser.json](reports/intake-browser.json) |
| 户型/独卫/取消要求浏览器回归 | **5 项通过，0 JS 异常** | [rental-browser.json](reports/rental-browser.json) |
| 120 条话术抽取 | 规则与真实模型模式均 120/120 通过 | [最终模型报告](reports/intake-after-model.json)、[配对比较](reports/intake-comparison.json)；合成回归集，非盲测，标注纠正的 R058 不计入前后比较 |
| 25 条真实推荐业务 oracle | 全部通过 | [rental-business-oracle.json](reports/rental-business-oracle.json)；独立 SQL 核对数量和展示房源，未把字段存在当业务正确 |
| 依赖一致性 | 通过 | `pip check` → No broken requirements found |
| Compose 配置 | 通过，数据库健康 | `docker compose ... config --quiet` 及实际健康检查 |
| 本地配置与 Git | `.env` 被忽略；交付源码和报告未发现实际 API key 字符串 | 凭证值不写入本报告 |

数据测试的 3 个跳过项为：完整固定快照的一项联库验收，以及依赖本地完整快照的两项稀疏度检查。专用 PostgreSQL 持久化测试已实际运行。不能把这些跳过项计为通过。

## 真实业务结果

机器可读报告：[business-live.json](reports/business-live.json)、[advanced-live.json](reports/advanced-live.json)、[intake-regression.json](reports/intake-regression.json)。不是仅检查响应字段存在，关键断言如下。

| 场景 | 预期及实际验证 |
| --- | --- |
| 预算 ≤5000、至少 2 房、D05 | 独立 SQL 得出 **65 套**符合，系统 eligible=65；评估 **475** 套租赁，展示 5 套，每套硬条件均通过 |
| 不符合和未知 | 缺卧室等证据单列 unverified；确认的硬失败优先于 unknown；不会用软偏好弥补硬失败 |
| 家具齐全为硬要求 | 样本缺少可信家具事实，已验证推荐为 0，候选列为 unknown，不编造已满足 |
| 预算改 4500 | 保留原卧室及 D05，预算 criterion_id 不变，实际需求版本增加；重复相同预算不重复增版 |
| 超市距离软偏好 | 独立读取 Neo4j **554 个 Supermarket** 点，以不同球面公式重算距离与排名；前 5 名一致，距离误差 <0.01 米；65 套硬匹配未因软偏好被排除 |
| 复合请求 | 一条“找房并总结”实际产生 **A → B → C → D**，总结绑定本轮推荐 ID |
| 连续修正 | 快速发送预算 4800、4500，前一操作 superseded，最终只以 4500 为准；重复同一消息 ID 不产生新推荐 |
| 解释及对比 | 保留推荐 ID/需求版本，直接回答真实房源名称、租金、月租差异、逐项证据和未知项，不额外跑推荐 |
| 收藏反馈 | 同 event_id 重投只保存一次，下一调用传入一条反馈；新请求 ID、相同需求版本、正确 previous_state_id |
| 联系草稿 | 未核实联系人不能审批；无审批不能执行；编辑后旧审批失效 |
| 模拟执行 | 同修订反复点击只有一条 action_executions，external_id 使用 simulated 前缀 |
| 看房 | 使用未来新加坡时间；同客户或联系人时间重叠被拒绝；双方确认与模拟提交分开，单方标记不能 confirmed |
| 旧草稿 | 推荐/需求改变后拒绝继续执行旧草稿 |
| 无匹配 | 预算 1000 时不自动提高预算；调整建议引用实际房源证据 |
| 总结下载 | 实际下载 HTML / Markdown，含当前预算、需求版本、来源请求、待核实事项和证据 |
| 状态询问 | “联系了吗”解释本地模拟事实，不宣称邮件已真实发送 |
| 到期提醒 | 实际后台扫描产生一条持久提醒事件；没有外部发送 |
| 权限及引用 | 本会话证据可读，另一随机 owner 访问该会话被拒绝；原始数据引用可回溯 |
| 事件及恢复 | 单调 event_id、全量需求快照、SSE 游标竞态回归通过；真实 PostgreSQL 的 checkpoint/角色隔离/中断任务恢复集成通过 |
| 金文泰/独立卫浴/通勤原句 | 自动解析真实区域并按就近软偏好排序，不追问半径；补“公交、Clementi MRT附近3公里”正确更新为站点 3000m，保留预算、独立卫浴、30min；旧版遗留的两条澄清也能解除 |
| 新加坡地点目录/KG | 55 区及 5 大区中英名称逐一通过，188 站/613 具名出口有来源；样本图已写入区域/站点/出口，593 条房源区域归属关系 |
| 1B1B + 月租 ≤2500 | 实际 eligible=4、unverified=1、excluded=470；独立卫浴不从数量推断，新增独卫后进入待核实，取消后恢复并保留原条件 |

真实模型验收发现并修正了两个语义问题：软偏好漏填权重、明确“最好”偶尔被模型标成 hard。代码现在补默认权重 1，并按确定性的用户措辞约束硬软属性；非法显式权重仍会拒绝，数值、单位和原文引用继续严格校验。回归测试覆盖这两个故障。

## 浏览器验证

使用项目 `.venv` 中 Playwright 调用本机真实 Chrome，访问 `http://127.0.0.1:8010`。完成：对话检索真实房源、打开证据、修改预算、编辑并核实联系人、审批及模拟执行、实际下载文档、刷新恢复、移动端页面宽度及 JS 异常检查。

- [推荐界面](screenshots/02-matching-listings.png)
- [跟进工作台](screenshots/03-action-review.png)
- [移动端](screenshots/04-mobile.png)
- [浏览器实际下载的总结](reports/browser-summary.md)
- [无半径就近检索界面](screenshots/05-nearby-area.png)
- [MRT 3 公里及公交补充结果](screenshots/06-nearby-mrt-3km.png)
- [1B1B 实际匹配](screenshots/07-layout-1b1b.png)
- [独卫专用权缺证据说明](screenshots/08-private-bathroom-unavailable.png)

## 复现

先启动 Docker Desktop 和应用；密钥填写在本地 `.env`，不要放入命令、测试或聊天记录。

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System'
.\scripts\start-app.ps1 -SkipDataImport
npm run typecheck
$env:BUSINESS_LIVE='1'
node --env-file=.env --import tsx --test tests/*.test.ts
.venv\Scripts\python.exe -m pytest tests/test_api.py -q
$env:PROPERTYGURU_TEST_DATABASE_URL='postgresql+psycopg2://propertyguru_scraper@127.0.0.1:5432/propertyguru_test'
.venv\Scripts\python.exe -m pytest data/tests -q
.venv\Scripts\python.exe evaluation/browser_flow.py
.venv\Scripts\python.exe evaluation/intake_browser.py
```

只有不启用 BUSINESS_LIVE / TEST_DATABASE_URL 的 `npm test` 会跳过集成项，不能代替上面的全量命令。业务套件写入独立会话和模拟记录，会调用实际配置模型；数据持久化测试只在专用测试库的临时 schema 内运行。

## 尚未验收通过的内容

- **完整 2026-10-09 快照**：尚缺数据库，完整库房源为 0。`data/.runtime/verification-report.json` 保留 failed，不用样本测试替代。
- **队友 Recommendation Agent**：尚未交付远程服务；已实现接口网关及校验，当前业务实测使用明确标记的本地基线。行为兴趣重排未宣称实现。
- **外部邮件/销售 CRM/日历**：无真实提供商连接；动作执行仅 simulation，不能据此证明已联系销售或预约成功。
- **向量 RAG 与法规答案**：无合格正文和向量接口，当前只做经审阅系统说明检索，明确 vector_enabled=false。
- **生产运行条件**：没有完成公网认证、服务间鉴权、负载压测、进程强杀混沌测试和生产备份演练；当前交付目标是本机小组项目。
