import { randomUUID } from "node:crypto";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { tool } from "@langchain/core/tools";
import { Annotation, END, MemorySaver, Send, START, StateGraph } from "@langchain/langgraph";
import type { AgentRegistry } from "./registry.js";
import {
  spawnInputSchema, type AgentId, type AgentResult, type Assignment,
  type ExternalRuntimeEvent, type MasterEvent,
} from "../types.js";

type WorkerEvent = Extract<MasterEvent, { type: "agent_result" | "agent_error" }>;
type WakeEvent = WorkerEvent | ExternalRuntimeEvent;
type GraphEvent = { type: "dispatch"; assignments: Assignment[] } |
  Extract<MasterEvent, { type: "master_answer" | "runtime_answer" }>;
type MasterInput =
  | { kind: "user"; query: string; runningAgentStatus: string | null }
  | { kind: "runtime"; event: WakeEvent };
export type SequencedEvent = { id: number; event: MasterEvent };

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
      try {
        const result: AgentResult = { agent, prompt, output: await registry[agent](prompt) };
        config.writer?.({ type: "agent_result", result } satisfies WorkerEvent);
      } catch (error) {
        config.writer?.({
          type: "agent_error", agent, prompt,
          detail: error instanceof Error ? error.message : String(error),
        } satisfies WorkerEvent);
      }
      return {};
    })
    .addEdge(START, "dispatch")
    .addConditionalEdges("dispatch", (state) => state.assignments.map(
      (assignment) => new Send("worker", { assignment }),
    ), ["worker"])
    .addEdge("worker", END)
    .compile();

  return async (assignments: Assignment[], onEvent: (event: WorkerEvent) => void): Promise<void> => {
    for await (const chunk of await graph.stream({ assignments }, { streamMode: "custom" })) {
      const event = chunk as WorkerEvent;
      if (event.type === "agent_result" || event.type === "agent_error") onEvent(event);
    }
  };
}

function spawnSuccess(assignments: Assignment[]): string {
  return JSON.stringify({ status: "spawned", agents: assignments.map(({ agent }) => agent) });
}

export function createSpawnAgentTool(registry: AgentRegistry, onEvent: (event: WorkerEvent) => void = () => {}) {
  const runWorkers = createWorkerRuntime(registry);
  return tool(
    async ({ assignments }) => {
      void runWorkers(assignments, onEvent).catch((error) => console.error("Worker runtime failed", error));
      return spawnSuccess(assignments);
    },
    {
      name: "spawn_agent",
      description: "Dispatch one or more tasks to agents a, b, c, or d, with a separate prompt for each selected agent.",
      schema: spawnInputSchema,
    },
  );
}

function runtimeMessage(event: WakeEvent): HumanMessage {
  if (event.type === "agent_result") {
    return new HumanMessage(`Runtime event: agent ${event.result.agent} completed. Result: ${JSON.stringify(event.result)}`);
  }
  if (event.type === "agent_error") {
    return new HumanMessage(`Runtime event: agent ${event.agent} failed. Details: ${JSON.stringify(event)}`);
  }
  return new HumanMessage(`Runtime event ${event.name}: ${JSON.stringify(event.payload)}`);
}

