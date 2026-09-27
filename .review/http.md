# HTTP/API 层审查

## 已实现接口与契约

`api/main.py` 用 Pydantic 校验 query、session_id、runtime event，使用 httpx 转发到图服务 `/run`、`/events`、`/runtime/events`，并将前两者的字节流作为 SSE 返回；`/health` 检查上游。`src/server.ts` 对 `/run` 发送 `session`、`master_answer`、`done`（失败时 `error`），对 `/events` 按会话重放/订阅带 ID 的持久化事件（`agent_result`、`agent_error`、`runtime_event`、`runtime_answer` 等），`after` 优先于 `Last-Event-ID`；runtime POST 落库后返回 202。健康检查查询 PostgreSQL。正常路径与 README.md 一致；`tests/test_api.py` 只验证了成功转发、基本空白 query/session 校验和健康成功路径。

## 有证据的问题

1. **[高] 并发发布可能使 SSE 游标永久跳过事件。** `src/agents/master.ts:111-114` 各次 `publish` 独立等待数据库 append 后才向订阅者 push，`src/agents/master.ts:133-137` 收到一个较大 ID 就更新 `after` 并丢弃随后到达的较小 ID。触发条件：同一 session 的并发 runtime 发布中，ID 较大的 append 回调/通知先于较小 ID 的通知到达（数据库 ID 顺序不保证异步通知回调顺序）；实时订阅先收到例如 `id: 2` 后收到 `id: 1`。后果：`id: 1` 被丢弃，客户端以 2 重连时数据库查询 `event_id > 2` 也无法补回（`src/session-store.ts:95-99`）。

2. **[高] 请求体、事件重放和慢客户端均无上限/背压。** `src/server.ts:78-82` 把整个 POST 请求堆积为 Buffer，`src/types.ts:19-22,36-40` 不限制 query、name、payload 的大小；`src/session-store.ts:95-100` 一次取回全部历史事件，`src/server.ts:60-62,103-106` 忽略 `response.write()` 返回的 `false`，`src/agents/master.ts:40-51` 的实时队列也无上限。触发条件：巨大请求/长会话从 `after=0` 重放/客户端读取很慢但持续产生事件。后果：Node 进程内存持续增长、连接占用和服务失稳；FastAPI 也需解析客户端的大 JSON。

3. **[中] 空闲 `/api/events` 无法及时建立 SSE 响应。** `src/server.ts:51-60` 仅调用 `writeHead`，未 flush headers，直到有历史/新事件才 `write`；`api/main.py:45-46,66` 又等待 httpx 收到上游响应头才返回 StreamingResponse。触发条件：订阅没有历史事件且暂时没有新事件的会话。后果：客户端在首次事件前收不到 HTTP 200/SSE 响应头；上游超时配置为 `read=None`（`api/main.py:26`），中间代理可能先超时，而不是已建立的空闲事件流。

4. **[中] 查询断流后后台图执行没有取消/背压。** `src/server.ts:103-108` 仅检查 `response.destroyed` 后跳过写入，不向 `master.stream` 传递取消信号；`src/agents/master.ts:470-511` 将图执行作为独立任务启动，图调用也未接收请求的 abort signal。触发条件：客户端在 `/api/query` 模型运行中断开，或停止读取响应。后果：本应只服务于已断开的请求仍可调用模型、占用同 session 队列、派发 Agent 任务并保存历史，结果却无法送达该请求；慢读取时输出缓冲还会累积。

5. **[中] 已发送 SSE 响应头后的服务端异常被当作正常结束。** `src/server.ts:109-115` 在 `headersSent` 时仅 `response.end()`，不发送 `error` 事件。触发条件：`/run` 流的迭代器或写入在发送响应头后抛错（与 `master.stream` 自行捕获并发出 `error` 的路径不同）。后果：客户端收到 200 后静默 EOF，没有契约中的 `done` 或 `error`，难以判定是否成功并安全重试；`api/main.py:58-66` 也只是原样转发字节，不补全终止状态。

6. **[中] 上游协议错误与客户端输入错误被错误归类。** `api/main.py:102-106` 把 runtime POST 的所有上游 HTTP 非 2xx（包括上游 422/500）都转成 503；`api/main.py:47-56` 把 events 的上游 422 转为 502。触发条件：例如 `/api/events?session_id=%20` 通过 FastAPI 的非空长度校验却在 `src/server.ts:44-49` 被 trim 后判 422，或 `Last-Event-ID: abc` 在图服务被判 422；runtime 上游返回 500 时也返回 503。后果：可纠正的输入错误被报告为网关/不可用，实际后端错误也无法与不可用区分，客户端可能错误重试。
