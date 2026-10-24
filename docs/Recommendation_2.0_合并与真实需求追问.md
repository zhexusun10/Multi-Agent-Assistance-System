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

本机Docker引擎当前未运行，尝试启动时Windows拒绝启动com.docker.service，因此隔离PostgreSQL全链路验收尚未完成；浏览器回放使用真实页面与拦截业务接口，不能冒称完整线上验收。待引擎恢复后运行隔离数据库测试，应用启动会自动执行迁移007。本次没有调用付费模型测试。
