import assert from "node:assert/strict";
import test from "node:test";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { createMaster, createSpawnAgentTool } from "../src/agents/master.js";
import type { AgentRegistry } from "../src/agents/registry.js";
import { spawnInputSchema } from "../src/types.js";

function unusedWorkers(): AgentRegistry {
  const unexpected = async () => { throw new Error("worker should not run"); };
  return { a: unexpected, b: unexpected, c: unexpected, d: unexpected };
}

test("spawn_agent runs selected workers with isolated inputs", async () => {
  const calls: string[] = [];
  const registry: AgentRegistry = {
    ...unusedWorkers(),
    a: async (prompt) => { calls.push(`a:${prompt}`); return "A result"; },
    b: async (prompt) => { calls.push(`b:${prompt}`); return "B result"; },
  };
  const result = await createSpawnAgentTool(registry).invoke({
    assignments: [{ agent: "a", prompt: "task A" }, { agent: "b", prompt: "task B" }],
  });

  assert.deepEqual(calls, ["a:task A", "b:task B"]);
  assert.deepEqual(JSON.parse(String(result)), [
    { agent: "a", prompt: "task A", output: "A result" },
    { agent: "b", prompt: "task B", output: "B result" },
  ]);
});

test("spawn_agent starts selected workers concurrently", async () => {
  const started: string[] = [];
  let releaseA!: (value: string) => void;
  let releaseB!: (value: string) => void;
  let signalBoth!: () => void;
  const bothStarted = new Promise<void>((resolve) => { signalBoth = resolve; });
  const run = (agent: string) => async (prompt: string) => {
    started.push(`${agent}:${prompt}`);
    if (started.length === 2) signalBoth();
    return new Promise<string>((resolve) => {
      if (agent === "a") releaseA = resolve;
      else releaseB = resolve;
    });
  };
  const registry: AgentRegistry = { ...unusedWorkers(), a: run("a"), b: run("b") };

  const pending = createSpawnAgentTool(registry).invoke({
    assignments: [{ agent: "a", prompt: "only A" }, { agent: "b", prompt: "only B" }],
  });
  await bothStarted;
  assert.deepEqual(started, ["a:only A", "b:only B"]);
  releaseA("A done");
  releaseB("B done");
  assert.equal(JSON.parse(String(await pending)).length, 2);
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
  const model = {
    bindTools: () => ({ invoke: async () => new AIMessage("direct answer") }),
    invoke: async () => { throw new Error("second model call should not happen"); },
  } as unknown as BaseChatModel;
  const events = [];
  for await (const event of createMaster(model, unusedWorkers()).stream("question", "session-a")) {
    events.push(event);
  }
  assert.deepEqual(events, [
    { type: "session", session_id: "session-a" },
    { type: "master_answer", answer: "direct answer" },
    { type: "done", session_id: "session-a", answer: "direct answer", agents: [] },
  ]);
});

test("master answer streams before a background worker finishes", async () => {
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
  const model = {
    bindTools: () => ({ invoke: async () => new AIMessage({ content: "", tool_calls: [{
      name: "spawn_agent", id: "call_1",
      args: { assignments: [{ agent: "a", prompt: "worker task" }] },
    }] }) }),
    invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) => {
      assert.equal(messages.at(-1)?.content, "user question");
      assert.equal(messages.some((message) => message instanceof ToolMessage), false);
      return new AIMessage("master answer");
    },
  } as unknown as BaseChatModel;
  const stream = createMaster(model, registry).stream("user question", "session-b");

  assert.deepEqual((await stream.next()).value, { type: "session", session_id: "session-b" });
  assert.deepEqual((await stream.next()).value, { type: "master_answer", answer: "master answer" });
  await workerStarted;
  releaseWorker("worker answer");
  assert.deepEqual((await stream.next()).value, {
    type: "agent_result",
    result: { agent: "a", prompt: "worker task", output: "worker answer" },
  });
  assert.deepEqual((await stream.next()).value, {
    type: "done", session_id: "session-b", answer: "master answer",
    agents: [{ agent: "a", prompt: "worker task", output: "worker answer" }],
  });
  assert.equal((await stream.next()).done, true);
});

