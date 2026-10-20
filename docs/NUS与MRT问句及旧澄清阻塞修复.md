# NUS与MRT问句、旧澄清阻塞修复

复核日期：2026-10-20。

## 问题及复现

用户原话：“我想租新加坡国立大学附近3公里内的房子，要求房子3公里内附近有MRT，预算3000新币每月，要求有独立浴室”。旧解析把第二个分句中的主语“房子”当作地点，送去地点目录，得到“地点目录暂未找到‘房子’”的阻塞澄清。用户下一轮删去MRT分句后，`pendingQuestions()`继续保留旧问题，因此简化请求依然无法进入推荐。这不是数据库没有房源，也不是Recommendation Agent未接入导致的。

修复前结果保存在 `evaluation/reports/location-subject-before.json`。

## 实现变化

- `src/domain/geo-intake.ts` 优先解析“房子…附近有/距离…目标”的关系，区分房源主语和地理目标；通用房屋称谓不会送去地点解析。`MRT`可表示站点类别，`Clementi MRT`仍是具名站点。类别意图由代码生成，模型不能编造地点ID。
- `src/domain/workflow.ts` 将类别意图转为Contract V1已有的 `place_proximity`，`target.category=MRTStation`，半径换算为米；未指定半径只作更近优先，不增加硬范围。默认直线距离，步行要求仍保留为无法核实的路线条件，不冒充实际路线。
- `src/adapters/locations.ts` 从当前官方LTA目录提供所有MRT站点出口，排除LRT；`src/adapters/recommendation.ts` 对该类别求房源到所有已收录MRT出口的最近直线距离，保留目录版本、哈希及证据。没有任意挑选单个站名，也没有假借平台标注字段证明真实距离。
- `src/domain/requirements.ts` 修复旧pending中的房源主语误识别：移除已证明无效的“房子”澄清，并从原始分句恢复漏掉的MRT约束。预算、NUS、独立卫浴等原条件保留。真正不存在或多义的地点澄清仍保留，不能因修改预算就消失。
- 旧会话在下一次找房请求中自动修复，无须新建会话；历史对话原文不被改写。未提及的既有条件继续保留，明确取消才删除。
- 前端距离软偏好显示“就近排序”箭头，不能因为距离大于评分尺度就显示红色“未满足”；该变化不修改后端事实、排名或硬条件状态。
- 对话和结果区明确显示“已找到待核实候选”及展示数量，避免把“缺少独卫等条件证据”表述成没有房源；仍不能宣称全部条件通过。

## 真实业务验收

四个真实DeepSeek/HTTP/数据库场景全部通过：完整原句、简化原句的新会话、复现旧pending后再次提交简化原句、没有独卫要求的对照查询。

当前样本共评估475条租赁记录；完整原句及简化原句均返回176条待核实候选并展示前5条，299条被排除。NUS与MRT条件按独立SQL坐标及球面距离公式核对；MRT对照使用官方目录541个MRT出口。当前这个范围内两个查询的候选数相同，是数据计算结果，不代表MRT条件未执行；展示房源的MRT距离值均逐项对照通过。

独立卫浴没有可核验房源证据，保留为unknown，未为了展示推荐而放宽或改为通过。去掉该诉求的对照查询返回176条已验证候选并展示前5条。

报告：`evaluation/reports/location-subject-live.json`。

## 回归与网页

- 新增5个本地测试，含20种房源主语/MRT口语表达、具名站点、平台字段、旧会话恢复及真实未知地点保留。
- 原120条租房话术回归通过；原金文泰地区、具名MRT、公交补充、独立距离核对、快速修改和请求重放集成测试通过。
- 真实Chrome检查五张房源卡片、NUS/MRT各自条件、同会话再提简化需求、刷新与390px手机宽度，通过且无JavaScript错误；没有发送外部消息。
- 网页报告：`evaluation/reports/location-subject-ui.json`；截图：`location-subject-desktop.png`及`location-subject-mobile.png`。

最后一轮重启验收还发现，本机受管终端中后台启动的graph进程退出，留下running的推荐任务。数据API和数据库健康，尚未出现推荐结果不是地点解析再次失败。当前用独立托管前台进程运行图服务，入口为`scripts/run-graph.mjs`；它登记自己的PID和启动时间，继续兼容stop-app的进程身份校验。重启后持久任务恢复，并再次执行真实业务及网页验收；测试结束另行检查服务健康。普通PowerShell使用start-app的方式仍保留。

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System'
node --import tsx --test tests/location-subject.test.ts
$env:LOCATION_SUBJECT_LIVE='1'
node --env-file=.env --import tsx --test tests/location-subject-live.test.ts
.\.venv\Scripts\python.exe scripts/test_location_subject_ui.py
```
