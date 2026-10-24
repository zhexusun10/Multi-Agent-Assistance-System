# Recommendation 2.0：合并本地最新版与真实需求追问

2026-10-24。远端基线为GitHub main `6a910ab13fed5e87b82da5b38255b6655d2636b5`，包含`feat/recommendation-exploration`及`ff40fb6`的改进。本地修改先提交为`a29e8bf`，并保留`backup/local-before-recommendation-v2-20261024`。本次为三方合并，不以远端旧版页面或Explanation覆盖本地最新版，也不把旧推荐内核覆盖回去。

## 合并原则与实际变化

- 推荐核心采用江山当前完整实现：四阶段工具链、硬条件完整合格池、池缓存、原生地理解析、fused-v2评分、select-v1同分选择/冷启动价格分层探索、最终重读及重放核验。排名、距离/价格归一化、相似度和权重公式不做应用层简化。
- 累计浏览和保存有效看房时间的草稿现在通过behavior_context参与原生评分；增量feedback事件只校验记录，避免同一浏览时长重复计分。原生新增自身相似度为1、硬条件不再自动排除行为特征、无区分度特征跳过等规则全部保留。
- 原生请求沿用1.0主契约并加入behavior_context和cold_start；返回为2.0。TypeScript严格镜像、Python模型和生成Schema同步。原始请求/返回完整存档，工作台继续使用既有应用投影，避免其他Agent与页面重写。
- 保留本地最新逐套选房介绍、整体比较和口语表达、距离去重、页面布局、预算摘要、浏览记录及Top5统一展示。应用实际请求result_limit=5，而非请求10再随意裁前五；原生SDK仍支持其完整的1至10范围与原生默认值。
- Explanation需要解释排序时，事实包包含抽样/探索的真实选择机制，不把抽样入选说成分数更高或第一套更好；看房意向不说成已确认预约。历史原生1.0分数及存档有单独只读兼容器。

## 卡片触发与循环

权威信号路径为`RecommendationResponse.selection.cold_start.active`，应用投影中完整保存在`recommendation.native.selection.cold_start`。

| 原生结果 | 应用行为 |
| --- | --- |
| evaluated=true且active=true，信号与本次pool_key一致 | Requirement Agent规划最多三个适用且尚未处理的问题；存在问题才显示卡片 |
| active=false | 不生成卡片，现有OPEN卡片结束；不会因为条件少而强行追问 |
| 旧版或缺失/无效信号、错误结果、过期请求或数据版本不一致 | 不猜分数，不回退Mock，不生成新卡片 |
| active=true但字段已问完或用户已选择暂不补充 | 不重复追问 |

江山提供的`round-3.response.json`已保存为契约回归样本，严格2.0校验通过。其中C=1.0、B=4、S约0.955，阈值分别0.8、5、0.9，原生已返回`active=false`，所以该例不再出现补充卡片。应用只遵守原生标志，不复刻这些阈值或稳定性算法。

生成时保存`trigger=native_cold_start`和当次原始诊断；内部诊断不写入用户文案。选题继续使用已有常用字段和真实覆盖率、差异性规则，已说出的字段不再问，未知字段不伪造成已知。

用户提交卡片 → 校验并合并结构化需求 → Requirement → Recommendation → Explanation → 再检查新的cold_start标志。只有用户主动提交或要求重荐才更新房源，浏览本身不会跳动当前列表。

只有被编排器接受的结果才更新会话cold_start退出状态；PostgreSQL迁移007追加该状态列。该状态与原生pool_key一起发送到下一轮，池变化后是否重新进入探索由原生规则决定。旧SQLite和历史会话数据不删除，V2状态与传输绑定使用原生的新版本结构。

## 地理数据兼容

新原生模块已直接支持URA边界、LTA最近已记录出口和OSM坐标距离，替换了应用旧版Node官方距离桥接在生产路径中的职责，实际运算采用原生内核。应用仅给原生地点目录补入本地已有、经哈希核验的牛车水与荷兰村街区记录，并把其版本纳入缓存身份。已由Requirement Agent验证的街区ID及精确别名保持街区身份，防止被同名站点替代；错误ID/名称组合仍返回歧义。没有改写用户的目的地或填补缺失坐标。

## 主要入口

- `src/agents/requirement/refinement/trigger.ts`、`service.ts`：真实触发、计划持久化和提交。
- `src/domain/cold-start.ts`、`workflow.ts`、`migrations/007-recommendation-cold-start.sql`：接受结果后持久化退出状态，下一轮携带。
- `src/contracts/recommendation-native.ts`、`src/adapters/native-recommendation.ts`：严格2.0契约与无损投影。
- `recommendation/`：江山的新原生模块；`app_geography.py`仅保留应用数据兼容。
- `src/agents/explanation/`、`skills/evidence-based-explanation/SKILL.md`：保留本地介绍能力并适配新评分和选择原因。

