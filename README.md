# Multi-Agent Assistance System

基于 TypeScript、LangGraph 和 FastAPI 的多 Agent 后端骨架。用户请求先进入 `master`；它可以直接回答，也可以通过 `spawn_agent` 工具选择 `a`、`b`、`c`、`d` 中的一个或多个 Agent，并为每个 Agent 传入独立任务文本。`master` 的回答和子 Agent 的运行并行进行；子 Agent 完成后把结果返回 `master`，并通过同一条事件流继续发送给客户端。模型由 LangChain 的 `initChatModel` 按 `provider:model` 标识加载对应 provider SDK。

当前版本只定义运行链路、数据契约和代码边界。**没有编写 master 或子 Agent 的系统提示词，也没有分配 A–D 的业务职责。**

## 架构

```mermaid
flowchart LR
    U[客户端] --> F[FastAPI /api/query]
    F --> S[TypeScript 图服务 /run]
    S --> M[master 选择任务]
    M --> R[master 回答并释放会话]
    M --> T[LangGraph Send 分发]
    T --> A[agent a]
    T --> B[agent b]
    T --> C[agent c]
    T --> D[agent d]
    A --> J[结果写回 master 会话]
    B --> J
    C --> J
    D --> J
    R --> F
    J --> F
    F --> U
```

- **FastAPI** 是对外 HTTP 入口，负责请求校验和转发 SSE 事件流。
- **TypeScript 图服务** 通过本机 HTTP 接收请求，运行 LangGraph，并逐步发送事件。
- **master 图** 包含任务选择与回答节点，并通过 LangGraph checkpointer 保存会话历史。`spawn_agent` 是可选工具；调用时可选择 1–4 个不同 Agent。master 回答后即可处理同一会话的新请求。
- **子 Agent 运行图** 使用 LangGraph `Send` 为每个选中的 Agent 创建独立并行任务，由图运行时调度。每个 Agent 各有独立文件和 LangGraph 工作流，只接收分配给自己的任务文本，不共享其他 Agent 的上下文。

### 运行语义

`master` 的图实例在 TypeScript 服务启动时创建，并在服务运行期间持续接收用户请求。每次请求都先由 `master` 决定是否分配任务；选中的子 Agent 通过 `Send` 并发启动，各自只接收一份任务文本。通信方向是 master 分配任务给子 Agent、子 Agent 完成后返回结果；子 Agent 之间没有通信通道，也不会主动向 master 发指令。master 可以先完成并发送自己的回答，子 Agent 不会阻塞它。每个子 Agent 完成后把结果交回 master；若结果先到，会等 master 回答发出后再进入同一条事件流。最后的 `done` 事件包含本次回答和所有子 Agent 结果。

`master` 使用 LangGraph checkpointer 按 `session_id` 保存对话历史。同一会话的 master 回答阶段依次处理；上一轮的子 Agent 仍在运行时，新 query 已可进入 master。已完成的子 Agent 结果安全写回会话，供之后的 master 调用使用；如果新 query 先于结果到达，master 当时还看不到该结果。不同会话互相隔离并可并发处理。子 Agent 始终只获得本次分配的任务文本，不读取 master 的历史。

新 query 进入 master 时，运行时会读取该会话仍在工作的子 Agent，并在本次模型输入的最末尾追加状态。例如 A 和 D 尚未完成时，末行是 `Sub agents A and D are still running`。完成的 Agent 会从状态中移除；全部完成时不追加状态行。这行状态只用于本次模型调用，保存到会话历史中的仍是用户原始 query。

这采用 LangChain 文档中的 [单一分发工具与子 Agent 隔离模式](https://docs.langchain.com/oss/javascript/langchain/multi-agent/subagents#single-dispatch-tool)，并使用 LangGraph 的 [Send 并行分发](https://docs.langchain.com/oss/javascript/langgraph/workflows-agents) 与 [Graph API](https://docs.langchain.com/oss/javascript/langgraph/quickstart#use-the-graph-api)。

## 目录

```text
api/main.py              FastAPI 对外 API
src/server.ts            TypeScript 图服务 HTTP 入口
src/model.ts             模型创建与环境变量
src/types.ts             请求、分配任务和结果契约
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
   npm run dev:graph
   ```

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

返回的是 `text/event-stream`。例如选择 Agent A 时，事件依次为：

```text
event: session
data: {"type":"session","session_id":"会话 ID"}

event: master_answer
data: {"type":"master_answer","answer":"master 的回答"}

event: agent_result
data: {"type":"agent_result","result":{"agent":"a","prompt":"分配的任务","output":"Agent A 的结果"}}

event: done
data: {"type":"done","session_id":"会话 ID","answer":"master 的回答","agents":[{"agent":"a","prompt":"分配的任务","output":"Agent A 的结果"}]}
```

如果 `master` 直接回答且没有调用 `spawn_agent`，事件流只有 `session`、`master_answer` 和 `done`；运行出错时发送 `error` 事件。

继续同一会话时，在下一次请求中带回 `session_id`：

```bash
curl -N -X POST http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"继续刚才的问题","session_id":"上一次返回的会话 ID"}'
```

首次请求不带 `session_id` 时由图服务生成 UUID；也可以由客户端传入自己的非空会话 ID（最多 128 个字符）。

`GET /health` 会检查图服务是否可用。FastAPI 默认连接 `http://127.0.0.1:3001`；可用 `GRAPH_SERVICE_URL` 修改。图服务监听地址和端口分别由 `GRAPH_HOST`、`GRAPH_PORT` 控制，默认仅监听本机。

## 后续职责划分

- 在 `src/agents/a.ts` 至 `d.ts` 中分别实现各 Agent 的职责、工具和工作流；`registry.ts` 保持统一的 `(prompt) => Promise<string>` 调用契约。
- 在确定职责后，再补充对应的系统提示词和工具描述。当前只有 `spawn_agent` 的参数说明，供模型按 `a`–`d` 选择目标并提供各自的任务文本。
- 更换已安装的模型提供商时只需修改 `CHAT_MODEL` 和对应凭据；模型创建逻辑集中在 `src/model.ts`。

当前 checkpointer 使用内存存储：会话历史在图服务进程运行期间有效，重启后清空。需要跨重启保留历史时，可在图服务层替换为数据库 checkpointer；API 的 `session_id` 契约不需改变。

## 验证

```bash
npm run typecheck
npm test
npm run build
python3 -m unittest discover -s tests -p 'test_*.py'
```

图流程测试使用模拟模型，不需要 API 密钥。真实模型的端到端调用需要先配置模型凭据。
