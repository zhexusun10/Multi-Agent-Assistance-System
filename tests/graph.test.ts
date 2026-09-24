import assert from "node:assert/strict";
import test from "node:test";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { createMaster, createSpawnAgentTool } from "../src/agents/master.js";
import type { AgentRegistry } from "../src/agents/registry.js";
import { spawnInputSchema, type MasterEvent } from "../src/types.js";

function unusedWorkers(): AgentRegistry {
  const unexpected = async () => { throw new Error("worker should not run"); };
  return { a: unexpected, b: unexpected, c: unexpected, d: unexpected };
}

async function collect(stream: AsyncGenerator<MasterEvent>): Promise<MasterEvent[]> {
  const events: MasterEvent[] = [];
  for await (const event of stream) events.push(event);
  return events;
}

test("spawn_agent returns success before workers finish and keeps inputs isolated", async () => {
  const started: string[] = [];
  let releaseA!: (value: string) => void;
  let releaseB!: (value: string) => void;
  let signalBoth!: () => void;
  const bothStarted = new Promise<void>((resolve) => { signalBoth = resolve; });
  const run = (agent: "a" | "b") => async (prompt: string) => {
    started.push(`${agent}:${prompt}`);
    if (started.length === 2) signalBoth();
    return new Promise<string>((resolve) => {
      if (agent === "a") releaseA = resolve;
      else releaseB = resolve;
    });
  };
  const received: MasterEvent[] = [];
  const registry: AgentRegistry = { ...unusedWorkers(), a: run("a"), b: run("b") };
  const result = await createSpawnAgentTool(registry, (event) => received.push(event)).invoke({
    assignments: [{ agent: "a", prompt: "only A" }, { agent: "b", prompt: "only B" }],
  });
  assert.deepEqual(JSON.parse(String(result)), { status: "spawned", agents: ["a", "b"] });
  await bothStarted;
  assert.deepEqual(new Set(started), new Set(["a:only A", "b:only B"]));
  releaseA("A done");
  releaseB("B done");
  // The worker graph reports each completion through a separate callback.
  while (received.length < 2) await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(new Set(received.map((event) => event.type)), new Set(["agent_result"]));
});

test("spawn input rejects repeated workers and blank assignments", () => {
  assert.equal(spawnInputSchema.safeParse({ assignments: [
    { agent: "a", prompt: "one" }, { agent: "a", prompt: "two" },
  ] }).success, false);
  assert.equal(spawnInputSchema.safeParse({ assignments: [
    { agent: "b", prompt: "   " },
  ] }).success, false);
});

test("master can answer directly without spawning workers", async () => {
  let calls = 0;
  const model = {
    bindTools: () => ({ invoke: async () => { calls++; return new AIMessage("direct answer"); } }),
    invoke: async () => { throw new Error("second model call should not happen"); },
  } as unknown as BaseChatModel;
  const master = createMaster(model, unusedWorkers());
  assert.deepEqual(await collect(master.stream("question", "session-a")), [
    { type: "session", session_id: "session-a" },
    { type: "master_answer", answer: "direct answer" },
    { type: "done", session_id: "session-a", answer: "direct answer", spawned_agents: [] },
  ]);
  assert.equal(calls, 1);
});

test("an idle event subscription does not invoke the model", async () => {
  let calls = 0;
  const model = {
    bindTools: () => ({ invoke: async () => { calls++; return new AIMessage("answer"); } }),
    invoke: async () => { calls++; return new AIMessage("answer"); },
  } as unknown as BaseChatModel;
  const master = createMaster(model, unusedWorkers());
  const controller = new AbortController();
  const updates = master.subscribe("idle", 0, controller.signal);
  const next = updates.next();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 0);
  controller.abort();
  assert.equal((await next).done, true);
});

test("spawn success reaches the one follow-up model call; the query ends before the worker", async () => {
  let releaseWorker!: (value: string) => void;
  let signalWorkerStarted!: () => void;
  const workerStarted = new Promise<void>((resolve) => { signalWorkerStarted = resolve; });
  const registry: AgentRegistry = {
    ...unusedWorkers(),
    a: async () => new Promise<string>((resolve) => {
      releaseWorker = resolve;
      signalWorkerStarted();
    }),
  };
  const modelInputs: Array<Array<HumanMessage | AIMessage | ToolMessage>> = [];
  const model = {
    bindTools: () => ({ invoke: async () => new AIMessage({ content: "", tool_calls: [{
      name: "spawn_agent", id: "call_1",
      args: { assignments: [{ agent: "a", prompt: "worker task" }] },
    }] }) }),
    invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) => {
      modelInputs.push(messages);
      return new AIMessage(modelInputs.length === 1 ? "master answer" : "worker follow-up");
    },
  } as unknown as BaseChatModel;
  const master = createMaster(model, registry);
  const first = await collect(master.stream("user question", "session-b"));
  assert.deepEqual(first, [
    { type: "session", session_id: "session-b" },
    { type: "master_answer", answer: "master answer" },
    { type: "done", session_id: "session-b", answer: "master answer", spawned_agents: ["a"] },
  ]);
  assert.equal(modelInputs.length, 1);
  assert.equal(modelInputs[0].at(-1) instanceof ToolMessage, true);
  assert.deepEqual(JSON.parse(String(modelInputs[0].at(-1)?.content)), {
    status: "spawned", agents: ["a"],
  });
  await workerStarted;
  releaseWorker("worker answer");

  const updates = master.subscribe("session-b");
  assert.deepEqual((await updates.next()).value?.event, {
    type: "agent_result",
    result: { agent: "a", prompt: "worker task", output: "worker answer" },
  });
  assert.deepEqual((await updates.next()).value?.event, {
    type: "runtime_answer", answer: "worker follow-up",
  });
  await updates.return();
  assert.equal(modelInputs.length, 2);
  assert.match(String(modelInputs[1].at(-1)?.content), /worker answer/);
});

