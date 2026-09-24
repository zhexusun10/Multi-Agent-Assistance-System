import { randomUUID } from "node:crypto";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { tool } from "@langchain/core/tools";
import { Annotation, END, MemorySaver, START, StateGraph } from "@langchain/langgraph";
import type { BaseCheckpointSaver } from "@langchain/langgraph-checkpoint";
import { MemoryAgentTaskStore, type AgentTask, type AgentTaskStore } from "../agent-task-store.js";
import { MemorySessionEventStore, type SequencedEvent, type SessionEventStore } from "../session-store.js";
import type { AgentRegistry } from "./registry.js";
import {
  spawnInputSchema, type AgentId, type AgentResult, type Assignment,
  type ExternalRuntimeEvent, type MasterEvent,
} from "../types.js";

type WorkerEvent = Extract<MasterEvent, { type: "agent_result" | "agent_error" }>;
type WakeEvent = WorkerEvent | ExternalRuntimeEvent;
type GraphEvent = { type: "dispatch"; assignments: Assignment[] } | { type: "runtime_consumed" } |
  Extract<MasterEvent, { type: "master_answer" | "runtime_answer" }>;
type MasterInput =
  | { kind: "user"; query: string; runningAgentStatus: string | null;
      runtimeEvents: Array<{ event: WakeEvent; eventKey?: string }> }
  | { kind: "runtime"; event: WakeEvent; eventKey?: string };
type SessionJob = { run: () => Promise<void> };
type SessionQueue = {
  users: SessionJob[];
  runtime: SessionJob[];
  running: boolean;
  activeRuntime?: { controller: AbortController; item: PendingRuntime };
};
type PendingRuntime = {
  event: WakeEvent;
  eventKey?: string;
  persisted: Promise<void>;
  status: "pending" | "steered" | "completed";
  answerReady: boolean;
  resolve: () => void;
  reject: (error: unknown) => void;
  retry: () => void;
};
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

function spawnSuccess(assignments: Assignment[]): string {
  return JSON.stringify({ status: "spawned", agents: assignments.map(({ agent }) => agent) });
}

