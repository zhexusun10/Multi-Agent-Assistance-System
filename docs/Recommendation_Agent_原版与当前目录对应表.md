# Recommendation Agent 原版与当前项目目录对应表

江山原来的 Recommendation Agent 已完整迁入当前项目的 `recommendation/` 目录，共 71 个原版文件。

- 原版对照提交：`907cfa839d2665b3702450eb40acd4c96fe9296c`。
- 当前已推送版本：`89131897f56bbc176c30246a451f118f7de0209f`。
- 项目仓库：https://github.com/zhexusun10/Multi-Agent-Assistance-System
- 当前本地根目录：`D:\Real Estate Agent\Multi-Agent-Assistance-System`。

## 1. 两个原目录的整体对应关系

| 江山原版目录 | 当前项目目录 |
|---|---|
| `dev/recommendationSubagent/` | `recommendation/` |
| `dev/sharedContract/recommendationSubagentContract/` | `recommendation/contracts/` |

这两个目录下的文件均保持原来的相对层级和文件名。例如：

```text
原版：dev/recommendationSubagent/ranking/ranker.py
现在：recommendation/ranking/ranker.py

原版：dev/sharedContract/recommendationSubagentContract/requirements.py
现在：recommendation/contracts/requirements.py
```

## 2. 原版推荐逻辑的具体文件

下表的原版路径均位于 `dev/recommendationSubagent/` 下；当前路径均位于 `recommendation/` 下，表中的相对路径保持一致。

| 功能 | 两个版本中对应的相对路径 |
|---|---|
| 推荐服务入口、完整执行流程 | `service.py` |
| 配置、参数 | `config.py` |
| Schema 生成与检查 | `schemas.py` |
| Agent 控制器、工具调用循环 | `agent/controller.py` |
| 大模型调用桥接 | 原版 `agent/model_bridge.ts`；当前已删除（声明删除），改为 Python LangChain 客户端 `agent/llm.py` |
| 工具分发与候选池管理 | `tools/dispatcher.py` |
| 核心排序、显式与隐式评分融合 | `ranking/ranker.py` |
| 评分计算函数 | `ranking/metrics.py` |
| 条件判断规则 | `validation/rules.py` |
| 返回前最终校验 | `validation/gate.py` |
| 反馈处理、兴趣档案 | `context/manager.py` |
| 状态、快照与事务持久化 | `state/store.py` |
| 数据库连接 | `repositories/connections.py` |
| PostgreSQL 房源读取 | `repositories/listing_repository.py` |
| Neo4j 地理证据查询 | `repositories/geographic_repository.py` |
| 数据仓储接口 | `repositories/interfaces.py` |
| 候选数据模型 | `models/candidate.py` |
| 房源摘要模型 | `models/listing_summary.py` |
| 评分明细模型 | `models/score_breakdown.py` |
| 工具返回模型 | `models/tool_results.py` |
| 执行追踪 | `tracing/trace.py` |
| 原生命令行入口 | `terminal/__main__.py` |
| 原生验收流程 | `terminal/acceptance.py` |
| 原版测试 | `tests/test_policy_controller.py`、`tests/test_recommendation.py`、`tests/test_terminal_repositories.py` |
| 原版样例、样例脚本 | `examples/` 下全部文件 |
| 依赖及说明 | `requirements.txt`、`README.md` |
| Python 包初始化文件 | 各目录的 `__init__.py` |

当前 GitHub 代码入口：[recommendation 目录](https://github.com/zhexusun10/Multi-Agent-Assistance-System/tree/89131897f56bbc176c30246a451f118f7de0209f/recommendation)。

## 3. 原版输入、输出契约的对应文件

原版前缀为 `dev/sharedContract/recommendationSubagentContract/`，当前前缀为 `recommendation/contracts/`。

| 内容 | 两个版本中对应的相对路径 |
|---|---|
| 请求结构 | `request.py` |
| 需求字段、约束结构 | `requirements.py` |
| 用户反馈结构 | `feedback.py` |
| 条件证据结构 | `evidence.py` |
| 推荐响应结构 | `response.py` |
| 错误结构 | `errors.py` |
| 请求 JSON Schema | `schemas/request.schema.json` |
| 需求 JSON Schema | `schemas/requirements.schema.json` |
| 反馈 JSON Schema | `schemas/feedback.schema.json` |
| 响应 JSON Schema | `schemas/response.schema.json` |
| 说明及包初始化 | `README.md`、`__init__.py` |

当前 GitHub 契约入口：[recommendation/contracts](https://github.com/zhexusun10/Multi-Agent-Assistance-System/tree/89131897f56bbc176c30246a451f118f7de0209f/recommendation/contracts)。

## 4. 当前项目新增的接入文件

下面这些文件是为了把原版接入我们的网页工作流而增加的，江山原版中没有对应文件：

| 当前文件 | 作用 |
|---|---|
| `recommendation/app.py` | HTTP 服务外壳，对外提供原生推荐调用 |
| `src/adapters/native-recommendation.ts` | Requirement 请求转换、调用推荐服务、保存原生响应、转换展示结构 |
| `src/contracts/recommendation-native.ts` | TypeScript 侧的原生响应结构校验 |
| `scripts/run-recommendation.py` | 启动原生推荐服务 |
| `frontend/native-ranking.js` | 前端展示原生评分及反馈功能 |

另有已有文件增加了接入分支：

| 当前文件 | 接入修改 |
|---|---|
| `src/adapters/recommendation.ts` | 增加 `native` 推荐模式 |
| `src/domain/explanation.ts` | 增加原生评分、证据的展示处理 |
| `src/server.ts` | 增加原生反馈入口检查 |
| `frontend/app.js` | 接入原生推荐结果展示 |
| `scripts/start-app.ps1` 等启动脚本 | 管理新增推荐服务进程 |
