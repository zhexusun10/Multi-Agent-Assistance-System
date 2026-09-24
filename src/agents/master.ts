import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { tool } from "@langchain/core/tools";
import { Annotation, END, START, StateGraph } from "@langchain/langgraph";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import type { AgentRegistry } from "./registry.js";
import { agentResultsSchema, spawnInputSchema, type AgentResult, type QueryResult } from "../types.js";

const MasterState = Annotation.Root({
  query: Annotation<string>(),
  messages: Annotation<Array<HumanMessage | AIMessage | ToolMessage>>({
    reducer: (left, right) => left.concat(right),
    default: () => [],
  }),
  answer: Annotation<string>(),
});

export function createSpawnAgentTool(registry: AgentRegistry) {
  return tool(
    async ({ assignments }) => {
      const results: AgentResult[] = await Promise.all(
        assignments.map(async ({ agent, prompt }) => {
          const output = await registry[agent](prompt);
          return { agent, prompt, output };
        }),
      );
      return JSON.stringify(results);
    },
    {
      name: "spawn_agent",
      description: "Dispatch one or more tasks to agents a, b, c, or d, with a separate prompt for each selected agent.",
      schema: spawnInputSchema,
    },
  );
}

export function createMaster(model: BaseChatModel, registry: AgentRegistry) {
  if (!model.bindTools) {
    throw new Error("The master model must support tool calling");
  }
  const spawnAgent = createSpawnAgentTool(registry);
  const toolNode = new ToolNode([spawnAgent]);
  const routingModel = model.bindTools([spawnAgent], { tool_choice: "spawn_agent" });

  const graph = new StateGraph(MasterState)
    .addNode("master", async (state) => {
      const response = await routingModel.invoke([new HumanMessage(state.query)]);
      if (!(response instanceof AIMessage) || response.tool_calls?.length !== 1 || response.tool_calls[0].name !== "spawn_agent") {
        throw new Error("master must call spawn_agent exactly once");
      }
      spawnInputSchema.parse(response.tool_calls[0].args);
      return { messages: [new HumanMessage(state.query), response] };
    })
    .addNode("spawn", toolNode)
    .addNode("finish", async (state) => {
      const response = await model.invoke(state.messages);
      return { answer: response.text, messages: [response] };
    })
    .addEdge(START, "master")
    .addEdge("master", "spawn")
    .addEdge("spawn", "finish")
    .addEdge("finish", END)
    .compile();

  return async (query: string): Promise<QueryResult> => {
    const result = await graph.invoke({ query });
    const agents = result.messages
      .filter((message): message is ToolMessage => message instanceof ToolMessage)
      .flatMap((message) => agentResultsSchema.parse(JSON.parse(String(message.content))) as AgentResult[]);
    return { answer: result.answer, agents };
  };
}