test("worker results are emitted after master answer even if workers finish first", async () => {
  let releaseAnswer!: (value: AIMessage) => void;
  let signalAnswerStarted!: () => void;
  const answerStarted = new Promise<void>((resolve) => { signalAnswerStarted = resolve; });
  const registry: AgentRegistry = { ...unusedWorkers(), a: async () => "quick result" };
  const model = {
    bindTools: () => ({ invoke: async () => new AIMessage({ content: "", tool_calls: [{
      name: "spawn_agent", id: "call_1",
      args: { assignments: [{ agent: "a", prompt: "quick task" }] },
    }] }) }),
    invoke: async () => {
      signalAnswerStarted();
      return new Promise<AIMessage>((resolve) => { releaseAnswer = resolve; });
    },
  } as unknown as BaseChatModel;
  const stream = createMaster(model, registry).stream("question", "session-c");

  assert.equal((await stream.next()).value?.type, "session");
  const nextEvent = stream.next();
  await answerStarted;
  releaseAnswer(new AIMessage("late master answer"));
  assert.deepEqual((await nextEvent).value, { type: "master_answer", answer: "late master answer" });
  assert.equal((await stream.next()).value?.type, "agent_result");
  assert.equal((await stream.next()).value?.type, "done");
});

test("LangGraph runtime starts selected subagents concurrently", async () => {
  const started: string[] = [];
  let releaseA!: (value: string) => void;
  let releaseB!: (value: string) => void;
  let signalBothStarted!: () => void;
  const bothStarted = new Promise<void>((resolve) => { signalBothStarted = resolve; });
  const runner = (agent: "a" | "b") => async (prompt: string) => {
    started.push(`${agent}:${prompt}`);
    if (started.length === 2) signalBothStarted();
    return new Promise<string>((resolve) => {
      if (agent === "a") releaseA = resolve;
      else releaseB = resolve;
    });
  };
  const registry: AgentRegistry = { ...unusedWorkers(), a: runner("a"), b: runner("b") };
  const model = {
    bindTools: () => ({ invoke: async () => new AIMessage({ content: "", tool_calls: [{
      name: "spawn_agent", id: "call_parallel",
      args: { assignments: [
        { agent: "a", prompt: "task A" },
        { agent: "b", prompt: "task B" },
      ] },
    }] }) }),
    invoke: async () => new AIMessage("master continues"),
  } as unknown as BaseChatModel;
  const stream = createMaster(model, registry).stream("question", "parallel");
  assert.equal((await stream.next()).value?.type, "session");
  assert.deepEqual((await stream.next()).value, { type: "master_answer", answer: "master continues" });
  await bothStarted;
  assert.deepEqual(new Set(started), new Set(["a:task A", "b:task B"]));
  releaseA("A done");
  releaseB("B done");
  const remaining = [];
  for await (const event of stream) remaining.push(event);
  assert.equal(remaining.filter((event) => event.type === "agent_result").length, 2);
  assert.equal(remaining.at(-1)?.type, "done");
});

test("master remembers returned worker results by session; workers see only assigned tasks", async () => {
  const routingInputs: Array<Array<HumanMessage | AIMessage | ToolMessage>> = [];
  const workerInputs: string[] = [];
  let calls = 0;
  const registry: AgentRegistry = {
    ...unusedWorkers(),
    a: async (prompt) => { workerInputs.push(prompt); return `result for ${prompt}`; },
  };
  const model = {
    bindTools: () => ({ invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) => {
      routingInputs.push(messages);
      calls += 1;
      return new AIMessage({ content: "", tool_calls: [{
        name: "spawn_agent", id: `call_${calls}`,
        args: { assignments: [{ agent: "a", prompt: `task ${calls}` }] },
      }] });
    } }),
    invoke: async () => new AIMessage(`answer ${calls}`),
  } as unknown as BaseChatModel;
  const master = createMaster(model, registry);
  for await (const _event of master.stream("first", "same")) { /* consume */ }
  for await (const _event of master.stream("second", "same")) { /* consume */ }
  for await (const _event of master.stream("separate", "other")) { /* consume */ }

  assert.equal(routingInputs[0].length, 1);
  assert.equal(routingInputs[1].some((message) => message instanceof ToolMessage), true);
  assert.equal(routingInputs[1].filter((message) => message instanceof HumanMessage).length, 2);
  assert.equal(routingInputs[2].length, 1);
  assert.deepEqual(workerInputs, ["task 1", "task 2", "task 3"]);
});

