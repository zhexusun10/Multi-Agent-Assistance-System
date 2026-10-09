# 独立测试审阅

## 摘要

仅验证了无真实模型调用、无真实 PostgreSQL 的路径；TypeScript 类型检查和构建、Python API 模拟测试通过，`npm test` 15 项中 13 通过、2 项 PostgreSQL 集成测试因未设置 `TEST_DATABASE_URL` 跳过（模型工厂测试已自设临时假密钥，无需真实 API 密钥）。不能据此认定 README 中的持久化、服务重启和真实 SSE 端到端行为已验证。未读取或加载 `.env`，未修改源码。

## 命令结果

环境：Node v25.4.0、npm 11.7.0、Python 3.11.2；起初缺少 `node_modules`，执行 `npm ci --ignore-scripts --no-audit --no-fund` 成功（58 packages）。Python 的 FastAPI/httpx/Pydantic 依赖已可用。

| 命令 | 结果 |
| --- | --- |
| `npm run typecheck` | 退出码 0，`tsc --noEmit` 通过。 |
| `npm test` | 退出码 0：15 项，13 通过、2 跳过；`tests/model.test.ts` 自设临时假密钥，只构造模型对象、不调用线上模型。 |
| `npm run build` | 退出码 0，`tsc` 通过。 |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 退出码 0，7 项通过。 |

两项 DB 集成测试由 `TEST_DATABASE_URL` 是否存在决定，当前没有提供，因此跳过；未连接或验证外部数据库（`tests/postgres-session.test.ts:14-16,75-77`）。README 也要求加载本地配置后才运行数据库测试（`README.md:176-186`）；不应将跳过解释为通过。

## README 声称能力与覆盖边界

- README 声称 master 分派、完成后事件唤醒、同 session 调度抢占及运行中 Agent 状态（`README.md:33-45`）。`tests/graph.test.ts:23-348` 使用假模型、内存 checkpoint/任务/事件存储，覆盖直接回答、工具参数校验、单 Agent 分派及异步完成、游标、任务串行、失败事件、抢占与状态；**未验证**真实 provider 的推理或四 Agent 并发、跨 session 并行的负载行为。README 已明确系统提示词与 A–D 业务职责尚未实现（`README.md:5`），不能把这些当成已测试能力。
- README 声称 PostgreSQL 历史隔离、重启续处理与事件重放（`README.md:34-35,43,144,174`）。`tests/postgres-session.test.ts:14-140` 有两项真实 DB 测试，但本次均跳过；即使运行也主要验证 checkpoint/游标和任务恢复，不启动真实 HTTP 服务或模拟真实进程崩溃。`src/server.ts:24-28,43-73,103-108` 的路由与断连/错误路径没有 TS HTTP 端到端测试。
- README 声称 FastAPI 的查询/事件流/外部 Runtime 入口及 SSE 重连（`README.md:138-166`）。`tests/test_api.py:55-103` 的 7 项通过 `httpx.MockTransport` 验证转发与基本校验；未覆盖 `Last-Event-ID` 请求头、上游 4xx/5xx/断连、SSE 长连接及真实图服务与数据库重放（`api/main.py:42-108`）。
- README 声称可切换 OpenAI/Anthropic/Google 的 provider SDK（`README.md:68-89`）。`tests/model.test.ts:5-16` 只检查工厂返回对象有 `invoke`/`bindTools`，没有调用模型，也没有验证 `OPENAI_BASE_URL` 配置生效（`src/model.ts:5-11`）。

## 可证实的问题

1. **外部 Runtime 事件在重启中途有丢失唤醒风险（代码路径可证，未做 DB 崩溃实测）**：接口在事件落库后即返回 202，不等回答（`README.md:146-154`；`src/agents/master.ts:390-394`）；运行中待处理状态只保存在 `pendingRuntime` 内存映射（`src/agents/master.ts:107,315-335`）。启动恢复仅执行 `resumePendingTasks()`，该函数只扫描 Agent 任务，并不扫描已落库但未得到 `runtime_answer` 的外部事件（`src/server.ts:24`；`src/agents/master.ts:449-454`）。因此若落库/返回 202 后、推理完成前进程停止，原始事件能重放，但不会自动再次唤醒 master 生成回答；现有两项 DB 测试也未覆盖这一情形（`tests/postgres-session.test.ts:14-140`）。
