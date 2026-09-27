# 持久化任务、事件与子 Agent 审查

## 已实现的能力与边界

- `src/agents/{a,b,c,d}.ts:5-6` 均仅包装 `createWorker`；`src/agents/registry.ts:10-16` 统一暴露四个 runner，尚无各自职责、工具或系统提示词（`README.md:3-5,168-171`）。worker 以 JSON 编码的 `(worker, sessionId, agent)` 为 checkpoint thread，累积自身消息，借助 `lastTaskId` 避免**紧接着重投的同一任务**重复写入上下文（`src/agents/worker.ts:9-17,25-44`）。
- PostgreSQL 任务表支持事务式批量入队、按 session/agent 领取队首任务（`FOR UPDATE SKIP LOCKED`）、记录成功/失败、标记已投递、重启时重排 `running` 并扫描待处理任务（`src/agent-task-store.ts:107-211`；`src/agents/master.ts:404-453`）。进程内 `activePairs` 让同一 session 的同一 agent 串行执行；不同 pair 可并行。master、worker 分别使用持久化 checkpoint；事件表按 session 保存带唯一 `(session_id,event_id)` 的事件，计数器事务性分配递增 ID，`event_key` 提供任务事件/回答的去重（`src/session-store.ts:34-100`）。
- `tests/postgres-session.test.ts:14-73,75-139` 覆盖已完成运行后的历史/游标重放、不同 agent/会话上下文隔离、`running` 任务重排及任务结果重投去重；**未覆盖**崩溃在外部事件已提交但尚未回答、并发发布顺序、回答失败和分配后 query 失败。测试需要 `TEST_DATABASE_URL`（`:15,:76`），本次未连接数据库；本地也没有 `node_modules`，尝试运行无 DB 的 TS 探针时因找不到 `tsx` 退出，以下是代码路径推演而非实测结果。

## 可复现缺陷（按严重性）

1. **高｜外部 Runtime 事件提交后重启可能永不被处理。** `src/agents/master.ts:315-331,375-393,449-453`；`src/server.ts:21-22,85-92`；`src/session-store.ts:58-86`。触发：调用 `/runtime/events`，`acceptRuntimeEvent` 在原始事件成功写表后返回 202，此时让 runtime 模型调用保持未完成并重启图服务。`pendingRuntime` 和 session queue 仅在内存；启动只恢复 `agent_tasks`，不会从 `session_events` 扫描未消费的外部事件。结果：原始事件可以 SSE 重放，但不会自动产生 `runtime_answer` 或进入 master checkpoint；只有客户端再次主动发事件才有机会触发。与子 Agent 结果可借未投递 task 恢复不同。证据：恢复入口仅 `requeueRunning`/`pendingPairs`，而外部事件没有 task、没有持久化的消费状态。**确认度：高（静态路径确定，未做断电实测）。**

2. **高｜任务入队与 query 成功并非同一原子操作，失败后重试可重复派单。** `src/agents/master.ts:254-259,291-313,476-507`；`src/agent-task-store.ts:129-149`。触发：路由模型稳定调用一次 `spawn_agent(a,p)`，`dispatch` 导致任务已提交并启动 worker，随后回答节点的 `model.invoke` 抛错，或入队之后进程崩溃。当前 query 返回 `error`（或断连），但已入队任务仍执行且无撤销；客户端用同一 session 重发相同 query，产生新的随机任务 ID 与第二次执行。任务表中没有 query/调用 ID 唯一键，checkpoint 与队列写入没有跨库事务；即使任务已经完成，重发也不会由 `lastTaskId` 去重。**确认度：高（模型第二次调用抛错即可用内存存储复现；本地依赖缺失未运行）。**

3. **高｜实时订阅可能跳过较小的事件 ID，断线续传也补不回来。** `src/agents/master.ts:111-145`；`src/session-store.ts:62-85,95-100`。触发：订阅完成初始 `list` 后，同一 session 两个并发 `publish` 分别提交 ID 1、2，但 ID 1 的 `append` 返回/通知慢于 ID 2（可用实现 `SessionEventStore` 的延迟包装器确定性重现；PostgreSQL 并发连接也不能保证提交后 JS 回调按事件 ID 唤醒）。订阅先收到 2 并设置 `after=2`，随后 1 在 `if (entry.id > after)` 被丢弃；客户端用 `after=2` 重连也永远拿不到 1。数据库分配顺序本身是事务性的，问题在提交与逐个 `subscriber.push` 之间没有按 ID 串行化/补读。**确认度：高（接口和逻辑可确定性复现；真实 DB 时序概率未实测）。**

4. **中｜Runtime 回答持续失败会锁死同一 agent 的后续排队任务并重复写错误事件。** `src/agents/master.ts:363-369,404-439`；`src/agent-task-store.ts:175-181,206-211`。触发：A 的已完成任务唤醒 master 时 `model.invoke` 一直抛错；再给同 session 的 A 排第二个任务。`process` 拒绝 `finished`、无条件追加未去重的 `error`；`kickAgent` 在 `markDelivered` 前中断，定时器因未投递任务仍存在每秒重新 kick，`nextUndelivered` 永远先取得同一失败投递，后面的 `claimNext` 无法执行。原始 task 事件用 key 去重，错误事件却每次新增 ID；master 不可用时增长无界，其他 session/agent 则不受该 pair 阻塞。**确认度：高（静态控制流确定；无 DB/模型动态验证）。**

## 核查说明

`src/agent-task-store.ts:184-192` 的领取语句可防并发领取同一行；`src/session-store.ts:61-90` 的 ID 计数器在同一事务更新，去重时回滚，未发现通常情况下的数据库重复 ID。`src/agents/worker.ts:25-44` 的同一任务结果去重在测试所覆盖的“最后任务重投”场景成立，不能据此推断上述跨事件/跨 query 的端到端恰好一次语义。项目明确要求单图服务实例（`README.md:174`）；以上未将多实例抢占当作缺陷。
