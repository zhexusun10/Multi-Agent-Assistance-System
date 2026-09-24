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
type GraphEvent = { type: "dispatch"; assignments: Assignment[] } |
  Extract<MasterEvent, { type: "master_answer" | "runtime_answer" }>;
type MasterInput =
  | { kind: "user"; query: string; runningAgentStatus: string | null }
  | { kind: "runtime"; event: WakeEvent; eventKey?: string };
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
  const sessionTails = new Map<string, Promise<void>>();
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
    alreadyProcessed: Annotation<boolean>(),
    processedRuntime: Annotation<Record<string, string>>({
      reducer: (left, right) => ({ ...left, ...right }),
      default: () => ({}),
    }),
  });

  const masterGraph = new StateGraph(MasterState)
    .addNode("master", async (state, config) => {
      if (state.input.kind === "runtime") {
        const previous = state.input.eventKey && state.processedRuntime[state.input.eventKey];
        if (previous !== undefined) {
          return { assignments: null, routeCall: null, answerMessage: new AIMessage(previous), alreadyProcessed: true };
        }
        const response = await model.invoke([...state.messages, runtimeMessage(state.input.event)]);
        if (!(response instanceof AIMessage)) throw new Error("master returned an invalid runtime answer");
        return { assignments: null, routeCall: null, answerMessage: response, alreadyProcessed: false };
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
    .compile({ checkpointer: persistence.checkpointer });

  function scheduleRuntimeEvent(sessionId: string, event: WakeEvent, eventKey?: string): {
    persisted: Promise<void>; finished: Promise<void>;
  } {
    let resolvePersisted!: () => void;
    let rejectPersisted!: (error: unknown) => void;
    const persisted = new Promise<void>((resolve, reject) => {
      resolvePersisted = resolve;
      rejectPersisted = reject;
    });
    const finished = withSessionLock(sessionId, async () => {
      try {
        await publish(sessionId, event, eventKey ? `event:${eventKey}` : undefined);
        resolvePersisted();
        let answer: string | undefined;
        for await (const chunk of await masterGraph.stream(
          { input: { kind: "runtime", event, eventKey } },
          { configurable: { thread_id: sessionId }, streamMode: "custom", durability: "sync" },
        )) {
          const graphEvent = chunk as GraphEvent;
          if (graphEvent.type === "runtime_answer") answer = graphEvent.answer;
        }
        if (answer === undefined) throw new Error("Runtime answer is missing");
        await publish(sessionId, { type: "runtime_answer", answer }, eventKey ? `answer:${eventKey}` : undefined);
      } catch (error) {
        rejectPersisted(error);
        console.error("Runtime event handling failed", error);
        await publish(sessionId, { type: "error", detail: "Runtime event handling failed" });
      }
    });
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

    void (async () => {
      try {
        await withSessionLock(sessionId, async () => {
          let answer: string | undefined;
          let spawnedAgents: AgentId[] = [];
          for await (const chunk of await masterGraph.stream(
            { input: { kind: "user", query, runningAgentStatus: await runningAgentStatus(sessionId) } },
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

  return { stream, subscribe, receiveRuntimeEvent, acceptRuntimeEvent, resumePendingTasks, waitForTaskIdle };
}