export function createSpawnAgentTool() {
  return tool(
    async ({ assignments }) => spawnSuccess(assignments),
    {
      name: "spawn_agent",
      description: "Assign tasks to the session's existing agents a, b, c, or d. Each agent retains its own context across tasks.",
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

export function createMaster(
  model: BaseChatModel,
  registry: AgentRegistry,
  persistence: { checkpointer: BaseCheckpointSaver; events: SessionEventStore; tasks: AgentTaskStore } = {
    checkpointer: new MemorySaver(), events: new MemorySessionEventStore(), tasks: new MemoryAgentTaskStore(),
  },
) {
  if (!model.bindTools) throw new Error("The master model must support tool calling");

  const routingModel = model.bindTools([createSpawnAgentTool()]);
  const sessionQueues = new Map<string, SessionQueue>();
  const pendingRuntime = new Map<string, Set<PendingRuntime>>();
  const activePairs = new Map<string, Promise<void>>();
  const subscribers = new Map<string, Set<EventQueue<SequencedEvent>>>();

  async function publish(sessionId: string, event: MasterEvent, key?: string): Promise<void> {
    const entry = await persistence.events.append(sessionId, event, key);
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
    try {
      // Register before reading so events committed during replay also reach this subscriber.
      for (const entry of await persistence.events.list(sessionId, after)) {
        if (signal?.aborted) return;
        if (entry.id > after) {
          after = entry.id;
          yield entry;
        }
      }
      for await (const entry of queue) {
        if (entry.id > after) {
          after = entry.id;
          yield entry;
        }
      }
    } finally {
      signal?.removeEventListener("abort", abort);
      listeners.delete(queue);
      if (!listeners.size) subscribers.delete(sessionId);
      queue.close();
    }
  }

  async function runningAgentStatus(sessionId: string): Promise<string | null> {
    const agents = await persistence.tasks.activeAgents(sessionId);
    if (!agents.length) return null;
    const names = (["a", "b", "c", "d"] as AgentId[])
      .filter((agent) => agents.includes(agent))
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

  function isSteered(item: PendingRuntime): boolean {
    return item.status === "steered";
  }

  function removePending(sessionId: string, item: PendingRuntime): void {
    const pending = pendingRuntime.get(sessionId);
    pending?.delete(item);
    if (!pending?.size) pendingRuntime.delete(sessionId);
  }

  function sessionQueue(sessionId: string): SessionQueue {
    let queue = sessionQueues.get(sessionId);
    if (!queue) {
      queue = { users: [], runtime: [], running: false };
      sessionQueues.set(sessionId, queue);
    }
    return queue;
  }

  function enqueueSession(sessionId: string, kind: "user" | "runtime", work: () => Promise<void>): Promise<void> {
    const queue = sessionQueue(sessionId);
    const finished = new Promise<void>((resolve, reject) => {
      queue[kind === "user" ? "users" : "runtime"].push({
        run: async () => {
          try { await work(); resolve(); }
          catch (error) { reject(error); }
        },
      });
    });
    if (!queue.running) void drainSession(sessionId, queue);
    return finished;
  }

  async function drainSession(sessionId: string, queue: SessionQueue): Promise<void> {
    queue.running = true;
    try {
      let job: SessionJob | undefined;
      while ((job = queue.users.shift() ?? queue.runtime.shift())) await job.run();
    } finally {
      queue.running = false;
      if (!queue.users.length && !queue.runtime.length) sessionQueues.delete(sessionId);
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
    alreadyProcessed: Annotation<boolean>(),
    alreadySteered: Annotation<boolean>(),
    processedRuntime: Annotation<Record<string, string>>({
      reducer: (left, right) => ({ ...left, ...right }),
      default: () => ({}),
    }),
    steeredRuntime: Annotation<Record<string, boolean>>({
      reducer: (left, right) => ({ ...left, ...right }),
      default: () => ({}),
    }),
  });

  const masterGraph = new StateGraph(MasterState)
    .addNode("master", async (state, config) => {
      if (state.input.kind === "runtime") {
        if (state.input.eventKey && state.steeredRuntime[state.input.eventKey]) {
          return { assignments: null, routeCall: null, answerMessage: null, alreadySteered: true };
        }
        const previous = state.input.eventKey && state.processedRuntime[state.input.eventKey];
        if (previous !== undefined) {
          return { assignments: null, routeCall: null, answerMessage: new AIMessage(previous),
            alreadyProcessed: true, alreadySteered: false };
        }
        const response = await model.invoke([...state.messages, runtimeMessage(state.input.event)], config);
        if (!(response instanceof AIMessage)) throw new Error("master returned an invalid runtime answer");
        return { assignments: null, routeCall: null, answerMessage: response,
          alreadyProcessed: false, alreadySteered: false };
      }
      const response = await routingModel.invoke([
        ...state.messages,
        ...state.input.runtimeEvents.map(({ event }) => runtimeMessage(event)),
        currentQueryMessage(state.input.query, state.input.runningAgentStatus),
      ], config);
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
        if (state.alreadySteered) {
          config.writer?.({ type: "runtime_consumed" } satisfies GraphEvent);
          return {};
        }
        const response = state.answerMessage;
        if (!(response instanceof AIMessage)) throw new Error("runtime answer is missing");
        config.writer?.({ type: "runtime_answer", answer: response.text } satisfies GraphEvent);
        if (state.alreadyProcessed) return {};
        return {
          messages: [runtimeMessage(state.input.event), response], answerMessage: response,
          processedRuntime: state.input.eventKey ? { [state.input.eventKey]: response.text } : {},
        };
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
          ...state.messages, ...state.input.runtimeEvents.map(({ event }) => runtimeMessage(event)),
          currentQueryMessage(query, runningAgentStatus), recordedCall, success,
        ], config)
        : state.answerMessage;
      if (!(response instanceof AIMessage)) throw new Error("master answer is missing");
      config.writer?.({ type: "master_answer", answer: response.text } satisfies GraphEvent);
      return {
        messages: [
          ...state.input.runtimeEvents.map(({ event }) => runtimeMessage(event)), new HumanMessage(query),
          ...(recordedCall && success ? [recordedCall, success] : []), response,
        ],
        answerMessage: response,
        steeredRuntime: Object.fromEntries(state.input.runtimeEvents
          .filter(({ eventKey }) => eventKey !== undefined)
          .map(({ eventKey }) => [eventKey!, true])),
      };
    })
    .addEdge(START, "master")
    .addEdge("master", "answer")
    .addEdge("answer", END)
    .compile({ checkpointer: persistence.checkpointer });

  function scheduleRuntimeEvent(sessionId: string, event: WakeEvent, eventKey?: string): {
    persisted: Promise<void>; finished: Promise<void>;
  } {
    const persisted = publish(sessionId, event, eventKey ? `event:${eventKey}` : undefined);
    let resolveFinished!: () => void;
    let rejectFinished!: (error: unknown) => void;
    const finished = new Promise<void>((resolve, reject) => {
      resolveFinished = resolve;
      rejectFinished = reject;
    });
    const item: PendingRuntime = {
      event, eventKey, persisted, status: "pending", answerReady: false, resolve: resolveFinished,
      reject: rejectFinished, retry: () => {},
    };
    const pending = pendingRuntime.get(sessionId) ?? new Set<PendingRuntime>();
    pending.add(item);
    pendingRuntime.set(sessionId, pending);
    const process = async () => {
      if (item.status !== "pending") return;
      const controller = new AbortController();
      sessionQueue(sessionId).activeRuntime = { controller, item };
      try {
        await persisted;
        let answer: string | undefined;
        let consumed = false;
        for await (const chunk of await masterGraph.stream(
          { input: { kind: "runtime", event, eventKey } },
          { configurable: { thread_id: sessionId }, streamMode: "custom", durability: "sync", signal: controller.signal },
        )) {
          const graphEvent = chunk as GraphEvent;
          if (graphEvent.type === "runtime_answer") {
            answer = graphEvent.answer;
            item.answerReady = true;
          }
          if (graphEvent.type === "runtime_consumed") consumed = true;
        }
        if (isSteered(item)) return;
        if (consumed) {
          item.status = "completed";
          removePending(sessionId, item);
          resolveFinished();
          return;
        }
        if (answer === undefined) throw new Error("Runtime answer is missing");
        await publish(sessionId, { type: "runtime_answer", answer }, eventKey ? `answer:${eventKey}` : undefined);
        item.status = "completed";
        removePending(sessionId, item);
        resolveFinished();
      } catch (error) {
        if (isSteered(item)) return;
        item.status = "completed";
        removePending(sessionId, item);
        rejectFinished(error);
        console.error("Runtime event handling failed", error);
        await publish(sessionId, { type: "error", detail: "Runtime event handling failed" });
      } finally {
        const queue = sessionQueue(sessionId);
        if (queue.activeRuntime?.controller === controller) queue.activeRuntime = undefined;
      }
    };
    item.retry = () => {
      void enqueueSession(sessionId, "runtime", process).catch((error) => {
        console.error("Runtime event scheduling failed", error);
      });
    };
    item.retry();
    return { persisted, finished };
  }

  async function receiveRuntimeEvent(sessionId: string, event: WakeEvent, eventKey?: string): Promise<void> {
    const { persisted, finished } = scheduleRuntimeEvent(sessionId, event, eventKey);
    void persisted.catch(() => {});
    await finished;
  }

  async function acceptRuntimeEvent(sessionId: string, event: ExternalRuntimeEvent): Promise<void> {
    const { persisted, finished } = scheduleRuntimeEvent(sessionId, event);
    void finished.catch((error) => console.error("Runtime event handling failed", error));
    await persisted;
  }

  function taskEvent(task: AgentTask): WorkerEvent {
    if (task.status === "failed") {
      return { type: "agent_error", agent: task.agent, prompt: task.prompt, detail: task.error ?? "Agent failed" };
    }
    const result: AgentResult = { agent: task.agent, prompt: task.prompt, output: task.output ?? "" };
    return { type: "agent_result", result };
  }

  function kickAgent(sessionId: string, agent: AgentId): void {
    const pairKey = JSON.stringify([sessionId, agent]);
    if (activePairs.has(pairKey)) return;
    const running = (async () => {
      try {
        while (true) {
          const finished = await persistence.tasks.nextUndelivered(sessionId, agent);
          if (finished) {
            await receiveRuntimeEvent(sessionId, taskEvent(finished), finished.id);
            await persistence.tasks.markDelivered(finished.id);
            continue;
          }
          const task = await persistence.tasks.claimNext(sessionId, agent);
          if (!task) break;
          let output: string;
          try {
            output = await registry[agent](task.prompt, sessionId, task.id);
          } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            await persistence.tasks.finish(task.id, null, detail);
            continue;
          }
          await persistence.tasks.finish(task.id, output, null);
        }
      } catch (error) {
        console.error("Agent task processing failed", error);
        try {
          await persistence.tasks.requeueRunning(sessionId, agent);
        } catch (requeueError) {
          console.error("Could not requeue agent task", requeueError);
        }
      } finally {
        activePairs.delete(pairKey);
        try {
          if (await persistence.tasks.hasWork(sessionId, agent)) {
            setTimeout(() => kickAgent(sessionId, agent), 1000);
          }
        } catch (error) {
          console.error("Could not inspect agent task queue", error);
        }
      }
    })();
    activePairs.set(pairKey, running);
  }

  async function resumePendingTasks(): Promise<void> {
    await persistence.tasks.requeueRunning();
    for (const { sessionId, agent } of await persistence.tasks.pendingPairs()) {
      kickAgent(sessionId, agent);
    }
  }

  async function waitForTaskIdle(): Promise<void> {
    await Promise.all(activePairs.values());
  }

  async function* stream(query: string, sessionId: string): AsyncGenerator<MasterEvent> {
    const events = new EventQueue<MasterEvent>();
    events.push({ type: "session", session_id: sessionId });

    const steered = [...(pendingRuntime.get(sessionId) ?? [])]
      .filter((item) => item.status === "pending" && !item.answerReady);
    for (const item of steered) item.status = "steered";
    const active = sessionQueue(sessionId).activeRuntime;
    if (active && steered.includes(active.item)) active.controller.abort();

    void (async () => {
      try {
        await enqueueSession(sessionId, "user", async () => {
          await Promise.all(steered.map((item) => item.persisted));
          let answer: string | undefined;
          let spawnedAgents: AgentId[] = [];
          for await (const chunk of await masterGraph.stream(
            { input: {
              kind: "user", query, runningAgentStatus: await runningAgentStatus(sessionId),
              runtimeEvents: steered.map(({ event, eventKey }) => ({ event, eventKey })),
            } },
            { configurable: { thread_id: sessionId }, streamMode: "custom", durability: "sync" },
          )) {
            const event = chunk as GraphEvent;
            if (event.type === "dispatch") {
              spawnedAgents = event.assignments.map(({ agent }) => agent);
              await persistence.tasks.enqueue(sessionId, event.assignments);
              for (const agent of spawnedAgents) kickAgent(sessionId, agent);
            } else if (event.type === "master_answer") {
              answer = event.answer;
              events.push(event);
            }
          }
          if (answer === undefined) throw new Error("master did not answer");
          events.push({ type: "done", session_id: sessionId, answer, spawned_agents: spawnedAgents });
          for (const item of steered) {
            item.status = "completed";
            removePending(sessionId, item);
            item.resolve();
          }
        });
      } catch (error) {
        for (const item of steered) {
          item.status = "pending";
          item.retry();
        }
        console.error("Master run failed", error);
        events.push({ type: "error", detail: "Master run failed" });
      } finally {
        events.close();
      }
    })();

    for await (const event of events) yield event;
  }

  return { stream, subscribe, receiveRuntimeEvent, acceptRuntimeEvent, resumePendingTasks, waitForTaskIdle };
}
