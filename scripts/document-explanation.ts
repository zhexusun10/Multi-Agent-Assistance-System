import {readFile,writeFile} from 'node:fs/promises';
import {EXPLANATION_ROLE_CONTRACT,EXPLANATION_PARAMETERS,explanationSystemPrompt,EXPLANATION_AUDIT_PROMPT} from '../src/agents/explanation/prompt.js';
import {EXPLANATION_EXAMPLES} from '../src/agents/explanation/few-shot.js';
const read=async(p:string)=>JSON.parse(await readFile(p,'utf8'));
const summary=await read('evaluation/reports/explanation-summary.json');
const dev=await read('evaluation/reports/explanation-'+summary.latest_development_run.tag+'.json');
const finished=summary.status==='passed';
const devMetrics=summary.latest_development_run.metrics;
const workflow=await read('evaluation/reports/explanation-model-workflow.json'),delivery=await read('evaluation/reports/explanation-delivery-ui.json');
const ui={...delivery,read_only:false,characters:delivery.cases[0]?.answer.length??0,answer:delivery.cases[0]?.answer??'最新页面模型验收因服务返回402中断，当前结果与待办请见explanation-delivery-ui.json。'};
const liveStatus=ui.status==='passed'?`桌面及手机页面已从真实输入框提交新问题，核对后台模型正文（${ui.characters}字符）、无浏览器错误或横向溢出。`:'最新页面模型复验因余额不足中断，不能将上一轮通过当作本轮完成；保留错误记录与待续测项。';
const skill=await readFile('skills/evidence-based-explanation/SKILL.md','utf8');
const fence=(s:unknown,language='text')=>'```'+language+'\n'+(typeof s==='string'?s:JSON.stringify(s,null,2))+'\n```';
let doc=`# Explanation Agent：自然语言解释实现与验收

实现日期：2026-10-21；2026-10-22更新分项解释及对话同步恢复。用户界面中的推荐解释由LLM生成；Recommendation算法、评分和结果顺序未改变。

${finished?`**Explanation 模块本轮模型回归已完成。** 同一版本${summary.metrics.cases}条均完成生成、校验、语义复核及独立金标准评分。${summary.regression?'旧语料作为回归复用，增加了卫生间数量、裕廊东缺资料与近距离比较场景，不称为新的未见保留集。当前线上解释路由和英文检查见evaluation/reports/explanation-model-workflow.json，桌面、手机及显示恢复见evaluation/reports/explanation-delivery-ui.json；完整A/B/C的历史记录单独保留。':'列明场景的真实工作台与桌面/手机页面也已完成验收。'}`:'**Explanation 模块验收尚未全部完成。** 具体已完成项、待办和阻塞见evaluation/reports/explanation-summary.json；不把旧轮结果当作当前版本全部通过。'}

这些样本验证解释模块的事实忠实、排序说明和自然表达，不代表 Recommendation 已覆盖所有地点类型与需求字段。之后发现的裕廊东、交通及1B1B组合触发推荐空排名循环，单独归因、修复并完成三条真实流程回归，见[推荐空排名循环：原因与修复](推荐空排名循环_原因与修复.md)。尚未接通的原生字段与地理证据仍保留为待确认。

2026-10-22另外发现旧语料未覆盖D05这类含数字的字符串：解释校验曾把D05拆成数量5而误拒绝真实回复。现已按字段类型保留完整字符串、逐段精确核对，邮编及中英文日期也保留其标识含义；不会把编码里的数字放入价格/距离许可范围。原始失败数据重放、原句全流程及网页验收见[邮区编码导致推荐解释失败：原因与修复](邮区编码导致推荐解释失败_原因与修复.md)。

## 原因与修改

原流程已经能把recommendation_explanation映射到Agent C。但C只调用模型选择listing_ids/criterion_ids，选择结果highlights没有用于正文，随后buildExplanation用代码逐条拼接统计、字段、证据ID和分数。因此“经过Explanation Agent”不等于“由LLM解释”。本轮删除该拼接分支及无效highlights路径。

现在是：Orchestrator识别解释意图 → Agent C加载角色Skill → 读取当前固定推荐及同会话原生存档 → 构建有来源的事实包 → 注入两条相关few-shot → LLM生成正文及后台引用 → Zod/来源/数值/长度校验 → 独立LLM复核 → 必要时反馈纠错 → 把模型text作为聊天summary。

搜索完成后的自动解释也走同一条生成路径。单独询问原因不调用Requirement/Recommendation，不增加推荐请求、不修改需求版本、不改变列表和排序；提交时原有陈旧结果保护继续生效。

## 文件与职责

| 文件 | 职责 |
|---|---|
| src/agents/orchestrator/agent-orchestrator.ts | 既有意图到C的映射，本轮不改变三路权重 |
| src/domain/workflow.ts | C实际调用generateExplanation，读取同会话exchange_ref；最终正文提交与过期保护 |
| src/agents/explanation/contracts.ts | 严格生成/复核JSON Schema和内部事实包类型 |
| src/agents/explanation/evidence.ts | 对接原始Recommendation结果、strengths/trade_offs/unknowns及实际评分贡献；有据事实和排序范围 |
| src/agents/explanation/prompt.ts | 身份契约、生成提示词、复核提示词和参数 |
| src/agents/explanation/few-shot.ts | ${EXPLANATION_EXAMPLES.length}条独立表达示例；问题相关度与结构化排序情形选两条 |
| src/agents/explanation/validation.ts | 严格字段、合法引用、固定集合/次序、数字及输出长度、工程信息泄漏校验 |
| src/agents/explanation/generator.ts | 真实LLM写作、独立复核、最多三轮纠错；直接返回模型正文 |
| skills/evidence-based-explanation/SKILL.md | 应用Agent C运行时实际加载的Skill，版本2.0 |
| src/domain/explanation.ts | 后台结构化证据及可下载文档记录；已删除诊断字段拼接成聊天回复的旧分支 |
| src/domain/errors.ts | 生成失败的简短用户提示，不退回字段报告 |
| contracts/explanation/ | 由Zod导出的生成和复核JSON Schema |
| evaluation/explanation-cases.ts | 独立金标准、排序/证据及数量与使用权场景；当前${devMetrics.cases}开发语料+${summary.holdout.cases}原保留语料 |

## Recommendation到Explanation的真实数据

已有criterion_results给出每项通过、未满足或未知；explanation_evidence给出符合/取舍/未知的引用。原生完整返回从同会话exchange_ref读取，校验request_id、conversation_id、requirements_version后，读取原生strengths/trade_offs/unknowns（这些是条件ID，不是自然语言句子），对应回真实条件和证据。

排序由native.scores.contributions、ordering、explicit_weight/implicit_weight和suppressed_features识别实际使用的因素。只有已知、参与评分且未被抑制的因素会进入解释；未知交通偏好不能说已经让它排前。后台保留原始数值，但不把分数、权重、覆盖率或候选数量放进模型的面向用户事实包。没有修改原生评分公式或重新排一次序。

硬条件是筛选门槛，预算门槛不等于按价格从低到高。仅一项有效距离偏好时，可以解释为有资料的房源按接近程度排序；多因素同时生效时不能说只看距离。实际用了浏览记录就简短说明辅助作用；样例不冒充真实浏览。已评分和未评分的待确认选项分开解释，同分或缺少区分依据不把第一套说成最好。

房源ID、来源和证据ID只在后台追溯。卧室0占位不会说成“0间卧室”。月租、距离可选择性保留，支持米转公里、合理四舍五入及有来源的租金差/预算余量。直线距离不换算步行时间，也不推断实际通勤体验。当前可租与今天价格须简短联系确认。

## 输出与失败边界

整体解释分为“您的要求、这些房源符合的部分、还需要了解的部分（若有）、为什么这样排序、注意”，最多五块。需求与符合项分别换行，对应已有记录的数值或范围；缺资料不说成未满足。没有比较信息时排序只说一句，不重复强调价格不决定排序。具体提问或要求简短时可省略完整需求回顾。中文通常250至500字，最多850字符；英文最多250词。输出结构仍只有version、focus_listing_ids和paragraphs；每段text保留纯文本换行与项目符号，support_refs留在后台。严格拒绝未知键、非法房源、改序、无来源数字及诊断术语；正文不向用户展示JSON。

事实包新增完整需求视图，将同一目的地的范围与更近偏好合并，内部排序尺度不会被误当成另一条半径。需求、逐套结果及证据保持对应，不能把一套有资料说成所有都有资料。已知的同地点距离允许由代码计算可追溯的差值；未知观察不参与运算。

逐套视图直接提供position、title、observed_value、unit，比较事实同时明确近远方向，防止将第一和第二套的事实说反。多偏好综合排序中，较远但更符合租金偏好的房源可以排前；解释取舍即可，不要求模型另算分数。生成器在每次尝试的最后重申回答语言，英文纠错使用英文，中文证据和复核反馈不能切换正文语言。

比较事实的引用递归追溯至原始观察，再关联对应的用户需求与当前房源名称，避免正确的半径、楼号或名称数字被误拦。浏览兴趣比较直接读取实际返回的整体匹配结果，双方有可用行为证据才作比较，不需要模型另算分数或猜具体偏爱面积；整体更匹配不等于每项特征单独更相似。

## 对话显示与恢复

历史记录显示，用户提出解释后约4秒后台已完成保存，第二次提问发生在约一小时后。没有证据表明模型生成一直阻塞。浏览器回归复现了新回答已经进入DOM却停在可视区外的问题：旧版每次状态刷新都重建聊天消息，再以平滑滚动追随，容易在后续刷新时丢失位置；再次发送会强制滚动，因而看起来上次回答与下次问题一同出现。原前端也缺少漏通知后的独立恢复。

现在只追加新消息，消息未变化不重绘；长解释定位开头，用户向上阅读时保留位置。SessionSync串行合并刷新、拒绝较旧修订及其他会话的迟到响应。SSE断线重连、心跳超时重建、回到页面同步，加上处理中约2秒/闲置约15秒的状态同步，保证无需再次提问触发刷新。代理明确禁止缓存与缓冲。模型正文仍在完成事实校验和语义复核后一次性发布，不展示未通过校验的草稿。

浏览器正常连接、保持心跳但丢掉完成通知、一次状态请求503这三条真实模型测试分别只发送一次问题，均自动显示回答，不修改需求或推荐；详情及完成到显示的耗时见evaluation/reports/explanation-delivery-ui.json。

独立模型复核事实、未知状态、排序归因、问题指代及自然表达，重点防止省略浏览因素、把“预算符合”说成“按价格排序”、把未知设施说成已满足，以及拿直线距离推断路线。最多三轮，包含结构修复和语义修复；调用或验证持续失败时保留诊断并给简短重试提示。没有规则报告回退。

## 实验迭代

`;
doc+='| 版本 | 样本 | 通过 | 备注 |\n|---|---:|---:|---|\n';
for(const [file,note] of [['v1-smoke','首次小批检查；其中两条独立评分上下文漏了已有面积/卧室，原报告保留。'],['v2-development','修正评分上下文与排序阈值引用，定位数值引用问题。'],['v3-development','支持正确比较租金与偏好阈值，72条通过。'],['v4-development','收紧长度后发现英文段落上限、遗漏浏览因素、租金余量引用等回归；一条评委误把记录价当已确认今日价。'],['v5-development','修正合法算术引用、英文长度、单因素排序边界和浏览因素覆盖。'],['v6-development','补充数字楼号及简称；78开发样本通过。真实八套混合列表仍需更短回答。'],['v7-development','补充混合列表示例、明确长度目标；65条通过，19条因402余额不足中断。'],['v8-development','包含英文语言校验的当前源码完整回归；旧版本结果保留，不混作最终通过数。']] as const){const r=await read('evaluation/reports/explanation-'+file+'.json');doc+=`| ${file} | ${r.metrics.cases} | ${r.metrics.passed} | ${note} |\n`;}
for(const [file,note] of [['v10-layout','分项展示初版；暴露混合列表的引用问题及独立评分JSON格式问题。'],['v11-layout','128条模型回归通过；随后补充真实近距离比较的路线推断保护。'],['v12-layout','余额恢复后135/136通过，保留英文纠错语言切换的原始诊断；后续针对性检查补充实体归属修正。'],['v14-layout','132/136通过，定位比较引用追溯及浏览兴趣方向证据不足并保留失败诊断。'],[summary.latest_development_run.tag,'当前冻结源码回归；充值续测仅执行未通过和未运行的样本，保留原始报告与合并来源。']] as const){const r=await read('evaluation/reports/explanation-'+file+'.json');doc+=`| ${file} | ${r.metrics.cases} | ${r.metrics.passed} | ${note} |\n`;}
doc+=`
当前开发语料执行：${devMetrics.passed}/${devMetrics.cases}通过；原保留语料${summary.holdout.passed??0}/${summary.holdout.cases??28}通过。两部分必须具有与当前源码相同的哈希才计入模型回归。指标包括事实忠实、排序正确、问题覆盖、自然表达、简洁；各项逐条独立模型评分，不是只检查生成器自报成功。有限语料通过不代表任意自然语言绝对正确。

所有实际回答、纠错原因、独立评分、异常及源码哈希保留在evaluation/reports/explanation-*.json；未覆盖旧轮报告。最终状态为${summary.status}。${finished?`完整${summary.metrics.cases}条中${summary.metrics.repaired}条经过有界纠错后通过，其余首轮通过；无生成异常。`:''}

真实工作台原句已运行完整21431套数据的推荐；后续解释沿用已接受结果。当前${workflow.checks.length}条解释请求，结果${workflow.status}；检查进入explain/Agent C、不新增Recommendation调用、不改需求或结果。英文问题是否确实用英文回答：${summary.workflow.english_answer_verified?'已通过':'待验证'}。旧版本英文串语言的问题及原始报告保留，最终检查脚本已加入确定性断言。${liveStatus}本轮TypeScript 205条通过、20条可选测试跳过；Python代理7条通过。未发送外部消息。

## 真实界面生成示例

以下是页面验收时真实工作台生成的中文原文，不是文档手写模板：

`+fence(ui.answer)+`

## 复现

`+fence(`npm test
npm run build
node --import tsx scripts/export-explanation-schema.ts --check
node --import tsx scripts/with-project-env.mjs evaluation/evaluate-explanation.ts --tag ${summary.latest_development_run.tag} --split all
node --import tsx scripts/with-project-env.mjs evaluation/continue-explanation.ts --tag v12-layout
node --import tsx scripts/with-project-env.mjs scripts/test-explanation-workflow.ts
.venv/Scripts/python.exe scripts/test_explanation_delivery_ui.py
.venv/Scripts/python.exe scripts/import-recommendation-upstream.py --check`)+`

## 附录A：身份契约与参数

`+fence(EXPLANATION_ROLE_CONTRACT,'json')+'\n\n'+fence(EXPLANATION_PARAMETERS,'json')+`

## 附录B：实际Skill全文

`+fence(skill,'markdown')+`

## 附录C：实际系统提示词

`+fence(explanationSystemPrompt('（运行时注入附录B的Skill全文）'))+`

## 附录D：独立复核提示词

`+fence(EXPLANATION_AUDIT_PROMPT)+`

## 附录E：全部few-shot

`;
for(const e of EXPLANATION_EXAMPLES)doc+=`### ${e.id}\n\n用户：${e.question}\n\n示例事实：${e.context}\n\n`+fence(e.output,'json')+'\n\n';
await writeFile('docs/Explanation_Agent_自然语言解释实现与验收.md',doc);console.log(JSON.stringify({characters:doc.length,examples:EXPLANATION_EXAMPLES.length}));
