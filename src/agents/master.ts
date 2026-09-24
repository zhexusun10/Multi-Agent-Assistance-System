import { randomUUID } from "node:crypto";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { tool } from "@langchain/core/tools";
import { Annotation, END, MemorySaver, Send, START, StateGraph } from "@langchain/langgraph";
import type { AgentRegistry } from "./registry.js";
import { spawnInputSchema, type AgentId, type AgentResult, type Assignment, type MasterEvent } from "../types.js";

type DispatchEvent = { type: "dispatch"; assignments: Assignment[]; routeCall: AIMessage };
type RuntimeEvent = DispatchEvent | Extract<MasterEvent, { type: "master_answer" | "agent_result" }>;

class EventQueue<T> implements AsyncIterable<T> {
  private items: T[] = [];
  private waiter?: (item: IteratorResult<T>) => void;
  private closed = false;

  push(item: T): void {
    if (this.closed) return;
    if (this.waiter) {
      const waiter = this.waiter;
      this.waiter = undefined;
      waiter({ value: item, done: false });
    } else this.items.push(item);
  }

  close(): void {
    this.closed = true;
    this.waiter?.({ value: undefined, done: true });
    this.waiter = undefined;
  }

  async *[Symbol.asyncIterator](): AsyncGenerator<T> {
    while (this.items.length) yield this.items.shift()!;
    while (!this.closed) {
      const next = await new Promise<IteratorResult<T>>((resolve) => { this.waiter = resolve; });
      if (next.done) return;
      yield next.value;
      while (this.items.length) yield this.items.shift()!;
    }
  }
}

function createWorkerRuntime(registry: AgentRegistry) {
  const WorkerState = Annotation.Root({
    assignments: Annotation<Assignment[]>(),
    assignment: Annotation<Assignment>(),
  });

  const graph = new StateGraph(WorkerState)
    .addNode("dispatch", () => ({}))
    .addNode("worker", async (state, config) => {
      const { agent, prompt } = state.assignment;
      const result: AgentResult = { agent, prompt, output: await registry[agent](prompt) };
      config.writer?.({ type: "agent_result", result } satisfies MasterEvent);
      return {};
    })
    .addEdge(START, "dispatch")
    .addConditionalEdges("dispatch", (state) => state.assignments.map(
      (assignment) => new Send("worker", { assignment }),
    ), ["worker"])
    .addEdge("worker", END)
    .compile();

  return async (assignments: Assignment[], onResult: (result: AgentResult) => void = () => {}) => {
    const results: AgentResult[] = [];
    for await (const chunk of await graph.stream({ assignments }, { streamMode: "custom" })) {
      const event = chunk as RuntimeEvent;
      if (event.type === "agent_result") {
        results.push(event.result);
        onResult(event.result);
      }
    }
    return results;
  };
}

export function createSpawnAgentTool(registry: AgentRegistry) {
  const runWorkers = createWorkerRuntime(registry);
  return tool(
    async ({ assignments }) => JSON.stringify(await runWorkers(assignments)),
    {
      name: "spawn_agent",
      description: "Dispatch one or more tasks to agents a, b, c, or d, with a separate prompt for each selected agent.",
      schema: spawnInputSchema,
    },
  );
}

