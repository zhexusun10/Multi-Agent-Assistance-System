# Recommendation双向接口的完整合成示例

对应[字段表与V1差异说明](../../../docs/Recommendation_Agent_双向接口与V1差异.md)，实际模型来自 `src/contracts/recommendation-v1.ts`，传输契约版本仍为1.0。

本目录保留应用兼容模型的示例。当前默认的江山原生服务使用 `recommendation/contracts/` 和 `recommendation/examples/contracts/`；参见 [原生接入说明](../../../docs/Recommendation_Agent_原生接入说明.md)。

| 输入 | 输出 | 业务意义 |
| --- | --- | --- |
| request.standard.json | response.success.json | 硬预算/卧室/邮区通过，超市软偏好按距离计分 |
| request.private-bathroom.json | response.unverified.json | 新增独立卫浴硬要求，没有使用权证据，候选改为待核实；status=no_match |
| request.standard.json | response.error.json | 检索失败，未完成评估，counts为null |
| request.feedback.json | 无固定输出 | 四类反馈字段示例；反馈不改明确条件，算法决定是否重新评分 |
| request.conflict.json | response.needs-clarification.json | 两个预算条件结构合法但互相冲突，B可返回澄清而不假称完成评估 |

房源900000000001和fixture-design-v1均为合成材料，不是实际市场数据。不要把fixture报文当作生产推荐；完整快照的实际版本为2026-10-09。样例中的source_kind=fixture仅用于格式验收，远程生产服务的证据引用需要双方按交接文档约定解析方案。

在项目根目录运行：

```powershell
node --import tsx scripts/validate-recommendation-examples.ts
```

脚本调用真实Zod模型和validateResponse，核对八份JSON及字段索引。`field-index.json`列出所有181个已声明属性路径（包含两类候选的重复结构和可选native元数据），另列开放payload约定与21项field登记值；它不是新增业务接口。日期、单位、null、排名、贡献与证据引用均以当前运行约束为准。
