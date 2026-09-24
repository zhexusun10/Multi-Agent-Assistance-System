# Multi-Agent Assistance System

基于 TypeScript、LangGraph 和 FastAPI 的多 Agent 后端骨架。用户请求先进入 `master`；它可以直接回答，也可以通过 `spawn_agent` 工具向当前 session 固定的 `a`、`b`、`c`、`d` Agent 分配任务。工具名保留 `spawn_agent`，但每次调用都复用该 session 已有的逻辑 Agent，不创建新的 Agent 实例。任务先写入 PostgreSQL，master 随后回答并结束当前请求。子 Agent 完成时，Runtime 事件会再次唤醒 master；结果和后续回答通过独立的会话事件流发送给客户端。模型由 LangChain 的 `initChatModel` 按 `provider:model` 标识加载对应 provider SDK。

当前版本只定义运行链路、数据契约和代码边界。**没有编写 master 或子 Agent 的系统提示词，也没有分配 A–D 的业务职责。**

## 架构

```mermaid
flowchart LR
    U[客户端] --> F[FastAPI /api/query]
    F --> S[TypeScript 图服务 /run]
    S --> M[master 选择任务]
    S <--> P[(PostgreSQL 会话历史、Agent 上下文、任务与事件)]
    M --> R[Spawn 成功后回答并结束请求]
    M --> T[持久化任务队列]
    T --> A[session 的 agent a]
    T --> B[session 的 agent b]
    T --> C[session 的 agent c]
    T --> D[session 的 agent d]
    A --> J[Runtime 事件唤醒 master]
    B --> J
    C --> J
    D --> J
    R --> F
    J --> E[会话事件流 /api/events]
    E --> U
    F --> U
```

- **FastAPI** 是对外 HTTP 入口，负责请求校验和转发请求与会话的 SSE 事件流。
- **TypeScript 图服务** 通过本机 HTTP 接收请求，运行 LangGraph，并逐步发送事件。
- **master 图** 包含任务选择与回答节点，并通过 PostgreSQL 版 LangGraph checkpointer 保存会话历史。`spawn_agent` 是可选工具；调用时可选择 1–4 个不同 Agent。为兼容现有接口，工具返回值仍是 `{"status":"spawned","agents":[...]}`，实际含义是任务已提交给当前 session 的 Agent。后续 Runtime 事件再次启动图的一次执行。
- **PostgreSQL** 保存 master 与每个子 Agent 的 checkpoint、待处理任务和会话事件。图服务启动时自动创建所需表；按 `session_id` 隔离会话，并为每个会话保存递增事件 ID。
- **子 Agent 运行图** 每个 session 的 A、B、C、D 分别使用固定的 checkpoint 线程。再次分配给同一 Agent 时，它会读取自己的历史消息；不同 Agent 与不同 session 的上下文互相隔离。同一 Agent 的任务依次执行，不同 Agent 可并发执行。

### 运行语义

`master` 的图实例在 TypeScript 服务启动时创建，并在服务运行期间持续接收用户请求和 Runtime 事件。每次请求都先由 `master` 决定是否分配任务；分配记录写入 PostgreSQL 后，当前 session 的相应 Agent 开始处理。通信方向是 master 分配任务给子 Agent、子 Agent 完成后发送 Runtime 事件；子 Agent 之间没有通信通道。分配成功后，master 再调用一次模型回答用户，随后 `/api/query` 的 SSE 发送 `done` 并关闭。此时子 Agent 可以继续运行，但 master 不等待、不轮询，也不再消耗模型推理资源。每个子 Agent 完成或失败时，Runtime 事件会自动触发 master 的一次新执行，产生 `runtime_answer`。外部系统也可通过 `/api/runtime/events` 发送事件。查询完成得很快时，子 Agent 的事件也会在 `done` 之后处理。

`master` 使用 PostgreSQL 版 LangGraph checkpointer 按 `session_id` 保存对话历史。同一会话的用户请求和 Runtime 事件依次处理；上一轮的子 Agent 仍在运行时，新 query 已可进入 master。Runtime 事件处理后，事件内容和 master 的回答都会写入历史，供之后的调用使用。如果新 query 先于事件处理，则当时看不到该结果。不同会话互相隔离并可并发处理。子 Agent 只读取自己在该 session 的历史及新分配的任务文本，不读取 master 或其他 Agent 的历史。

图服务启动时会把上次运行中断的子 Agent 任务放回队列，并继续处理未完成任务和未投递结果。若停机发生在模型调用中，该任务会从本次 Agent 节点重新执行；已写入 checkpoint 的任务结果按任务 ID 去重，不会重复加入 Agent 上下文。

新 query 进入 master 时，运行时会读取该会话仍在工作的子 Agent，并在本次模型输入的最末尾追加状态。例如 A 和 D 尚未完成时，末行是 `Sub agents A and D are still running`。完成的 Agent 会从状态中移除；全部完成时不追加状态行。这行状态只用于本次模型调用，保存到会话历史中的仍是用户原始 query。

