# “Expected unit m” 问题排查与修复

> 后续更新：下文记录第一轮修复历史。用户进一步验证指出“附近必须先给半径”的处理过度，该策略已被替换。当前实现自动解析地区和站点，把无半径附近作为就近软偏好；实际“Clementi MRT附近3公里、通勤公交”可直接继续。最新方案见 [完整调研与实施](../docs/模糊需求与地理推荐调研及实施方案.md)，最新回归为 48 项 TypeScript 通过，并使用新的 [浏览器记录](reports/intake-browser.json)。旧版范围追问不再代表现行行为。

日期：2026-10-19。用户原句：

> 我要找金文泰附近，预算2500每月的房子，要求有独立卫浴，离新国立大学通勤30分钟以内

## 定位证据

数据库原操作 `94ec7723-065c-4981-8307-5b1385ed11a7` 为 failed。该操作仅有一条 agent=a 的 failed 任务，错误包含 `Expected unit m` 和 `Geographic target and distance_type required`。`recommendation_calls` 对应记录数为 **0**。所以错误发生在 Requirement Agent 校验阶段，与队友 B 尚未接入无关。

真实 DeepSeek 复现还出现过：给“附近”猜 2000m、把金文泰猜成 Mall 类别、输出不存在的 bathroom_type，以及把 price 的金额/单位放进 place_proximity。这些提案被严格校验拒绝是正确的；前一版没有在提案前识别确定的歧义，重试又缺少具体校验反馈，还把原始 Zod 错误直接展示给用户，是本次修复的缺陷。

## 修复

- 具名“附近/周边”缺少范围或口径时，生成合法澄清，不猜半径、类别或邮区。已明确的预算和其他条件保存在 pending 草案，用户原话仍留在操作上下文。
- 补充“离……通勤 N 分钟”的中文解析，使用 commute_time/min，目标名称保留原话，未指定交通方式时标 unspecified。
- 独立卫浴用现有 amenities/private_bathroom 表示，不能用 bathrooms>=1 替代。不同设施条件不再相互覆盖。
- 给模型完整字段登记表，禁止虚构字段；重试包含具体校验项。未通过的原始错误留在数据库任务记录，公开消息使用可读说明。
- 澄清回答按最新消息处理；只改预算仍保留未解位置问题，明确改用邮区才解除该问题。后续调用携带全部已保留条件。

## 验收

完整 TypeScript：**42 passed，0 failed，0 skipped**，含本问题的真实 HTTP + DeepSeek + PostgreSQL/Neo4j 业务回归。新增 Chrome 用户旅程 **3 项通过，0 JS 异常**。

1. 原句只运行 A，展示月租 2500、独立卫浴和 30 min 通勤，询问附近范围；没有推荐调用或内部 JSON 错误。
2. 仅将预算改 3000，位置问题仍保留。
3. 用户明确将位置改为 D05 后恢复 A→B→C，完整需求保留。独立 SQL 核对样本中价格/邮区通过的候选数为 194；因缺少独立卫浴及真实通勤证据，这些候选仍为 unverified，已验证推荐为 0。

第 3 步中的 D05 是**测试用户明确指定的替代位置条件**，不代表系统把“金文泰附近”自动等同于 D05；预算 3000 也来自测试中明确的用户修改。

- [真实业务报告](reports/intake-regression.json)
- [浏览器报告](reports/intake-browser.json)
- [修复后的澄清界面](screenshots/05-intake-clarification.png)
- [保留未知硬条件的结果界面](screenshots/06-intake-unverified.png)

复现：应用启动后运行 `BUSINESS_LIVE=1` 的 TypeScript 套件，或 `.venv\Scripts\python.exe evaluation/intake_browser.py`。前端刷新后重新发送原句即可使用修复逻辑；历史失败任务保留原始诊断，不改写为成功记录。
