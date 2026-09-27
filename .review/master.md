# master 图与并发语义调查

范围：静态阅读 `README.md`、`src/agents/master.ts`、`src/types.ts`、`tests/graph.test.ts`，辅查任务/事件存储与服务入口；未调用模型、数据库或外部服务（本地未安装 `node_modules`）。

**已实现**：`src/agents/master.ts:229-313,460-499` 以 `session_id` 为 checkpoint 线程：query 先用绑定 `spawn_agent` 的模型路由；无工具时直接回答，有且仅有一次合法调用时校验 1–4 个不重复的 a–d 分配，发出 dispatch、持久化任务并启动 worker，再将工具成功消息交给模型作最终回答，SSE 发 `master_answer`、`done`。`src/types.ts:4-16` 定义参数约束。worker 结束/失败由 `src/agents/master.ts:404-427` 投递原始事件并唤醒图产生 `runtime_answer`；外部事件持久化后即可返回。`src/agents/master.ts:183-205,315-393,460-509` 单进程内同 session 用户队列优先于 Runtime 队列；新 query 可中止正在推理的 Runtime、吸收尚未产生回答的待处理事件，带 key 的 worker 结果在 checkpoint 中记录已回答/已吸收状态；`src/agents/master.ts:116-145` 订阅先注册再回放，按递增 ID 过滤重叠。运行中 Agent 状态来自任务表 queued/running 快照，仅拼接到本轮模型输入、不写进历史（`src/agents/master.ts:147-162,277-307,478-479`）。不同 session 的内存队列独立，固定 Agent 配对由 `activePairs` 防止同进程重复执行。`tests/graph.test.ts` 覆盖直答、分配后回答、快 worker、会话状态、顺序、抢占及重放的正常路径。

## 已确认的逻辑缺陷（代码路径可直接推出）

1. **外部事件重启后丢失唤醒**：`src/agents/master.ts:318-331,390-394,449-453`，`src/server.ts:19-22,83-88`。外部事件先入事件日志、再仅存于进程内 `pendingRuntime`/队列；接口在持久化后即返回 202。如果进程在 Runtime 图提交前停止，启动只恢复任务表中的 worker 任务，不扫描外部事件日志或恢复待运行事件；事件可在 `/events` 回放，但不会生成 `runtime_answer`，master 历史也未必消费该事件。没有 key 的外部事件亦无 checkpoint 去重/恢复标记。
2. **worker 后续任务被 master 回答阻塞**：`src/agents/master.ts:409-417,384-387`。处理一个已完成任务时 `kickAgent` 等待 `receiveRuntimeEvent` 的 `finished`（包括模型推理和事件发布）才 `markDelivered` 并 `claimNext`。触发：该 Runtime 回答慢、挂起或反复失败，同时同 session 同 Agent 有下一项已排队任务；后续 worker 即使空闲也不能开始。故任务执行进度耦合于 master 推理，不只是 Agent 自身串行。
3. **Runtime 失败形成无限重试/错误事件风暴**：`src/agents/master.ts:363-380,410-413,428-440`。Runtime 模型持续报错或事件发布持续失败时，`finished` 被拒绝，worker 已完成任务仍未 `markDelivered`；外层捕获后 `hasWork` 为真，每秒再次 kick，同一任务再次唤醒；可不断调用模型、产生无 key 的 `error` 事件。无最大次数、退避或持久的失败终态。
4. **dispatch 副作用与整轮 query 无事务边界**：`src/agents/master.ts:258-259,291-308,484-494`。在路由节点发出 dispatch 后便 `enqueue` 并启动 worker，随后才进行第二次模型调用、完成图及发送 `done`。触发：分配成功但回答模型/图的后续步骤失败；客户端只得到 query `error`，却已有任务运行甚至结果事件。若客户端重试 query，新分配生成新任务 ID，任务存储不按 query 去重；可能重复执行。该项不否认任务先持久化是预期行为，问题是失败时缺少一致性/幂等补偿。

## 需验证/推测

- **取消依赖底层模型遵守 AbortSignal**：`src/agents/master.ts:464-472,332-350,197-205`。抢占仅调用 `controller.abort()`，队列仍 `await job.run()`；若 LangGraph/模型调用对取消不作终止响应，用户 job 虽已排队却无法开始，同 session 全部请求卡住。是否发生取决于 LangGraph 和模型 provider 的取消实现；现有 `tests/graph.test.ts` 的永不 resolve 模型抢占测试验证其模拟环境，不能保证所有 provider。
- **流式事件与 checkpoint 的提交时点**：`src/agents/master.ts:269-274,298-308,345-359,488-494`。自定义事件在节点返回/整轮图完成前写出；若 checkpointer 写入或流结束失败，可能先观察到 `master_answer`/`runtime_answer`，再遇到失败/重试；实际发射与持久化顺序需针对所用 LangGraph 版本故障注入验证，不当作已确认的重复提交。

测试缺口：现有 `tests/graph.test.ts` 只检查正常抢占和内存去重，未模拟外部事件持久化后重启、回答长期失败、任务已入库后 query 失败、订阅/检查点提交失败及取消不响应；`tests/postgres-session.test.ts` 仅验证已完成的事件跨实例回放与 worker 任务恢复。