这采用 LangChain 文档中的 [单一分发工具与子 Agent 隔离模式](https://docs.langchain.com/oss/javascript/langchain/multi-agent/subagents#single-dispatch-tool)，并使用 LangGraph 的 [持久化 checkpoint](https://docs.langchain.com/oss/javascript/langgraph/add-memory) 与 [Graph API](https://docs.langchain.com/oss/javascript/langgraph/quickstart#use-the-graph-api)。

## 目录

```text
api/main.py              FastAPI 对外 API
src/server.ts            TypeScript 图服务 HTTP 入口
src/model.ts             模型创建与环境变量
src/types.ts             请求、分配任务和结果契约
src/agent-task-store.ts  PostgreSQL 子 Agent 任务队列
src/session-store.ts     PostgreSQL 会话事件记录
src/agents/master.ts     master 图与 spawn_agent 工具
src/agents/registry.ts   Agent 注册表
src/agents/worker.ts     子 Agent 共用的最小 LangGraph 工作流
src/agents/a.ts          Agent A 扩展入口
src/agents/b.ts          Agent B 扩展入口
src/agents/c.ts          Agent C 扩展入口
src/agents/d.ts          Agent D 扩展入口
tests/                  图流程与 API 契约测试
```

## 本地运行

需要 Node.js 20+、Python 3.11+，以及支持工具调用的聊天模型。

1. 安装依赖：

   ```bash
   npm ci
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. 配置模型。可参考 `.env.example`；按所选 provider 设置模型标识和密钥：

   ```bash
   export CHAT_MODEL="openai:gpt-4.1-mini"
   export OPENAI_API_KEY="你的密钥"
   ```

   已安装 OpenAI、Anthropic 和 Google GenAI 的 provider SDK。例如可改为 `CHAT_MODEL="anthropic:<模型名>"` 并设置 `ANTHROPIC_API_KEY`，或改为 `CHAT_MODEL="google-genai:<模型名>"` 并设置 `GOOGLE_API_KEY`。`OPENAI_BASE_URL` 仅在 `CHAT_MODEL` 使用 `openai:` 前缀时生效，可连接遵循 OpenAI 接口的服务。更多 provider 可按 [LangChain 模型文档](https://docs.langchain.com/oss/javascript/concepts/providers-and-models#one-api-for-any-model) 安装对应集成包，再设置 `CHAT_MODEL`。

3. 启动图服务：

   ```bash
   # 先启动 PostgreSQL，并创建 multi_agent_assistance 数据库。
   export DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/multi_agent_assistance"
   npm run dev:graph
   ```

   `DATABASE_URL` 必填，数据库账号需要建表权限。图服务会自动初始化 LangGraph checkpoint、任务和会话事件表；数据库不可用时启动失败，`/health` 也会报告不可用。

4. 在另一个终端启动 FastAPI：

   ```bash
   source .venv/bin/activate
   uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
   ```

5. 发送请求：

   ```bash
   curl -N -X POST http://127.0.0.1:8000/api/query \
     -H 'Content-Type: application/json' \
     -d '{"query":"你的问题"}'
   ```

返回的是 `text/event-stream`。例如选择 Agent A 时，当前请求的事件依次为：

```text
event: session
data: {"type":"session","session_id":"会话 ID"}

event: master_answer
data: {"type":"master_answer","answer":"master 的回答"}

event: done
data: {"type":"done","session_id":"会话 ID","answer":"master 的回答","spawned_agents":["a"]}
```

客户端拿到 `session_id` 后，可另行订阅后续事件：

```bash
curl -N "http://127.0.0.1:8000/api/events?session_id=会话ID"
```

子 Agent 完成后，此流依次收到带递增 `id` 的 `agent_result` 和 `runtime_answer`；失败时收到 `agent_error` 和后续回答。重连时传入 `after=上次收到的事件ID`，或使用 SSE 的 `Last-Event-ID` 请求头，可从 PostgreSQL 重放遗漏的事件，包括图服务重启前的事件。没有事件时，订阅连接异步等待，不触发模型调用。

外部 Runtime 事件可通过以下接口唤醒 master：

```bash
curl -X POST http://127.0.0.1:8000/api/runtime/events \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"会话ID","name":"build_finished","payload":{"ok":true}}'
```

事件写入 PostgreSQL 后接口返回 `202`，无需等待 master 推理；事件及后续的 `runtime_answer` 出现在 `/api/events`。如果 `master` 直接回答且没有调用 `spawn_agent`，当前请求仍只有 `session`、`master_answer` 和 `done`；运行出错时发送 `error` 事件。

继续同一会话时，在下一次请求中带回 `session_id`：

```bash
curl -N -X POST http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"继续刚才的问题","session_id":"上一次返回的会话 ID"}'
```

首次请求不带 `session_id` 时由图服务生成 UUID；也可以由客户端传入自己的非空会话 ID（最多 128 个字符）。

`GET /health` 会检查图服务是否可用。FastAPI 默认连接 `http://127.0.0.1:3001`；可用 `GRAPH_SERVICE_URL` 修改。图服务监听地址和端口分别由 `GRAPH_HOST`、`GRAPH_PORT` 控制，默认仅监听本机。

## 后续职责划分

- 在 `src/agents/a.ts` 至 `d.ts` 中分别实现各 Agent 的职责、工具和工作流；`registry.ts` 保持统一的 `(prompt, sessionId, taskId) => Promise<string>` 调用契约。
- 在确定职责后，再补充对应的系统提示词和工具描述。当前只有 `spawn_agent` 的参数说明，供模型按 `a`–`d` 选择目标并提供各自的任务文本。
- 更换已安装的模型提供商时只需修改 `CHAT_MODEL` 和对应凭据；模型创建逻辑集中在 `src/model.ts`。

master 与子 Agent 的历史、待办任务和会话事件均存放在 PostgreSQL，可在图服务重启后继续处理。进程停机时正在执行的模型调用会重新执行；未来若给子 Agent 添加有外部副作用的工具，应按任务 ID 实现幂等。当前同一 `session_id` 的请求在单个图服务进程内串行处理，部署时应只运行一个图服务实例。

## 验证

```bash
npm run typecheck
npm test
npm run build
python3 -m unittest discover -s tests -p 'test_*.py'
# 有测试数据库时，再执行 PostgreSQL 持久化集成测试：
TEST_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/multi_agent_assistance_test" npm test
```

图流程测试使用模拟模型，不需要 API 密钥。真实模型的端到端调用需要先配置模型凭据。
