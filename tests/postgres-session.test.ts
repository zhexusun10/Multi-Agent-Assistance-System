import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";
import { AIMessage, type HumanMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { PostgresSaver } from "@langchain/langgraph-checkpoint-postgres";
import pg from "pg";
import { createMaster } from "../src/agents/master.js";
import { createAgentRegistry } from "../src/agents/registry.js";
import { PostgresAgentTaskStore } from "../src/agent-task-store.js";
import type { AgentRegistry } from "../src/agents/registry.js";
import { PostgresSessionEventStore } from "../src/session-store.js";

test("PostgreSQL retains conversation and event cursors across master instances", {
  skip: !process.env.TEST_DATABASE_URL,
}, async () => {
  const pool = new pg.Pool({ connectionString: process.env.TEST_DATABASE_URL });
  try {
    const checkpointer = new PostgresSaver(pool);
    const events = new PostgresSessionEventStore(pool);
    const tasks = new PostgresAgentTaskStore(pool);
    await checkpointer.setup();
    await events.setup();
    await tasks.setup();
    const sessionId = randomUUID();
    const unexpected = async () => { throw new Error("Worker should not run"); };
    const registry: AgentRegistry = { a: unexpected, b: unexpected, c: unexpected, d: unexpected };
    let finishRuntime!: (response: AIMessage) => void;
    const runtimeResponse = new Promise<AIMessage>((resolve) => { finishRuntime = resolve; });
    const firstModel = {
      bindTools: () => ({ invoke: async () => new AIMessage("first answer") }),
      invoke: async () => runtimeResponse,
    } as unknown as BaseChatModel;
    const firstMaster = createMaster(firstModel, registry, { checkpointer, events, tasks });
    for await (const _ of firstMaster.stream("first question", sessionId)) { /* consume */ }
    await firstMaster.acceptRuntimeEvent(sessionId, {
      type: "runtime_event", name: "finished", payload: { ok: true },
    });
    assert.equal((await events.list(sessionId, 0)).length, 1);
    finishRuntime(new AIMessage("runtime answer"));
    const completed = firstMaster.subscribe(sessionId, 1);
    assert.equal((await completed.next()).value?.id, 2);
    await completed.return();

    let secondInput: HumanMessage[] = [];
    const secondModel = {
      bindTools: () => ({ invoke: async (messages: HumanMessage[]) => {
        secondInput = messages;
        return new AIMessage("second answer");
      } }),
      invoke: async () => new AIMessage("runtime answer"),
    } as unknown as BaseChatModel;
    const secondMaster = createMaster(secondModel, registry, { checkpointer, events, tasks });
    const replay = secondMaster.subscribe(sessionId);
    assert.deepEqual((await replay.next()).value, {
      id: 1,
      event: { type: "runtime_event", name: "finished", payload: { ok: true } },
    });
    assert.deepEqual((await replay.next()).value, {
      id: 2, event: { type: "runtime_answer", answer: "runtime answer" },
    });
    await replay.return();
    for await (const _ of secondMaster.stream("second question", sessionId)) { /* consume */ }
    assert.equal(secondInput.some((message) => message.content === "first question"), true);
    assert.equal(secondInput.some((message) => message.content === "first answer"), true);
    assert.equal(secondInput.some((message) => String(message.content).includes("finished")), true);
    const cursorReplay = secondMaster.subscribe(sessionId, 1);
    assert.equal((await cursorReplay.next()).value?.id, 2);
    await cursorReplay.return();
  } finally {
    await pool.end();
  }
});

test("session agents retain isolated context and running tasks resume", {
  skip: !process.env.TEST_DATABASE_URL,
}, async () => {
  const pool = new pg.Pool({ connectionString: process.env.TEST_DATABASE_URL });
  try {
    const checkpointer = new PostgresSaver(pool);
    const events = new PostgresSessionEventStore(pool);
    const tasks = new PostgresAgentTaskStore(pool);
    await checkpointer.setup();
    await events.setup();
    await tasks.setup();
    const sessionId = randomUUID();
    const otherSession = randomUUID();
    const workerInputs: string[][] = [];
    const workerModel = {
      invoke: async (messages: Array<{ content: unknown }>) => {
        workerInputs.push(messages.map((message) => String(message.content)));
        return new AIMessage(`worker-answer-${workerInputs.length}`);
      },
    } as unknown as BaseChatModel;
    const firstRegistry = createAgentRegistry(workerModel, checkpointer);
    const firstTaskId = randomUUID();
    assert.equal(await firstRegistry.a("A first", sessionId, firstTaskId), "worker-answer-1");
    assert.equal(await firstRegistry.a("A first", sessionId, firstTaskId), "worker-answer-1");
    assert.equal(workerInputs.length, 1);
    await firstRegistry.b("B first", sessionId, randomUUID());

    // New compiled graphs use the same PostgreSQL thread for each session/agent pair.
    const secondRegistry = createAgentRegistry(workerModel, checkpointer);
    await secondRegistry.a("A second", sessionId, randomUUID());
    assert.deepEqual(workerInputs[2], ["A first", "worker-answer-1", "A second"]);
    await secondRegistry.a("A other session", otherSession, randomUUID());
    assert.deepEqual(workerInputs[3], ["A other session"]);
    assert.deepEqual(workerInputs[1], ["B first"]);

    const [pending] = await tasks.enqueue(sessionId, [{ agent: "a", prompt: "A after restart" }]);
    assert.equal((await tasks.claimNext(sessionId, "a"))?.id, pending.id);
    let runtimeCalls = 0;
    const masterModel = {
      bindTools: () => ({ invoke: async () => new AIMessage("direct") }),
      invoke: async () => { runtimeCalls++; return new AIMessage("received"); },
    } as unknown as BaseChatModel;
    const resumed = createMaster(masterModel, secondRegistry, { checkpointer, events, tasks });
    await resumed.resumePendingTasks();
    const updates = resumed.subscribe(sessionId);
    assert.deepEqual((await updates.next()).value?.event, {
      type: "agent_result",
      result: { agent: "a", prompt: "A after restart", output: "worker-answer-5" },
    });
    assert.deepEqual((await updates.next()).value?.event, { type: "runtime_answer", answer: "received" });
    await updates.return();
    assert.deepEqual(workerInputs[4], [
      "A first", "worker-answer-1", "A second", "worker-answer-3", "A after restart",
    ]);
    await resumed.waitForTaskIdle();
    assert.deepEqual(await tasks.activeAgents(sessionId), []);
    await pool.query("UPDATE agent_tasks SET delivered_at = NULL WHERE id = $1", [pending.id]);
    const retried = createMaster(masterModel, secondRegistry, { checkpointer, events, tasks });
    await retried.resumePendingTasks();
    await retried.waitForTaskIdle();
    assert.equal(runtimeCalls, 1);
    assert.equal((await events.list(sessionId, 0)).length, 2);
  } finally {
    await pool.end();
  }
});
