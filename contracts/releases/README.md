# Recommendation Contract V1 可执行联调模型

> 当前默认提供方已切换为江山原生模块（原生契约 2.0）。本文原有字段表描述应用内部兼容模型 / 显式 baseline（V1，传输版本仍为 1.0，保留 `unverified_candidates`）；不能将它作为原生 `/recommend` 规范。原生权威定义是 `recommendation/contracts/*.py`、`recommendation/models/*.py` 和 `recommendation/contracts/schemas/*.json`，TS 镜像为 `src/contracts/recommendation-native.ts`。双向映射与缺口见 [原生接入说明](../../docs/Recommendation_Agent_原生接入说明.md)，原生 1.0 → 2.0 的逐字段变化见 [MIGRATION.md](../../evaluation/recommendation-exploration-2026-10-23/MIGRATION.md)。`recommendation-upstream-907cfa8.json` 保存原生文件来源及迁移后指纹（不修改）；本分支对清单内文件的每一处修改都登记在 `recommendation-downstream-patches.json`（目标文件、清单原指纹、修改后 LF 字节指纹、问题与变更说明），`scripts/import-recommendation-upstream.py --check` 同时核对两者。`scripts/compare-recommendation-upstream.py` 已退役，只作为 907cfa8 基线的历史证据（默认拒绝运行，`evaluation/reports/native-upstream-parity.json` 保留不重新生成）。

本目录把 Recommendation Agent Contract V1 的说明落实为可执行的本项目联调 profile，传输版本保持 `1.0`。它不是已经由四人共同冻结的新协议。主来源为 `D:\Real Estate Agent\design\sources\recommendation_agent_contract_v1.md`。

完整字段交接表、原V1差异和逐节样例见[双向接口与V1差异](../../docs/Recommendation_Agent_双向接口与V1差异.md)；原始文档已原样备份到 [references](../references/recommendation_agent_contract_v1.original.md)。可执行的合成请求/响应示例位于 [examples/current-v1](../examples/current-v1/README.md)，运行 `node --import tsx scripts/validate-recommendation-examples.ts` 可校验报文及完整字段覆盖。

- `recommendation-request-1.0.schema.json`：完整当前需求、反馈、previous_state_id、result_limit。
- `recommendation-response-1.0.schema.json`：身份、状态、已验证/待核实候选、证据、澄清、调整建议、覆盖统计及追踪。
- `executable-field-rules.json`：实际支持字段、值类型、比较操作、单位和 unsupported 标记。
- `criterion-registry-v1.1.json`：设计阶段的字段提案，供讨论；运行时以 TypeScript 的 `FIELD_RULES` 及其语义校验为准。

生成命令：`node --import tsx scripts/export-contracts.ts`。JSON Schema 只表达结构，不能替代 `validateResponse` 的业务一致性检查。后者还会拒绝身份不符、排名/数量不符、缺少证据、跨房源证据、数值矛盾、未知硬条件混入已验证推荐、得分贡献不一致等。

关键约定：

| 项目 | 本地可执行约定 |
| --- | --- |
| 房源 ID | 数字字符串，不能转浮点数处理 |
| 需求字段 | typed criteria，硬条件无权重，软条件正权重；用户未给相对权重时由 A 使用默认 1 |
| 未支持字段 | 合法诉求仍接收，判 unknown；不能伪装 malformed 或默认为通过 |
| 独立卫浴 | 现有 amenities 字段以 eq/private_bathroom 表示；没有室内使用权证据时判 unknown，不能由 bathrooms 数量推断 |
| 负卧室哨兵 | 数据导入规范化为 NULL；运行时负数不作为有效卧室数 |
| active_view | payload.active_duration_ms，正数，最大 3,600,000；带时区 occurred_at |
| save / shortlist | 有 event_id 的事件，不擅自修改显式需求 |
| explicit_attribute_feedback | payload.attribute、sentiment=positive/negative，可带原文 quote |
| 地理 | place_id 或 category；明确距离口径。平台 MRT 证据需 platform_reported；OSM geodesic 不冒充 walking |
| 无半径“附近” | soft place_proximity，数值为登记的评分参考尺度，绝不作硬过滤；本地 v2 使用 pivot/(pivot+distance)，单一地点偏好严格就近排序 |
| 地点类型 | URA:AREA:*、URA:REGION:* 为真实区域；LTA:MRT:*、LTA:LRT:* 为按轨道制式区分的站点出口集合。与 OSM:* 不同，可通过 /api/locations/{id} 取得实体和来源 |
| 结果 | success、needs_clarification、no_match、error；全部顶层字段均存在，未知统计为 null |
| 限额 | result_limit=1–20；本地 UI 请求 10（原生契约 2.0 按 min(10, result_limit) 返回已验证推荐，不返回待核实候选）；baseline 待核实候选最多返回 result_limit 条，响应校验上限 20 |
| 证据 | source_ref 必须能解析。data_api 引用由本项目持久化；远程推荐应实现其 recommendation_service 引用解析协议后再声称可点击回溯 |
| 重试 | request_id、body 保持不变；响应必须回显 conversation_id、request_id、requirements_version |

远程 `/recommend` 当前假设同一可信本机/开发环境，未实现跨组织认证。队友接入时需共同确认 source_ref / candidate_pool_ref 的跨服务解析方式、保留期、失效规则、行为贡献及评分版本。当前前端证据解析器完整支持本项目生成的 data_api 引用；不能将远程任意字符串自动当成本地 UUID。

当前基线返回的 `state_id=baseline:<request_id>` 是可回传状态标识，完整已评估候选 ID 集保存在 candidate_pool_ref，但基线每次仍重新核查当前数据集（full模式为21,431条完整快照），不承诺算法级候选复用。显式需求权重大于行为推断；基线对反馈只去重、保存和传输，不宣称已实现行为兴趣画像。