export function createMaster(model: BaseChatModel, registry: AgentRegistry) {
  if (!model.bindTools) throw new Error("The master model must support tool calling");

  const routingModel = model.bindTools([createSpawnAgentTool(registry)]);
  const runWorkers = createWorkerRuntime(registry);
  const sessionTails = new Map<string, Promise<void>>();
  const activeAgents = new Map<string, Map<AgentId, number>>();
  const eventHistory = new Map<string, SequencedEvent[]>();
  const subscribers = new Map<string, Set<EventQueue<SequencedEvent>>>();

  function publish(sessionId: string, event: MasterEvent): void {
    const history = eventHistory.get(sessionId) ?? [];
    const entry = { id: history.length + 1, event };
    history.push(entry);
    eventHistory.set(sessionId, history);
    for (const subscriber of subscribers.get(sessionId) ?? []) subscriber.push(entry);
  }

  async function* subscribe(sessionId: string, after = 0, signal?: AbortSignal): AsyncGenerator<SequencedEvent> {
    const queue = new EventQueue<SequencedEvent>();
    const abort = () => queue.close();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) queue.close();
    const listeners = subscribers.get(sessionId) ?? new Set<EventQueue<SequencedEvent>>();
    listeners.add(queue);
    subscribers.set(sessionId, listeners);
    for (const entry of eventHistory.get(sessionId) ?? []) {
      if (entry.id > after) queue.push(entry);
    }
    try {
      for await (const entry of queue) yield entry;
    } finally {
      signal?.removeEventListener("abort", abort);
      listeners.delete(queue);
      if (!listeners.size) subscribers.delete(sessionId);
      queue.close();
    }
  }

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
    input: Annotation<MasterInput>(),
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
      if (state.input.kind === "runtime") {
        const response = await model.invoke([...state.messages, runtimeMessage(state.input.event)]);
        if (!(response instanceof AIMessage)) throw new Error("master returned an invalid runtime answer");
        return { assignments: null, routeCall: null, answerMessage: response };
      }
      const response = await routingModel.invoke([
        ...state.messages,
        currentQueryMessage(state.input.query, state.input.runningAgentStatus),
      ]);
      if (!(response instanceof AIMessage)) throw new Error("master returned an invalid response");
      if (!response.tool_calls?.length) {
        return { assignments: null, routeCall: null, answerMessage: response };
      }
      if (response.tool_calls.length !== 1 || response.tool_calls[0].name !== "spawn_agent") {
        throw new Error("master may call spawn_agent only once per query");
      }
      const { assignments } = spawnInputSchema.parse(response.tool_calls[0].args);
      config.writer?.({ type: "dispatch", assignments } satisfies GraphEvent);
      return { assignments, routeCall: response, answerMessage: null };
    })
    .addNode("answer", async (state, config) => {
      if (state.input.kind === "runtime") {
        const response = state.answerMessage;
        if (!(response instanceof AIMessage)) throw new Error("runtime answer is missing");
        config.writer?.({ type: "runtime_answer", answer: response.text } satisfies GraphEvent);
        return { messages: [runtimeMessage(state.input.event), response], answerMessage: response };
      }

      const { query, runningAgentStatus } = state.input;
      let recordedCall: AIMessage | undefined;
      let success: ToolMessage | undefined;
      if (state.assignments && state.routeCall) {
        const call = state.routeCall.tool_calls?.[0];
        if (!call) throw new Error("spawn_agent call is missing");
        const callId = call.id ?? randomUUID();
        recordedCall = call.id ? state.routeCall : new AIMessage({
          content: state.routeCall.content, tool_calls: [{ ...call, id: callId }],
        });
        success = new ToolMessage({
          name: "spawn_agent", tool_call_id: callId, content: spawnSuccess(state.assignments),
        });
      }
      const response = recordedCall && success
        ? await model.invoke([
          ...state.messages, currentQueryMessage(query, runningAgentStatus), recordedCall, success,
        ])
        : state.answerMessage;
      if (!(response instanceof AIMessage)) throw new Error("master answer is missing");
      config.writer?.({ type: "master_answer", answer: response.text } satisfies GraphEvent);
      return {
        messages: [new HumanMessage(query), ...(recordedCall && success ? [recordedCall, success] : []), response],
        answerMessage: response,
      };
    })
    .addEdge(START, "master")
    .addEdge("master", "answer")
    .addEdge("answer", END)
    .compile({ checkpointer: new MemorySaver() });

  async function receiveRuntimeEvent(sessionId: string, event: WakeEvent): Promise<void> {
    await withSessionLock(sessionId, async () => {
      publish(sessionId, event);
      try {
        for await (const chunk of await masterGraph.stream(
          { input: { kind: "runtime", event } },
          { configurable: { thread_id: sessionId }, streamMode: "custom" },
        )) {
          const graphEvent = chunk as GraphEvent;
          if (graphEvent.type === "runtime_answer") publish(sessionId, graphEvent);
        }
      } catch (error) {
        console.error("Runtime event handling failed", error);
        publish(sessionId, { type: "error", detail: "Runtime event handling failed" });
      }
    });
  }

  async function* stream(query: string, sessionId: string): AsyncGenerator<MasterEvent> {
    const events = new EventQueue<MasterEvent>();
    events.push({ type: "session", session_id: sessionId });

    void (async () => {
      try {
        await withSessionLock(sessionId, async () => {
          let answer: string | undefined;
          let spawnedAgents: AgentId[] = [];
          for await (const chunk of await masterGraph.stream(
            { input: { kind: "user", query, runningAgentStatus: runningAgentStatus(sessionId) } },
            { configurable: { thread_id: sessionId }, streamMode: "custom" },
          )) {
            const event = chunk as GraphEvent;
            if (event.type === "dispatch") {
              spawnedAgents = event.assignments.map(({ agent }) => agent);
              const pending = new Set(spawnedAgents);
              for (const agent of spawnedAgents) addActiveAgent(sessionId, agent);
              void runWorkers(event.assignments, (workerEvent) => {
                const agent = workerEvent.type === "agent_result" ? workerEvent.result.agent : workerEvent.agent;
                if (pending.delete(agent)) removeActiveAgent(sessionId, agent);
                void receiveRuntimeEvent(sessionId, workerEvent);
              }).catch((error) => {
                console.error("Worker runtime failed", error);
                for (const assignment of event.assignments) {
                  if (!pending.delete(assignment.agent)) continue;
                  removeActiveAgent(sessionId, assignment.agent);
                  void receiveRuntimeEvent(sessionId, {
                    type: "agent_error", agent: assignment.agent, prompt: assignment.prompt,
                    detail: "Worker runtime failed",
                  });
                }
              });
            } else if (event.type === "master_answer") {
              answer = event.answer;
              events.push(event);
            }
          }
          if (answer === undefined) throw new Error("master did not answer");
          events.push({ type: "done", session_id: sessionId, answer, spawned_agents: spawnedAgents });
        });
      } catch (error) {
        console.error("Master run failed", error);
        events.push({ type: "error", detail: "Master run failed" });
      } finally {
        events.close();
      }
    })();

    for await (const event of events) yield event;
  }

  return { stream, subscribe, receiveRuntimeEvent };
}