test("a fast worker still wakes master after query done, and event cursors replay only new events", async () => {
  const model = {
    bindTools: () => ({ invoke: async () => new AIMessage({ content: "", tool_calls: [{
      name: "spawn_agent", id: "call_fast",
      args: { assignments: [{ agent: "a", prompt: "quick task" }] },
    }] }) }),
    invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) =>
      new AIMessage(messages.at(-1) instanceof ToolMessage ? "initial" : "on result"),
  } as unknown as BaseChatModel;
  const master = createMaster(model, { ...unusedWorkers(), a: async () => "quick result" });
  const initial = await collect(master.stream("question", "fast"));
  assert.equal(initial.at(-1)?.type, "done");
  const updates = master.subscribe("fast");
  const first = (await updates.next()).value!;
  assert.equal(first.id, 1);
  assert.equal(first.event.type, "agent_result");
  const second = (await updates.next()).value!;
  assert.equal(second.id, 2);
  assert.deepEqual(second.event, { type: "runtime_answer", answer: "on result" });
  await updates.return();
  const replay = master.subscribe("fast", 1);
  assert.deepEqual((await replay.next()).value, second);
  await replay.return();
});

test("new user messages run while a worker is pending and receive its current status", async () => {
  let releaseWorker!: (value: string) => void;
  let signalWorkerStarted!: () => void;
  const workerStarted = new Promise<void>((resolve) => { signalWorkerStarted = resolve; });
  const contexts: Array<Array<HumanMessage | AIMessage | ToolMessage>> = [];
  let routes = 0;
  const model = {
    bindTools: () => ({ invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) => {
      contexts.push(messages);
      routes++;
      if (routes === 1) return new AIMessage({ content: "", tool_calls: [{
        name: "spawn_agent", id: "call_pending",
        args: { assignments: [{ agent: "a", prompt: "slow task" }] },
      }] });
      return new AIMessage("second answer");
    } }),
    invoke: async () => new AIMessage("answer"),
  } as unknown as BaseChatModel;
  const master = createMaster(model, {
    ...unusedWorkers(),
    a: async () => new Promise<string>((resolve) => {
      releaseWorker = resolve;
      signalWorkerStarted();
    }),
  });
  await collect(master.stream("first", "shared"));
  await workerStarted;
  assert.equal((await collect(master.stream("second", "shared")))[1].type, "master_answer");
  assert.equal(contexts[1].at(-1)?.content, "second\n\nSub agent A is still running");
  releaseWorker("result");
  const updates = master.subscribe("shared");
  assert.equal((await updates.next()).value?.event.type, "agent_result");
  assert.equal((await updates.next()).value?.event.type, "runtime_answer");
  await updates.return();
  await collect(master.stream("third", "shared"));
  assert.equal(contexts[2].at(-1)?.content, "third");
  assert.equal(contexts[2].some((message) => String(message.content).includes("result")), true);
});

test("external runtime events wake master without a user query", async () => {
  const model = {
    bindTools: () => ({ invoke: async () => new AIMessage("user answer") }),
    invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) => {
      assert.match(String(messages.at(-1)?.content), /build_finished/);
      return new AIMessage("runtime answer");
    },
  } as unknown as BaseChatModel;
  const master = createMaster(model, unusedWorkers());
  await collect(master.stream("start", "external"));
  await master.receiveRuntimeEvent("external", {
    type: "runtime_event", name: "build_finished", payload: { ok: true },
  });
  const updates = master.subscribe("external");
  assert.deepEqual((await updates.next()).value?.event, {
    type: "runtime_event", name: "build_finished", payload: { ok: true },
  });
  assert.deepEqual((await updates.next()).value?.event, {
    type: "runtime_answer", answer: "runtime answer",
  });
  await updates.return();
});

test("worker failure is delivered as a runtime event and wakes master", async () => {
  const model = {
    bindTools: () => ({ invoke: async () => new AIMessage({ content: "", tool_calls: [{
      name: "spawn_agent", id: "call_error",
      args: { assignments: [{ agent: "a", prompt: "bad task" }] },
    }] }) }),
    invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) =>
      new AIMessage(messages.at(-1) instanceof ToolMessage ? "started" : "handled failure"),
  } as unknown as BaseChatModel;
  const master = createMaster(model, { ...unusedWorkers(), a: async () => { throw new Error("failed"); } });
  await collect(master.stream("question", "failure"));
  const updates = master.subscribe("failure");
  assert.deepEqual((await updates.next()).value?.event, {
    type: "agent_error", agent: "a", prompt: "bad task", detail: "failed",
  });
  assert.deepEqual((await updates.next()).value?.event, {
    type: "runtime_answer", answer: "handled failure",
  });
  await updates.return();
});
