import assert from "node:assert/strict";
import test from "node:test";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { createMaster, createSpawnAgentTool } from "../src/agents/master.js";
import type { AgentRegistry } from "../src/agents/registry.js";
import { spawnInputSchema } from "../src/types.js";

test("spawn_agent runs selected workers with isolated inputs", async () => {
  const calls: string[] = [];
  const registry: AgentRegistry = {
    a: async (prompt) => { calls.push(`a:${prompt}`); return "A result"; },
    b: async (prompt) => { calls.push(`b:${prompt}`); return "B result"; },
    c: async () => { throw new Error("c should not run"); },
    d: async () => { throw new Error("d should not run"); },
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

test("spawn input rejects repeated workers and blank assignments", () => {
  assert.equal(spawnInputSchema.safeParse({ assignments: [
    { agent: "a", prompt: "one" }, { agent: "a", prompt: "two" },
  ] }).success, false);
  assert.equal(spawnInputSchema.safeParse({ assignments: [
    { agent: "b", prompt: "   " },
  ] }).success, false);
});

test("master routes through spawn_agent and sees worker results before answering", async () => {
  const registry: AgentRegistry = {
    a: async () => "worker answer",
    b: async () => { throw new Error("unexpected b"); },
    c: async () => { throw new Error("unexpected c"); },
    d: async () => { throw new Error("unexpected d"); },
  };
  const model = {
    bindTools: (tools: Array<{ name: string }>, options: { tool_choice: string }) => {
      assert.deepEqual(tools.map((tool) => tool.name), ["spawn_agent"]);
      assert.equal(options.tool_choice, "spawn_agent");
      return { invoke: async (messages: HumanMessage[]) => {
        assert.equal(messages[0].content, "user question");
        return new AIMessage({ content: "", tool_calls: [{
          name: "spawn_agent", id: "call_1",
          args: { assignments: [{ agent: "a", prompt: "worker task" }] },
        }] });
      } };
    },
    invoke: async (messages: Array<HumanMessage | AIMessage | ToolMessage>) => {
      const toolMessage = messages.find((message) => message instanceof ToolMessage);
      assert.ok(toolMessage);
      assert.deepEqual(JSON.parse(String(toolMessage.content)), [
        { agent: "a", prompt: "worker task", output: "worker answer" },
      ]);
      return new AIMessage("final answer");
    },
  } as unknown as BaseChatModel;

  assert.deepEqual(await createMaster(model, registry)("user question"), {
    answer: "final answer",
    agents: [{ agent: "a", prompt: "worker task", output: "worker answer" }],
  });
});