原始907cfa8来源清单保留，39项V2下游声明来自远端并通过指纹检查；没有重写清单以掩盖核心变更。远端根目录的`tmp_ynoxj_n`临时SQLite、请求和运行痕迹未纳入交付，校准资料、实现与验收记录保留。

## 验证

当前已通过328项Node测试、470项Python测试以及桌面/手机端11项真实前端交互回放，均为0模型请求。Node另有22项依赖专用环境的测试默认跳过。覆盖准确提供的round-3返回、active=true/false、非法/缺失信号、旧版兼容、字段耗尽、来源过期、提交与历史卡片、Top5、原生多轮行为、地区/站点及相同分数抽样。源码构建和原生指纹检查通过。

### 2026-10-24 Docker启动后的真实联调

PostgreSQL、Neo4j、样例Neo4j及Chroma四个容器健康。正式服务8010/3001/8089/8091/8092已启动，迁移007已落库，正式Recommendation继续使用live控制器。预检确认SQL房源21,431套、图谱房源21,431套、55个规划区、188个站点，以及牛车水/荷兰村两个街区投影。

真实数据验收在随机隔离PostgreSQL schema和独立原生SQLite状态中运行，通过真实数据API、Neo4j、Recommendation HTTP、WorkflowService、正式FastAPI页面代理与Chromium完成。浏览器不拦截或伪造任何API响应。验收结束删除隔离schema并停止自建测试进程，正式服务保留运行。

| 步骤 | 真实结果 |
| --- | --- |
| NUS直线3公里内、月租不超过2,500新币 | 21,431套中239套合格，返回5套；原生active=true，生成补充卡片 |
| 页面编辑但尚未提交 | 当前五套列表不变；刷新恢复表单选择 |
| 卡片选择恰好1间卧室、保持预算；若询问更近则选更近优先 | 只提交一次结构化请求，真实A→B→C执行，预算保持2,500、不添加理想月租偏好 |
| 新推荐 | 合格池5套，展示5套；原生small_pool、active=false，不生成新卡片 |
| 原生多轮状态 | 第二轮previous_state_id准确引用第一轮state_id，未重建假状态 |
| 页面更新 | SSE与状态接口自动显示新房源和解释，无需再提问；历史卡片只读，刷新后仍正确 |
| 重复提交 | 同提交ID和内容幂等返回；修改提交ID的重复请求被拒绝 |

冷启动探索会抽取不同样本，所以第三个适用问题可能是房屋类型，也可能是更近偏好；验收根据服务实际返回的题目操作，不固定伪造题目。

**验证边界：**原生测试实例采用scripted控制器安排四个工具的调用顺序，使用江山实际过滤、打分、选择和验收代码及真实SQL/KG数据，不Mock推荐结果、不改正式控制器。首轮需求以结构化形式进入A，验证的是卡片合并链路，未重复付费测试自然语言意图识别/需求提取。零模型闭环使用实际Explanation证据恢复路径；不能将其写成LLM已通过。

另选同一代表场景的末轮Explanation进行真实模型补测，关闭SDK自动重试，累计3次writer请求后停止，未扩展样本。实际进入evidence_recovery，未达到语义复核阶段，因此“直接LLM生成并通过复核”这一项未通过；页面仍正常完成推荐和有来源校验的介绍，没有“本轮未完成”。该次测试的详细失败载荷未在隔离schema清理前留存，无法可靠断言具体哪条文字校验失败；验收脚本已补充清理前保存诊断，避免以后付费重现丢失的信息。不削弱产品校验以使测试通过。

联调另发现旧对话被删除后，其到期动作仍进入提醒扫描，导致每15秒抛错。扫描现排除已删除对话，并在加锁后再次检查删除状态，避免扫描与删除之间的竞态；真实数据库验证已删除对话不收到消息、正常提醒仅出现一次。

可复现命令（默认不调用模型；需要Docker和正式数据服务已启动）：

```powershell
$env:REFINEMENT_REAL_TEST='1'
node --import tsx scripts/with-project-env.mjs scripts/check_refinement_real.ts
```

仅在明确安排真实模型补测时额外设置`REFINEMENT_REAL_MODEL=1`，脚本全程最多3次请求，SDK重试为0。此轮已用满该预算，不自动继续。

结果见`evaluation/reports/requirement-refinement-real.json`、独立保存的`requirement-refinement-real-model.json`；真实页面截图为`requirement-refinement-real-before.png`和`requirement-refinement-real-after.png`。隔离PostgreSQL生命周期测试及本次26项针对性本地回归通过；构建、原生71文件指纹核验通过。正式8010会话创建、状态读取、删除和删除后410拒绝访问也已实测。