test("same-session master handles a new query while an earlier worker runs", async () => {
  let releaseWorker!: (value: string) => void;
  let signalWorkerStarted!: () => void;
  const workerStarted = new Promise<void>((resolve) => { signalWorkerStarted = resolve; });
  let routes = 0;
  const routingInputs: Array<Array<HumanMessage | AIMessage | ToolMessage>> = [];
  const registry: AgentRegistry = {
    ...unusedWorkers(),
    a: async () => new Promise<string>((resolve) => {
      releaseWorker = resolve;
      signalWorkerStarted();
    }),
  };
  const model = {
    bindTools: () => ({ invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) => {
      routingInputs.push(messages);
      routes += 1;
      if (routes === 1) return new AIMessage({ content: "", tool_calls: [{
        name: "spawn_agent", id: "call_1",
        args: { assignments: [{ agent: "a", prompt: "first task" }] },
      }] });
      return new AIMessage("second answer");
    } }),
    invoke: async () => new AIMessage("first answer"),
  } as unknown as BaseChatModel;
  const master = createMaster(model, registry);
  const first = master.stream("first", "shared");
  await first.next();
  assert.equal((await first.next()).value?.type, "master_answer");
  await workerStarted;

  const second = master.stream("second", "shared");
  assert.equal((await second.next()).value?.type, "session");
  assert.deepEqual((await second.next()).value, { type: "master_answer", answer: "second answer" });
  assert.equal((await second.next()).value?.type, "done");
  assert.equal(routes, 2);
  assert.equal(routingInputs[1].some((message) => message instanceof ToolMessage), false);

  releaseWorker("first result");
  for await (const _event of first) { /* consume */ }

  const third = master.stream("third", "shared");
  for await (const _event of third) { /* consume */ }
  assert.equal(routingInputs[2].some((message) => message instanceof ToolMessage), true);
});

test("new query context ends with the agents still running in that session", async () => {
  let releaseA!: (value: string) => void;
  let releaseD!: (value: string) => void;
  let signalBothStarted!: () => void;
  let started = 0;
  const bothStarted = new Promise<void>((resolve) => { signalBothStarted = resolve; });
  const worker = (agent: "a" | "d") => async () => {
    started += 1;
    if (started === 2) signalBothStarted();
    return new Promise<string>((resolve) => {
      if (agent === "a") releaseA = resolve;
      else releaseD = resolve;
    });
  };
  const registry: AgentRegistry = { ...unusedWorkers(), a: worker("a"), d: worker("d") };
  const contexts: Array<Array<HumanMessage | AIMessage | ToolMessage>> = [];
  let routes = 0;
  const model = {
    bindTools: () => ({ invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) => {
      contexts.push(messages);
      routes += 1;
      if (routes === 1) return new AIMessage({ content: "", tool_calls: [{
        name: "spawn_agent", id: "call_ad",
        args: { assignments: [
          { agent: "a", prompt: "task A" },
          { agent: "d", prompt: "task D" },
        ] },
      }] });
      return new AIMessage(`answer ${routes}`);
    } }),
    invoke: async () => new AIMessage("first answer"),
  } as unknown as BaseChatModel;
  const master = createMaster(model, registry);
  const first = master.stream("first", "shared");
  assert.equal((await first.next()).value?.type, "session");
  assert.equal((await first.next()).value?.type, "master_answer");
  await bothStarted;

  for await (const _event of master.stream("second", "shared")) { /* consume */ }
  assert.equal(contexts[1].at(-1)?.content,
    "second\n\nSub agents A and D are still running");

  releaseA("A done");
  assert.equal((await first.next()).value?.type, "agent_result");
  for await (const _event of master.stream("third", "shared")) { /* consume */ }
  assert.equal(contexts[2].at(-1)?.content,
    "third\n\nSub agent D is still running");

  releaseD("D done");
  for await (const _event of first) { /* consume */ }
  for await (const _event of master.stream("fourth", "shared")) { /* consume */ }
  assert.equal(contexts[3].at(-1)?.content, "fourth");
});