export function createMaster(model: BaseChatModel, registry: AgentRegistry) {
  if (!model.bindTools) throw new Error("The master model must support tool calling");

  const routingModel = model.bindTools([createSpawnAgentTool(registry)]);
  const runWorkers = createWorkerRuntime(registry);
  const sessionTails = new Map<string, Promise<void>>();
  const activeAgents = new Map<string, Map<AgentId, number>>();

  function addActiveAgent(sessionId: string, agent: AgentId): void {
    const agents = activeAgents.get(sessionId) ?? new Map<AgentId, number>();
    agents.set(agent, (agents.get(agent) ?? 0) + 1);
    activeAgents.set(sessionId, agents);
  }

  function removeActiveAgent(sessionId: string, agent: AgentId): void {
    const agents = activeAgents.get(sessionId);
    if (!agents) return;
    const count = agents.get(agent) ?? 0;
    if (count <= 1) agents.delete(agent);
    else agents.set(agent, count - 1);
    if (agents.size === 0) activeAgents.delete(sessionId);
  }

  function runningAgentStatus(sessionId: string): string | null {
    const agents = activeAgents.get(sessionId);
    if (!agents?.size) return null;
    const names = (["a", "b", "c", "d"] as AgentId[])
      .filter((agent) => agents.has(agent))
      .map((agent) => agent.toUpperCase());
    if (names.length === 1) return `Sub agent ${names[0]} is still running`;
    const namesText = names.length === 2
      ? names.join(" and ")
      : `${names.slice(0, -1).join(", ")}, and ${names.at(-1)}`;
    return `Sub agents ${namesText} are still running`;
  }

  function currentQueryMessage(query: string, status: string | null): HumanMessage {
    return new HumanMessage(status ? `${query}\n\n${status}` : query);
  }

  async function withSessionLock<T>(sessionId: string, work: () => Promise<T>): Promise<T> {
    const previous = sessionTails.get(sessionId) ?? Promise.resolve();
    let release!: () => void;
    const current = new Promise<void>((resolve) => { release = resolve; });
    const tail = previous.then(() => current);
    sessionTails.set(sessionId, tail);
    await previous;
    try {
      return await work();
    } finally {
      release();
      if (sessionTails.get(sessionId) === tail) sessionTails.delete(sessionId);
    }
  }

  const MasterState = Annotation.Root({
    query: Annotation<string>(),
    runningAgentStatus: Annotation<string | null>(),
    messages: Annotation<Array<HumanMessage | AIMessage | ToolMessage>>({
      reducer: (left, right) => left.concat(right),
      default: () => [],
    }),
    assignments: Annotation<Assignment[] | null>(),
    routeCall: Annotation<AIMessage | null>(),
    answerMessage: Annotation<AIMessage | null>(),
  });

  const masterGraph = new StateGraph(MasterState)
    .addNode("master", async (state, config) => {
      const response = await routingModel.invoke([
        ...state.messages,
        currentQueryMessage(state.query, state.runningAgentStatus),
      ]);
      if (!(response instanceof AIMessage)) throw new Error("master returned an invalid response");
      if (!response.tool_calls?.length) {
        return { assignments: null, routeCall: null, answerMessage: response };
      }
      if (response.tool_calls.length !== 1 || response.tool_calls[0].name !== "spawn_agent") {
        throw new Error("master may call spawn_agent only once per query");
      }
      const { assignments } = spawnInputSchema.parse(response.tool_calls[0].args);
      config.writer?.({ type: "dispatch", assignments, routeCall: response } satisfies DispatchEvent);
      return { assignments, routeCall: response, answerMessage: null };
    })
    .addNode("answer", async (state, config) => {
      const response = state.assignments
        ? await model.invoke([
          ...state.messages,
          currentQueryMessage(state.query, state.runningAgentStatus),
        ])
        : state.answerMessage;
      if (!(response instanceof AIMessage)) throw new Error("master answer is missing");
      config.writer?.({ type: "master_answer", answer: response.text } satisfies MasterEvent);
      return { messages: [new HumanMessage(state.query), response], answerMessage: response };
    })
    .addEdge(START, "master")
    .addEdge("master", "answer")
    .addEdge("answer", END)
    .compile({ checkpointer: new MemorySaver() });

  async function receiveResults(sessionId: string, routeCall: AIMessage, agents: AgentResult[]) {
    const call = routeCall.tool_calls?.[0];
    if (!call) throw new Error("spawn_agent call is missing");
    const callId = call.id ?? randomUUID();
    const recordedCall = call.id
      ? routeCall
      : new AIMessage({ content: routeCall.content, tool_calls: [{ ...call, id: callId }] });
    await withSessionLock(sessionId, async () => {
      await masterGraph.updateState(
        { configurable: { thread_id: sessionId } },
        { messages: [recordedCall, new ToolMessage({
          name: "spawn_agent",
          tool_call_id: callId,
          content: JSON.stringify(agents),
        })] },
        "answer",
      );
    });
  }

  async function* stream(query: string, sessionId: string): AsyncGenerator<MasterEvent> {
    const events = new EventQueue<MasterEvent>();
    events.push({ type: "session", session_id: sessionId });

    void (async () => {
      let answer: string | undefined;
      let masterComplete = false;
      let routeCall: AIMessage | undefined;
      let workerTask: Promise<{ agents: AgentResult[] } | { error: unknown }> | undefined;
      const buffered: AgentResult[] = [];

      const receiveAgent = (result: AgentResult) => {
        if (!masterComplete) buffered.push(result);
        else events.push({ type: "agent_result", result });
      };

      try {
        await withSessionLock(sessionId, async () => {
          for await (const chunk of await masterGraph.stream(
            { query, runningAgentStatus: runningAgentStatus(sessionId) },
            { configurable: { thread_id: sessionId }, streamMode: "custom" },
          )) {
            const event = chunk as RuntimeEvent;
            if (event.type === "dispatch") {
              routeCall = event.routeCall;
              const pending = new Set(event.assignments.map(({ agent }) => agent));
              for (const agent of pending) addActiveAgent(sessionId, agent);
              workerTask = runWorkers(event.assignments, (result) => {
                if (pending.delete(result.agent)) removeActiveAgent(sessionId, result.agent);
                receiveAgent(result);
              })
                .finally(() => {
                  for (const agent of pending) removeActiveAgent(sessionId, agent);
                })
                .then((agents) => ({ agents }), (error) => ({ error }));
            } else if (event.type === "master_answer") {
              answer = event.answer;
              events.push(event);
            }
          }
        });

        if (answer === undefined) throw new Error("master did not answer");
        masterComplete = true;
        for (const result of buffered) events.push({ type: "agent_result", result });
        buffered.length = 0;
        let agents: AgentResult[] = [];
        if (workerTask && routeCall) {
          const result = await workerTask;
          if ("error" in result) throw result.error;
          agents = result.agents;
          await receiveResults(sessionId, routeCall, agents);
        }
        events.push({ type: "done", session_id: sessionId, answer, agents });
      } catch (error) {
        console.error("Master run failed", error);
        events.push({ type: "error", detail: "Master run failed" });
      } finally {
        events.close();
      }
    })();

    for await (const event of events) yield event;
  }

  return { stream };
}
