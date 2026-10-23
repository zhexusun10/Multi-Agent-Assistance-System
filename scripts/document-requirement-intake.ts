import {readFile,writeFile} from 'node:fs/promises';
import {FIELD_RULES} from '../src/contracts/recommendation-v1.js';
import {INTAKE_EXAMPLES} from '../src/agents/requirement/few-shot.js';
import {INTAKE_PARAMETERS} from '../src/agents/requirement/extractor.js';
import {REQUIREMENT_ROLE_CONTRACT,intakeSystemPrompt,INTAKE_AUDITOR_PROMPT} from '../src/agents/requirement/prompt.js';
const report=JSON.parse(await readFile('evaluation/reports/intake-llm-summary.json','utf8'));
const skill=await readFile('skills/rental-requirement-intake/SKILL.md','utf8');
const fence=(value:string,language='')=>'```'+language+'\n'+value+'\n```';
let text=`# 需求提取 LLM 优先：实现、契约、提示词、模板与验收

实现日期：2026-10-21；多轮修改修复更新于2026-10-22。当前生产使用模型优先语义提取；本文替代旧版“规则先提取、模型补充”的实现说明。

## 执行流程

用户原话与既有需求 → 两条相关few-shot → LLM严格工具输出 → Zod结构/业务类型/原文校验和单位换算 → 受控地点目录解析 → 计算完整需求变更预览 → 独立LLM语义复核 → 需求持久化 → Recommendation。

复核在正式保存前读取实际合并后的effective_requirements，区分等价替换与真实取消。每次尝试的提案、合并预览、复核结论及错误写入后台tool_traces。多轮故障原因、修复与单独回归见[多轮需求修改修复](多轮需求修改_原因与修复.md)。

校验失败最多重提三次，单次调用最多30秒，提取及复核总预算90秒。反馈携带原始输入、原提案及具体错误，修正由模型重新提交；代码不通过正则改写语义，也不删除真实诉求来通过校验。认证/网络等非结构故障不偷偷切换规则。

原文解析器只保留为明确LLM_MODE=rules的离线模式与历史回归测试。生产不调用它预先提取、不按字段所有权过滤LLM补充，也不再用它判断“重新推荐”是否可以绕过提取。纯重新推荐由模型返回空变更；带新条件的重新推荐正常提取。

Schema允许的字段和受控枚举是接口约束，不是关键词提取。例句选择采用词项相关度检索，只决定给模型看哪两个示例，不决定最终字段或比较关系。模型负责语义；代码负责算术、字段/单位/类型/枚举、原文引用、旧ID合法性和真实地点查询。Requirement侧使用Zod和导出的JSON Schema；原生Recommendation侧继续使用原有Pydantic契约。

## 文件分工

| 文件 | 职责 |
|---|---|
| src/agents/requirement/contracts.ts | 模型输入输出、复核输出和应用草案的严格Schema；未知键拒绝；模型不能输出place_id、坐标或房源ID |
| src/agents/requirement/prompt.ts | 身份契约、完整系统提示词和独立语义复核提示词 |
| src/agents/requirement/few-shot.ts | ${INTAKE_EXAMPLES.length}条结构化例句与相关示例选择 |
| src/agents/requirement/extractor.ts | 严格工具调用、两个示例注入、语义复核、有限纠错和超时 |
| src/agents/requirement/validation.ts | 原文溯源、数值单位换算、受控枚举、移除旧ID及地点关系编译 |
| skills/rental-requirement-intake/SKILL.md | Requirement Agent身份、权限、完整性、否定/增删改和停止边界 |
| src/domain/requirements.ts | 兼容入口、需求合并；旧规则仅显式离线模式使用 |
| src/domain/workflow.ts | 地点查询、正式需求更新、多轮澄清恢复以及后续Agent调度 |
| contracts/requirements/intake-v2.schema.json | 由Zod导出的模型工具JSON Schema |
| contracts/requirements/intake-audit-v2.schema.json | 由Zod导出的独立语义复核JSON Schema |

## 输出与控制边界

模型输出upsert、locations、removals、clarifications、coverage。每一项带逐字原文引用。locations独立于普通属性，模型输出名称或受控类别，真实ID由目录查询补齐。MRT或公交站保留any_of，编译为PublicTransport；都需要则拆成两个独立条件。距离下限和区间也保留，不能全部改成小于。prefer_closer由模型明确给出，服务不再自行给所有硬距离添加软偏好。

面积sqm转sqft、公里转米由代码做确定换算。独卫与卫浴数量不互推，租期与产权不互推。只有明确挂牌来源时使用mrt_distance_m/mrt_walking_mins，普通站点距离走locations，完整通勤走commute_time。数据不足保留条件并在后续显示未知。distance_preference是应用生成的展示标记，用于区分无半径的附近和明确的软半径，避免用户明确的一公里在页面上消失；它不改变原生评分算法。

正式需求保持稳定ID。只改预算不删除交通、卧室等旧条件。取消一个设施按旧criterion_id处理，不能清空同字段其他设施。歧义澄清暂停推荐；回答后恢复已知条件并以模型的新澄清结果为准，不再用关键词保留旧问题。

## 字段登记表

| 字段 | 类型 | 比较符 | 规范单位 |
|---|---|---|---|
`;
for(const [name,r] of Object.entries(FIELD_RULES))text+=`| ${name} | ${r.kind} | ${r.ops.join(', ')} | ${r.unit||'—'} |\n`;
text+=`
可提取不等于已存在可验证事实，也不等于原生推荐支持此字段。此轮未添加公交站数据或OneMap接口，也未将MRT节点接入原生Place查询；相关要求被完整保留、传递和标为未知，不会宣称已满足，也未用旧算法补排。

## 实验与迭代

共${report.metrics.cases}条实际模型样本、40个语义分组，覆盖全部21个登记字段。每类采用中文口语、正式表述、英文、不同语序、否定/修饰和软硬偏好；另有复杂多条件及带旧需求的修改/取消。155条开发集用于迭代；42条保留样本在第四版冻结后首次运行，未用于例句编写和开发调试。few-shot是独立例句，不是这些测试查询的拷贝。

| 轮次 | 开发样本 | 完整通过 | 槽位F1 |
|---|---:|---:|---:|
`;
for(const r of report.iterations)text+=`| ${r.version} | ${r.metrics.cases} | ${r.metrics.exact_pass} | ${(100*r.metrics.slot_f1).toFixed(2)}% |\n`;
text+=`
v1保存原始报告，同时另存复核后的评分报告：四条挂牌时间金标准漏了platform_reported标记；两条先删旧预算再写新预算与替换等价。这六处评测问题已修正，没有改原始模型输出。后续扩展八条开发样本覆盖产权与挂牌地铁距离，因此不同轮次样本量有差异。

当前开发语料${report.development.exact_pass}/${report.development.cases}、原保留语料${report.holdout.exact_pass}/${report.holdout.cases}；合计${report.metrics.exact_pass}/${report.metrics.cases}完整通过，槽位精确率${(report.metrics.slot_precision*100).toFixed(2)}%、召回率${(report.metrics.slot_recall*100).toFixed(2)}%、F1 ${(report.metrics.slot_f1*100).toFixed(2)}%。${report.metrics.repair_cases}条发生有界纠错；${report.semantic_audit_passed}条通过独立模型语义复核；仅需求提取与语义复核P95为${report.metrics.p95_latency_ms}毫秒，不含后续全量推荐耗时。数字对应这份固定本地语料的一次实际模型运行，不推断为任意自然语言的普遍准确率。

${report.regression?`当前数字来自${report.regression.tag}对全部197条旧语料的真实模型回归，原有保留集在此轮已作为回归复用，不称为新的未见保留集。另有14条多轮修改场景使用真实地点目录与完整合并预览，报告为evaluation/reports/requirement-update-v9-model.json。`:''}

迭代解决了：示例数值泄漏、普通地铁距离与挂牌距离混淆、OCR误作地点、通勤附带无依据地点偏好、不强求误作软偏好、HDB型号澄清与审计员越界要求新字段、修复时复制示例原文。所有每条结果、原文、预期/实际槽位、纠错原因、时延和源码哈希都保留在evaluation/reports/intake-llm-*.json。

## 端到端与运行预算

scripts/test_requirement_model_workflow.py验证截图原句进入真实Recommendation请求、修改预算保留条件、重新推荐附带新条件、多轮卧室澄清恢复。scripts/test_requirement_model_ui.py验证桌面与手机可看到明确的1000米交通偏好及700米公园偏好。

最初LLM优先改造当轮的自动验收：TypeScript测试170通过、20项显式可选测试跳过、0失败；Python测试65通过。该轮真实业务工作流4项检查及桌面/手机页面记录在evaluation/reports/requirement-model-workflow.json和requirement-model-ui.json。2026-10-22多轮修改修复的当前测试、后端逐次跟踪与页面结果另见[多轮需求修改修复](多轮需求修改_原因与修复.md)，不把历史测试数量冒充当前源码重新执行的数量；均未发送外部消息。

测试中另发现全量多地点计算超过原生90秒预算：原始跟踪记录get_geographic_evidence约52秒，validate_hard_constraints约52秒，未进入完整排序就超时。使用原生已有配置机制，将总运行预算及LiveController构造参数设为300秒；HTTP等待450秒。config/recommendation-full.json只设置墙钟预算；模型轮数8、工具次数20、修复2和全部评分、权重、排序、完整候选池及最终重读不变。没有改原生算法内核。

scripts/with-project-env.mjs确保本项目.env不被父进程中另一项目的默认模型凭证覆盖。密钥只保留在本机.env，不进入报告。

## 复现

`+fence(`npm test
node --import tsx scripts/export-requirement-schema.ts --check
node --import tsx scripts/with-project-env.mjs evaluation/evaluate-intake-llm.ts --tag reproduce --split all --concurrency 3
node --import tsx evaluation/summarize-intake-llm.ts --tag reproduce
.venv/Scripts/python.exe scripts/test_requirement_model_workflow.py
.venv/Scripts/python.exe scripts/test_requirement_model_ui.py
.venv/Scripts/python.exe scripts/import-recommendation-upstream.py --check`,'text')+`

## 附录A：角色契约与参数

`+fence(JSON.stringify(REQUIREMENT_ROLE_CONTRACT,null,2),'json')+'\n\n'+fence(JSON.stringify(INTAKE_PARAMETERS,null,2),'json')+`

## 附录B：实际Skill全文

`+fence(skill,'markdown')+`

## 附录C：系统提示词模板

以下以空需求为例；实际运行注入真实既有需求、当天日期和Skill。

`+fence(intakeSystemPrompt({listing_type:'RENT',criteria:[]},'（运行时注入附录B的Skill全文）'),'text')+`

## 附录D：独立语义复核提示词全文

`+fence(INTAKE_AUDITOR_PROMPT,'text')+`

## 附录E：全部few-shot输入、上下文与严格输出
`;
for(const e of INTAKE_EXAMPLES)text+=`\n### ${e.id}\n\n用户：${e.query}\n\n既有需求：\n\n`+fence(JSON.stringify(e.base),'json')+'\n\n期望工具输出：\n\n'+fence(JSON.stringify(e.output),'json')+'\n';
await writeFile('docs/需求提取LLM优先_实现与验收.md',text);console.log(JSON.stringify({examples:INTAKE_EXAMPLES.length,fields:Object.keys(FIELD_RULES).length,characters:text.length}));
